(() => {
  "use strict";

  const state = {
    kind: null,
    title: null,
    videos: [],
    selectedIds: new Set(),
    pollTimer: null,
  };

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => [...document.querySelectorAll(sel)];

  const queryInput = $("#queryInput");
  const resolveBtn = $("#resolveBtn");
  const resultsSection = $("#resultsSection");
  const resultsTitle = $("#resultsTitle");
  const videoList = $("#videoList");
  const multiActions = $("#multiActions");
  const optionsSection = $("#optionsSection");
  const downloadBtn = $("#downloadBtn");
  const selectionCount = $("#selectionCount");
  const subtitleList = $("#subtitleList");
  const audioList = $("#audioList");
  const jobList = $("#jobList");
  const emptyQueue = $("#emptyQueue");
  const toastEl = $("#toast");

  function toast(message, type = "") {
    toastEl.textContent = message;
    toastEl.className = `toast ${type}`.trim();
    clearTimeout(toastEl._t);
    toastEl._t = setTimeout(() => toastEl.classList.add("hidden"), 4200);
  }

  function formatDuration(seconds) {
    if (seconds == null || Number.isNaN(seconds)) return "";
    const s = Math.max(0, Math.floor(seconds));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    if (h > 0) {
      return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
    }
    return `${m}:${String(sec).padStart(2, "0")}`;
  }

  function selectedMode() {
    return $$('input[name="mode"]:checked')[0]?.value || "video";
  }

  function updateModeVisibility() {
    const mode = selectedMode();
    $("#videoOpts").classList.toggle("hidden", mode === "audio" || mode === "subtitles");
    $("#audioOpts").classList.toggle("hidden", mode === "subtitles");
    $("#audioFormatLabel").classList.toggle("hidden", mode !== "audio");
    $("#embedAudioRow")?.classList.toggle("hidden", mode !== "video");
    $("#embedSubs").closest("label").classList.toggle("hidden", mode !== "video");
    const hint = $("#audioHint");
    if (hint) {
      hint.textContent =
        mode === "audio"
          ? "Só áudio: cada idioma seleccionado gera um ficheiro separado."
          : "Vídeo: seleccione idiomas e active «Embutir áudios» para várias pistas no ficheiro.";
    }
    if (mode === "subtitles") {
      $("#writeSubs").checked = true;
      $("#embedSubs").checked = false;
    }
    if (mode === "video" && $("#embedAudio") && !$("#embedAudio").dataset.userTouched) {
      $("#embedAudio").checked = true;
    }
  }

  function updateDownloadButton() {
    const n = state.selectedIds.size;
    downloadBtn.disabled = n === 0;
    selectionCount.textContent = n
      ? `${n} vídeo${n === 1 ? "" : "s"} seleccionado${n === 1 ? "" : "s"}`
      : "";
  }

  function renderSubtitles(subs) {
    if (!subs || !subs.length) {
      subtitleList.innerHTML =
        '<p class="muted">Sem legendas disponíveis para este vídeo.</p>';
      return;
    }
    subtitleList.innerHTML = subs
      .map(
        (s) => `
      <label>
        <input type="checkbox" name="subLang" value="${escapeAttr(s.code)}" />
        <span>${escapeHtml(s.name)}</span>
      </label>`
      )
      .join("");
  }

  function renderAudioLanguages(langs) {
    const selected = new Set(
      $$('input[name="audioLang"]:checked').map((el) => el.value)
    );
    if (!langs || !langs.length) {
      audioList.innerHTML =
        '<p class="muted">Sem pistas de áudio multi-idioma detectadas. Será usado o áudio predefinido.</p>';
      $("#audioSelectActions")?.classList.add("hidden");
      return;
    }
    $("#audioSelectActions")?.classList.remove("hidden");
    audioList.innerHTML = langs
      .map(
        (l) => `
      <label>
        <input type="checkbox" name="audioLang" value="${escapeAttr(l.code)}"
          ${selected.has(l.code) ? "checked" : ""} />
        <span>${escapeHtml(l.name)}</span>
      </label>`
      )
      .join("");
  }

  function escapeHtml(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function escapeAttr(str) {
    return escapeHtml(str).replace(/'/g, "&#39;");
  }

  function renderVideos() {
    videoList.innerHTML = "";
    state.videos.forEach((v) => {
      const id = v.id || v.webpage_url;
      const item = document.createElement("label");
      item.className = "video-item" + (state.selectedIds.has(id) ? " selected" : "");
      item.innerHTML = `
        <input type="checkbox" ${state.selectedIds.has(id) ? "checked" : ""} data-id="${escapeAttr(id)}" />
        <img src="${escapeAttr(v.thumbnail || "")}" alt="" loading="lazy"
             onerror="this.style.visibility='hidden'" />
        <div class="meta">
          <div class="title" title="${escapeAttr(v.title)}">${escapeHtml(v.title)}</div>
          <div class="sub">${escapeHtml(v.uploader || "")}${
            v.duration != null ? " · " + formatDuration(v.duration) : ""
          }</div>
        </div>`;
      const checkbox = item.querySelector("input");
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) state.selectedIds.add(id);
        else state.selectedIds.delete(id);
        item.classList.toggle("selected", checkbox.checked);
        updateDownloadButton();
      });
      videoList.appendChild(item);
    });
  }

  async function resolveQuery() {
    const query = queryInput.value.trim();
    if (!query) {
      toast("Indique um URL ou termo de pesquisa.", "error");
      return;
    }

    resolveBtn.disabled = true;
    resolveBtn.textContent = "A analisar…";
    try {
      const res = await fetch("/api/resolve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Falha ao analisar.");

      state.kind = data.kind;
      state.title = data.title;
      state.videos = data.videos || [];
      state.selectedIds = new Set();

      resultsTitle.textContent = data.title || "Resultados";
      resultsSection.classList.remove("hidden");
      optionsSection.classList.remove("hidden");

      const multi = state.videos.length > 1;
      multiActions.classList.toggle("hidden", !multi);

      if (!multi && state.videos[0]) {
        const v = state.videos[0];
        state.selectedIds.add(v.id || v.webpage_url);
        renderSubtitles(v.subtitles);
        renderAudioLanguages(v.audio_languages);
      } else {
        // Pré-seleccionar todos excepto pesquisas
        if (data.kind !== "search") {
          state.videos.forEach((v) => state.selectedIds.add(v.id || v.webpage_url));
        }
        subtitleList.innerHTML =
          '<p class="muted">Em lotes, as legendas usam os códigos que seleccionar abaixo. Pode enriquecer o primeiro vídeo seleccionado.</p>';
        // Tentar enriquecer o primeiro para popular idiomas
        const first = state.videos[0];
        if (first?.webpage_url) {
          enrichFirst(first.webpage_url);
        } else {
          renderSubtitles([]);
          renderAudioLanguages([]);
        }
      }

      renderVideos();
      updateDownloadButton();
      updateModeVisibility();
    } catch (err) {
      toast(err.message || String(err), "error");
    } finally {
      resolveBtn.disabled = false;
      resolveBtn.textContent = "Analisar";
    }
  }

  async function enrichFirst(url) {
    try {
      const res = await fetch("/api/enrich", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      const data = await res.json();
      if (!res.ok) return;
      renderSubtitles(data.subtitles);
      renderAudioLanguages(data.audio_languages);
    } catch {
      /* silencioso — lote continua sem lista rica */
    }
  }

  function gatherOptions() {
    const subtitleLangs = $$('input[name="subLang"]:checked').map((el) => el.value);
    const audioLangs = $$('input[name="audioLang"]:checked').map((el) => el.value);
    return {
      mode: selectedMode(),
      container: $("#container").value,
      quality: $("#quality").value,
      audio_format: $("#audioFormat").value,
      audio_langs: audioLangs,
      embed_audio: $("#embedAudio")?.checked ?? true,
      subtitle_langs: subtitleLangs,
      subtitle_format: $("#subtitleFormat").value,
      write_subs: $("#writeSubs").checked,
      embed_subs: $("#embedSubs").checked,
    };
  }

  function selectedVideos() {
    return state.videos.filter((v) => state.selectedIds.has(v.id || v.webpage_url));
  }

  async function startDownload() {
    const videos = selectedVideos();
    if (!videos.length) {
      toast("Seleccione pelo menos um vídeo.", "error");
      return;
    }

    const options = gatherOptions();
    if (options.mode === "subtitles" && !options.subtitle_langs.length) {
      toast("Seleccione pelo menos um idioma de legendas.", "error");
      return;
    }
    if (
      options.mode === "video" &&
      (options.write_subs || options.embed_subs) &&
      !options.subtitle_langs.length
    ) {
      toast("Seleccione idiomas de legendas ou desmarque as opções de legendas.", "error");
      return;
    }

    downloadBtn.disabled = true;
    downloadBtn.textContent = "A enfileirar…";
    try {
      const res = await fetch("/api/download", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          options,
          items: videos.map((v) => ({
            url: v.webpage_url,
            title: v.title,
            thumbnail: v.thumbnail,
          })),
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Falha ao enfileirar.");
      toast(`${data.jobs.length} download(s) na fila.`, "ok");
      await refreshJobs();
      startPolling();
    } catch (err) {
      toast(err.message || String(err), "error");
    } finally {
      downloadBtn.textContent = "Descarregar";
      updateDownloadButton();
    }
  }

  function renderJobs(jobs) {
    if (!jobs.length) {
      jobList.innerHTML = "";
      jobList.appendChild(emptyQueue);
      emptyQueue.classList.remove("hidden");
      return;
    }
    emptyQueue.classList.add("hidden");
    jobList.innerHTML = jobs
      .map((j) => {
        const canCancel = j.status === "queued" || j.status === "running";
        const canDownload = j.has_file;
        const err = j.error ? `<div class="sub" style="color:var(--err)">${escapeHtml(j.error)}</div>` : "";
        return `
        <div class="job-item" data-id="${escapeAttr(j.id)}">
          <img src="${escapeAttr(j.thumbnail || "")}" alt=""
               onerror="this.style.visibility='hidden'" />
          <div class="job-main">
            <div class="job-title" title="${escapeAttr(j.title)}">${escapeHtml(j.title)}</div>
            <div class="job-status ${escapeAttr(j.status)}">
              ${escapeHtml(j.status_label)}${j.message ? " · " + escapeHtml(j.message) : ""}
              ${j.progress && j.status === "running" ? ` (${j.progress}%)` : ""}
            </div>
            ${err}
            <div class="progress"><span style="width:${j.progress || 0}%"></span></div>
          </div>
          <div class="job-actions">
            ${
              canDownload
                ? `<a class="btn btn-sm btn-primary" href="/api/files/${j.id}">Guardar</a>`
                : ""
            }
            ${
              canCancel
                ? `<button type="button" class="btn btn-sm btn-ghost" data-action="cancel">Cancelar</button>`
                : ""
            }
            <button type="button" class="btn btn-sm btn-danger" data-action="remove">Remover</button>
          </div>
        </div>`;
      })
      .join("");

    jobList.querySelectorAll("[data-action]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const id = btn.closest(".job-item").dataset.id;
        const action = btn.dataset.action;
        try {
          if (action === "cancel") {
            await fetch(`/api/jobs/${id}/cancel`, { method: "POST" });
          } else if (action === "remove") {
            await fetch(`/api/jobs/${id}`, { method: "DELETE" });
          }
          await refreshJobs();
        } catch (err) {
          toast(err.message || String(err), "error");
        }
      });
    });
  }

  async function refreshJobs() {
    try {
      const res = await fetch("/api/jobs");
      const data = await res.json();
      const jobs = data.jobs || [];
      renderJobs(jobs);
      const busy = jobs.some(
        (j) => j.status === "queued" || j.status === "running"
      );
      if (busy) {
        startPolling();
      } else if (state.pollTimer) {
        clearInterval(state.pollTimer);
        state.pollTimer = null;
      }
    } catch {
      /* ignore polling errors */
    }
  }

  function startPolling() {
    if (state.pollTimer) return;
    state.pollTimer = setInterval(refreshJobs, 1000);
  }

  // Eventos
  resolveBtn.addEventListener("click", resolveQuery);
  queryInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      resolveQuery();
    }
  });

  $$('input[name="mode"]').forEach((el) =>
    el.addEventListener("change", updateModeVisibility)
  );

  $("#selectAllBtn").addEventListener("click", () => {
    state.videos.forEach((v) => state.selectedIds.add(v.id || v.webpage_url));
    renderVideos();
    updateDownloadButton();
  });

  $("#selectNoneBtn").addEventListener("click", () => {
    state.selectedIds.clear();
    renderVideos();
    updateDownloadButton();
  });

  $("#audioSelectAllBtn")?.addEventListener("click", () => {
    $$('input[name="audioLang"]').forEach((el) => {
      el.checked = true;
    });
    if ($("#embedAudio") && selectedMode() === "video") {
      $("#embedAudio").checked = true;
    }
  });

  $("#audioSelectNoneBtn")?.addEventListener("click", () => {
    $$('input[name="audioLang"]').forEach((el) => {
      el.checked = false;
    });
  });

  $("#embedAudio")?.addEventListener("change", () => {
    $("#embedAudio").dataset.userTouched = "1";
  });

  downloadBtn.addEventListener("click", startDownload);

  $("#clearFinishedBtn").addEventListener("click", async () => {
    await fetch("/api/jobs/clear-finished", { method: "POST" });
    await refreshJobs();
  });

  updateModeVisibility();

  // Restaurar fila após F5 — se houver jobs a decorrer, o refreshJobs() retoma o polling
  refreshJobs();

  // Quando o separador volta a ficar visível, sincronizar de imediato
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      refreshJobs();
    }
  });

  window.addEventListener("pageshow", () => {
    refreshJobs();
  });
})();
