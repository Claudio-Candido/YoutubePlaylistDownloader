"""Fila de downloads em background com progresso, cancelamento e persistência."""

from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from config import DOWNLOADS_DIR, PARALLEL_LIMIT
from services.downloader import (
    DownloadCancelled,
    download,
    pack_artifacts,
    primary_artifact,
)

STATE_FILE = DOWNLOADS_DIR / "jobs_state.json"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


STATUS_LABELS = {
    JobStatus.QUEUED: "À espera",
    JobStatus.RUNNING: "A descarregar",
    JobStatus.COMPLETED: "Concluído",
    JobStatus.FAILED: "Erro",
    JobStatus.CANCELLED: "Cancelado",
}


@dataclass
class Job:
    id: str
    url: str
    title: str
    thumbnail: str | None
    options: dict[str, Any]
    status: JobStatus = JobStatus.QUEUED
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    artifacts: list[str] = field(default_factory=list)
    download_name: str | None = None
    cancel_flag: bool = field(default=False, repr=False)

    @property
    def out_dir(self) -> Path:
        return DOWNLOADS_DIR / self.id

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "thumbnail": self.thumbnail,
            "status": self.status.value,
            "status_label": STATUS_LABELS[self.status],
            "progress": round(self.progress, 1),
            "message": self.message,
            "error": self.error,
            "created_at": self.created_at,
            "artifacts": [Path(a).name for a in self.artifacts],
            "has_file": self.status == JobStatus.COMPLETED and bool(self.artifacts),
            "options": {
                k: v
                for k, v in self.options.items()
                if k
                in {
                    "mode",
                    "container",
                    "quality",
                    "audio_format",
                    "audio_language",
                    "audio_langs",
                    "embed_audio",
                    "subtitle_langs",
                    "subtitle_format",
                    "write_subs",
                    "embed_subs",
                }
            },
        }

    def to_persist(self) -> dict[str, Any]:
        """Estado completo para gravar em disco."""
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "thumbnail": self.thumbnail,
            "options": self.options,
            "status": self.status.value,
            "progress": self.progress,
            "message": self.message,
            "error": self.error,
            "created_at": self.created_at,
            "artifacts": list(self.artifacts),
            "download_name": self.download_name,
        }

    @classmethod
    def from_persist(cls, data: dict[str, Any]) -> Job:
        status_raw = data.get("status") or JobStatus.QUEUED.value
        try:
            status = JobStatus(status_raw)
        except ValueError:
            status = JobStatus.FAILED
        return cls(
            id=data["id"],
            url=data.get("url") or "",
            title=data.get("title") or "Vídeo",
            thumbnail=data.get("thumbnail"),
            options=data.get("options") or {},
            status=status,
            progress=float(data.get("progress") or 0),
            message=data.get("message") or "",
            error=data.get("error"),
            created_at=data.get("created_at")
            or datetime.now(timezone.utc).isoformat(),
            artifacts=list(data.get("artifacts") or []),
            download_name=data.get("download_name"),
        )


