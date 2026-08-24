# YoutubePlaylistDownloader

[![Licença: MIT](https://img.shields.io/badge/Licen%C3%A7a-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Flask](https://img.shields.io/badge/Flask-3.x-black.svg)](https://flask.palletsprojects.com/)
[![yt-dlp](https://img.shields.io/badge/motor-yt--dlp-red.svg)](https://github.com/yt-dlp/yt-dlp)

Aplicação web para descarregar **vídeos**, **áudio** e **legendas** do YouTube,
com interface em português (tema escuro).

> **Origem / reescrita:** inspirado nas funcionalidades do projeto desktop
> **[Tyrrrz/YoutubeDownloader](https://github.com/Tyrrrz/YoutubeDownloader)** (Avalonia / C# / YoutubeExplode).
>
> Esta versão usa **Python (Flask) + HTML + CSS + JavaScript** e o motor **[yt-dlp](https://github.com/yt-dlp/yt-dlp)**,
> com textos da interface em português.

---

## Índice

- [Funcionalidades](#funcionalidades)
- [Stack tecnológica](#stack-tecnológica)
- [Requisitos](#requisitos)
- [Instalação](#instalação)
- [Como executar](#como-executar)
- [Utilização](#utilização)
- [Opções de download](#opções-de-download)
- [API HTTP](#api-http)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Configuração](#configuração)
- [Fila e persistência](#fila-e-persistência)
- [Metadados embutidos](#metadados-embutidos)
- [Diferenças face ao projeto original](#diferenças-face-ao-projeto-original)
- [Atualizar o yt-dlp](#atualizar-o-yt-dlp)
- [Resolução de problemas](#resolução-de-problemas)
- [Contribuir](#contribuir)
- [Licença](#licença)
- [Aviso legal](#aviso-legal)
- [Agradecimentos](#agradecimentos)

---

## Funcionalidades

- Interface web em português (tema escuro, responsiva)
- Análise de:
  - URL de **vídeo**
  - **playlist**
  - **canal**
  - **pesquisa** por texto (prefixo `?` força pesquisa)
- Modos de download:
  - **Vídeo** (contentores `mp4`, `webm`, `mkv`)
  - **Só áudio** (`mp3`, `m4a`, `ogg`)
  - **Só legendas** (`.srt` / `.vtt`)
- Qualidade de vídeo: melhor, ≤1080p, ≤720p, ≤480p, ≤360p, pior
- **Legendas** por idioma: guardar ficheiros separados e/ou embutir no vídeo
- **Multi-áudios**: seleccionar vários idiomas e embuti-los no mesmo ficheiro de vídeo
- **Metadados** automáticos (título, canal, data, descrição, URL, capa, capítulos)
- Fila com progresso, cancelamento, remoção e download do ficheiro (ou ZIP)
- Persistência da fila: sobrevive a F5; jobs interrompidos são retomados após reinício do servidor

---

## Stack tecnológica

| Camada | Tecnologia |
|--------|------------|
| Backend | Python 3.10+, Flask |
| Frontend | HTML5, CSS3, JavaScript (vanilla) |
| Motor de download | [yt-dlp](https://github.com/yt-dlp/yt-dlp) |
| Processamento media | [FFmpeg](https://ffmpeg.org/) |
| Fila | threads locais + `downloads/jobs_state.json` |

---

## Requisitos

### Obrigatórios

- **Python 3.10** ou superior
- **FFmpeg** (e tipicamente **ffprobe**) instalados e disponíveis no `PATH`

### Ubuntu / Debian

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip ffmpeg
```

### Windows

1. Instale Python a partir de [python.org](https://www.python.org/downloads/)
2. Instale FFmpeg (por exemplo via [winget](https://winget.run/pkg/Gyan.FFmpeg) ou [ffmpeg.org](https://ffmpeg.org/download.html)) e adicione-o ao `PATH`

### macOS

```bash
brew install python ffmpeg
```

---

## Instalação

```bash
git clone https://github.com/Claudio-Candido/YoutubePlaylistDownloader.git
cd YoutubePlaylistDownloader

python3 -m venv .venv

# Linux / macOS
source .venv/bin/activate

# Windows (PowerShell)
# .venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

---

## Como executar

```bash
source .venv/bin/activate   # se ainda não estiver activo
python app.py
```

Abra no browser: [http://127.0.0.1:5000](http://127.0.0.1:5000)

Os ficheiros descarregados ficam em `downloads/<job_id>/`.

Para parar o servidor: `Ctrl+C` no terminal.

---

## Utilização

1. Cole um **URL** do YouTube (vídeo, playlist ou canal) **ou** um termo de pesquisa.
2. Para forçar pesquisa, use o prefixo `?`, por exemplo: `?never gonna give you up`.
3. Clique **Analisar**.
4. Seleccione o(s) vídeo(s) e configure as opções (modo, qualidade, áudios, legendas).
5. Clique **Descarregar** e acompanhe a **Fila de downloads**.
6. Quando o estado for **Concluído**, clique **Guardar** para obter o ficheiro (ou ZIP se houver vários artefactos).

Atalho: `Ctrl+Enter` (ou `Cmd+Enter` no macOS) no campo de pesquisa executa **Analisar**.

---

## Opções de download

### Modo

| Modo | Descrição |
|------|-----------|
| Vídeo | Vídeo + áudio num contentor (`mp4` / `webm` / `mkv`) |
| Só áudio | Extrai trilha(s) de áudio (`mp3` / `m4a` / `ogg`) |
| Só legendas | Apenas ficheiros de legendas |

### Áudio

- Lista de idiomas disponíveis (quando o vídeo tiver multi-áudio)
- **Embutir áudios seleccionados no vídeo** — várias pistas no mesmo ficheiro (recomendado com `mkv` ou `mp4`)
- Em modo **só áudio**, cada idioma gera um ficheiro separado

### Legendas

- Selecção multi-idioma (manuais e automáticas)
- **Guardar ficheiros separados** (`.srt` / `.vtt`)
- **Embutir no vídeo**
- Dica: muitos idiomas de uma vez podem falhar por limite do YouTube (HTTP 429); prefira 2–4

---

## API HTTP

Base: `http://127.0.0.1:5000`

| Método | Rota | Descrição |
|--------|------|-----------|
| `GET` | `/` | Interface web |
| `GET` | `/api/health` | Estado do serviço e presença do FFmpeg |
| `POST` | `/api/resolve` | Analisa URL ou pesquisa → lista de vídeos + legendas/áudios |
| `POST` | `/api/enrich` | Detalhes (legendas/áudios) de um URL de vídeo |
| `POST` | `/api/download` | Enfileira um ou mais downloads |
| `GET` | `/api/jobs` | Lista a fila |
| `GET` | `/api/jobs/<id>` | Estado de um job |
| `POST` | `/api/jobs/<id>/cancel` | Cancela |
| `DELETE` | `/api/jobs/<id>` | Remove da fila (e ficheiros) |
| `GET` | `/api/files/<id>` | Descarrega o artefacto (ficheiro ou ZIP) |
| `POST` | `/api/jobs/clear-finished` | Limpa jobs concluídos / erro / cancelados |

### Exemplo: analisar

```bash
curl -s -X POST http://127.0.0.1:5000/api/resolve \
  -H 'Content-Type: application/json' \
  -d '{"query":"https://www.youtube.com/watch?v=jNQXAC9IVRw"}'
```

### Exemplo: enfileirar vídeo mkv com legendas

```bash
curl -s -X POST http://127.0.0.1:5000/api/download \
  -H 'Content-Type: application/json' \
  -d '{
    "url": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
    "title": "Me at the zoo",
    "options": {
      "mode": "video",
      "container": "mkv",
      "quality": "720",
      "embed_audio": true,
      "audio_langs": ["en"],
      "write_subs": true,
      "embed_subs": true,
      "subtitle_langs": ["en", "pt"],
      "subtitle_format": "srt"
    }
  }'
```

### Corpo típico de `options`

```json
{
  "mode": "video | audio | subtitles",
  "container": "mp4 | webm | mkv",
  "quality": "highest | 1080 | 720 | 480 | 360 | lowest",
  "audio_format": "mp3 | m4a | ogg",
  "audio_langs": ["pt", "en-US"],
  "embed_audio": true,
  "subtitle_langs": ["pt", "en"],
  "subtitle_format": "srt | vtt",
  "write_subs": true,
  "embed_subs": false
}
```

---

## Estrutura do projeto

```
YoutubePlaylistDownloader/
├── app.py                 # Entrada Flask e rotas da API
├── config.py              # Constantes (contentores, qualidades, pastas)
├── requirements.txt       # Dependências Python
├── LICENSE
├── README.md
├── .gitignore
├── downloads/             # Saída dos jobs (ignorada no git, excepto .gitkeep)
│   └── .gitkeep
├── services/
│   ├── resolver.py        # Resolve URL / playlist / canal / pesquisa
│   ├── downloader.py      # yt-dlp + FFmpeg (vídeo, áudio, legendas, metadados)
│   └── job_manager.py     # Fila, progresso, cancelamento, persistência
├── static/
│   ├── css/style.css
│   └── js/app.js
└── templates/
    └── index.html
```

---

## Configuração

Principais constantes em `config.py`:

| Variável | Significado | Predefinição |
|----------|-------------|--------------|
| `DOWNLOADS_DIR` | Pasta de saída | `downloads/` |
| `PARALLEL_LIMIT` | Downloads em paralelo | `2` |
| `VIDEO_CONTAINERS` | Contentores de vídeo | `mp4`, `webm`, `mkv` |
| `AUDIO_FORMATS` | Formatos só-áudio | `mp3`, `m4a`, `ogg` |
| `QUALITY_HEIGHT` | Limites de altura | ver ficheiro |

O servidor de desenvolvimento escuta em `127.0.0.1:5000` (`app.py`).

---

## Fila e persistência

- Estado da fila: `downloads/jobs_state.json`
- Progresso gravado periodicamente (~1 s)
- **Actualizar a página (F5):** a UI volta a ler `/api/jobs` e retoma o polling se houver jobs activos
- **Reinício do processo Flask:** jobs que estavam `queued` / `running` são recolocados na fila («A retomar…»)

---

## Metadados embutidos

Em downloads de vídeo e áudio, o ficheiro recebe (quando o contentor permite):

| Campo | Conteúdo típico |
|-------|-----------------|
| `title` | Título do vídeo |
| `artist` / `album_artist` | Canal / uploader |
| `album` | Canal |
| `date` | Data de publicação |
| `description` / `synopsis` | Descrição |
| `comment` | Origem + URL + canal + ID |
| `genre` | `YouTube` |
| `encoding_tool` | YoutubePlaylistDownloader (Flask + yt-dlp) |
| Capa | Thumbnail |
| Capítulos | Se existirem no vídeo |

---

## Diferenças face ao projeto original

| Aspecto | Tyrrrz/YoutubeDownloader | Este projecto |
|---------|--------------------------|---------------|
| UI | Desktop Avalonia | Web Flask |
| Motor | YoutubeExplode | yt-dlp |
| Login / cookies YouTube | Sim | Não (v1) |
| Tags MusicBrainz | Sim | Não |
| Selecção de idiomas de legendas | Embute todas | Escolha por idioma |
| Multi-áudios seleccionáveis | Preferência global | Selecção explícita + embutir |
| Contentor MKV | Não (na UI típica) | Sim |
| Fila persistente entre F5 | N/A (app desktop) | Sim |

---

## Atualizar o yt-dlp

O YouTube muda com frequência. Mantenha o motor actualizado:

```bash
source .venv/bin/activate
pip install -U yt-dlp
```

---

## Resolução de problemas

| Sintoma | O que fazer |
|---------|-------------|
| «FFmpeg não encontrado» | Instale o FFmpeg e confirme com `ffmpeg -version` |
| HTTP 429 nas legendas | Espere ~1 minuto; seleccione menos idiomas |
| Só 1 pista de áudio | Active «Embutir áudios» e escolha ≥2 idiomas; `mkv` costuma ser mais fiável |
| Vídeo privado / idade | Não suportado nesta versão (sem cookies) |
| Erro estranho do yt-dlp | Actualize: `pip install -U yt-dlp` |
| Fila vazia após crash | Verifique `downloads/jobs_state.json`; jobs incompletos devem retomar no próximo arranque |

---

## Contribuir

1. Faça fork do repositório
2. Crie um ramo (`git checkout -b funcionalidade/nova-opcao`)
3. Commit com mensagem clara
4. Abra um Pull Request

Sugestões e issues são bem-vindas.

---

## Licença

Distribuído sob licença **MIT**. Ver [LICENSE](LICENSE).

O código C# original de Tyrrrz/YoutubeDownloader tem a sua própria licença e termos; esta reescrita web é um projecto independente.

---

## Aviso legal

Esta ferramenta destina-se a uso pessoal e educacional. Respeite os
[Termos de Serviço do YouTube](https://www.youtube.com/t/terms) e os direitos
de autor dos conteúdos. O autor não se responsabiliza pelo uso indevido.

---

## Agradecimentos

- [Tyrrrz/YoutubeDownloader](https://github.com/Tyrrrz/YoutubeDownloader) — referência funcional da app desktop
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — motor de extracção e download
- [FFmpeg](https://ffmpeg.org/) — merge, legendas, áudio e metadados
- [Flask](https://flask.palletsprojects.com/) — framework web

<!-- maintainer: Claudio-Candido -->