class JobManager:
    def __init__(self, max_workers: int = PARALLEL_LIMIT) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._last_persist = 0.0
        self._load_state()

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(
                self._jobs.values(),
                key=lambda j: j.created_at,
                reverse=True,
            )
            return [j.to_dict() for j in jobs]

    def get_job(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def enqueue(
        self,
        *,
        url: str,
        title: str,
        thumbnail: str | None,
        options: dict[str, Any],
        job_id: str | None = None,
        resume_progress: float = 0.0,
        resume_message: str = "Na fila…",
    ) -> dict[str, Any]:
        job = Job(
            id=job_id or uuid.uuid4().hex[:12],
            url=url,
            title=title or "Vídeo",
            thumbnail=thumbnail,
            options=options,
            message=resume_message,
            progress=resume_progress,
            status=JobStatus.QUEUED,
        )
        with self._lock:
            self._jobs[job.id] = job
            self._persist_unlocked(force=True)
        self._executor.submit(self._run_job, job.id)
        return job.to_dict()

    def enqueue_many(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self.enqueue(**item) for item in items]

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            if job.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
                return job.to_dict()
            job.cancel_flag = True
            if job.status == JobStatus.QUEUED:
                job.status = JobStatus.CANCELLED
                job.message = "Cancelado"
                self._persist_unlocked(force=True)
            return job.to_dict()

    def remove(self, job_id: str, delete_files: bool = True) -> bool:
        with self._lock:
            job = self._jobs.pop(job_id, None)
            if job:
                self._persist_unlocked(force=True)
        if not job:
            return False
        job.cancel_flag = True
        if delete_files and job.out_dir.exists():
            shutil.rmtree(job.out_dir, ignore_errors=True)
        return True

    def file_path_for_download(self, job_id: str) -> tuple[Path, str] | None:
        """Devolve (caminho, nome_download). ZIP se vários artefactos."""
        job = self.get_job(job_id)
        if not job or job.status != JobStatus.COMPLETED or not job.artifacts:
            return None

        paths = [Path(a) for a in job.artifacts if Path(a).exists()]
        if not paths:
            # Tentar descobrir ficheiros na pasta do job
            if job.out_dir.exists():
                paths = [
                    p
                    for p in job.out_dir.iterdir()
                    if p.is_file()
                    and p.suffix.lower() not in {".part", ".ytdl", ".temp", ".json"}
                    and p.name != "jobs_state.json"
                ]
            if not paths:
                return None

        if len(paths) == 1:
            path = paths[0]
            return path, path.name

        zip_path = job.out_dir / f"{job.id}.zip"
        if not zip_path.exists():
            pack_artifacts(paths, zip_path)
        safe_title = "".join(c if c.isalnum() or c in " ._-" else "_" for c in job.title)[
            :80
        ].strip() or job.id
        return zip_path, f"{safe_title}.zip"

    def _run_job(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.cancel_flag:
                if job:
                    job.status = JobStatus.CANCELLED
                    job.message = "Cancelado"
                    self._persist_unlocked(force=True)
                return
            job.status = JobStatus.RUNNING
            job.message = job.message if job.progress > 0 else "A iniciar…"
            self._persist_unlocked(force=True)

        try:
            job.out_dir.mkdir(parents=True, exist_ok=True)

            def on_progress(pct: float, msg: str) -> None:
                with self._lock:
                    if job.cancel_flag:
                        return
                    job.progress = pct
                    job.message = msg
                    # Persistir periodicamente para a UI recuperar após F5
                    self._persist_unlocked(force=False)

            artifacts = download(
                job.url,
                job.out_dir,
                job.options,
                on_progress=on_progress,
                is_cancelled=lambda: job.cancel_flag,
            )

            with self._lock:
                if job.cancel_flag:
                    job.status = JobStatus.CANCELLED
                    job.message = "Cancelado"
                    self._persist_unlocked(force=True)
                    return
                job.artifacts = [str(p) for p in artifacts]
                job.download_name = primary_artifact(artifacts).name
                job.status = JobStatus.COMPLETED
                job.progress = 100.0
                msg = (job.message or "").strip()
                if (
                    not msg
                    or msg.startswith("A ")
                    or "%" in msg
                    or "ETA" in msg
                    or msg in {"Na fila…", "A iniciar…", "Erro"}
                ):
                    job.message = "Concluído"
                self._persist_unlocked(force=True)

        except DownloadCancelled:
            with self._lock:
                job.status = JobStatus.CANCELLED
                job.message = "Cancelado"
                job.progress = 0.0
                self._persist_unlocked(force=True)
            if job.out_dir.exists():
                shutil.rmtree(job.out_dir, ignore_errors=True)

        except Exception as exc:  # noqa: BLE001 — reportar qualquer falha ao UI
            with self._lock:
                job.status = JobStatus.FAILED
                job.error = str(exc)
                job.message = "Erro"
                self._persist_unlocked(force=True)
            if job.out_dir.exists():
                for p in job.out_dir.iterdir():
                    if p.suffix.lower() in {".part", ".ytdl", ".temp"}:
                        p.unlink(missing_ok=True)

    def _persist_unlocked(self, *, force: bool) -> None:
        """Grava jobs_state.json. Com force=False, no máximo ~1×/s."""
        now = time.monotonic()
        if not force and (now - self._last_persist) < 1.0:
            return
        self._last_persist = now
        try:
            DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "jobs": [j.to_persist() for j in self._jobs.values()],
            }
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(STATE_FILE)
        except OSError:
            pass

    def _load_state(self) -> None:
        if not STATE_FILE.exists():
            return
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        raw_jobs = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(raw_jobs, list):
            return

        to_resume: list[str] = []
        with self._lock:
            for item in raw_jobs:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                job = Job.from_persist(item)
                # Validar artefactos concluídos ainda existem
                if job.status == JobStatus.COMPLETED:
                    existing = [a for a in job.artifacts if Path(a).exists()]
                    if not existing and job.out_dir.exists():
                        existing = [
                            str(p)
                            for p in job.out_dir.iterdir()
                            if p.is_file()
                            and p.suffix.lower()
                            not in {".part", ".ytdl", ".temp", ".json"}
                        ]
                        job.artifacts = existing
                    if not existing:
                        job.status = JobStatus.FAILED
                        job.error = "Ficheiro já não está disponível."
                        job.message = "Erro"
                elif job.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
                    # Continuar o download após reinício / F5 no servidor
                    job.status = JobStatus.QUEUED
                    job.cancel_flag = False
                    job.message = "A retomar…"
                    to_resume.append(job.id)
                self._jobs[job.id] = job
            self._persist_unlocked(force=True)

        for job_id in to_resume:
            self._executor.submit(self._run_job, job_id)


# Instância global da aplicação
manager = JobManager()
