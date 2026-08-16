# Universal Video Downloader

Windows desktop GUI for downloading and processing online video with **yt-dlp**, **FFmpeg/FFprobe**, VPN/proxy-aware retry strategies and recovery-oriented diagnostics.

Current application version in the source: **5.8 VPN SMART**.

## Features

- Windows GUI built with Python and Tkinter.
- Download individual videos and video lists/playlists.
- Multiple yt-dlp fallback strategies for unstable networks and YouTube CDN failures.
- VPN/proxy support with masked credentials in diagnostic output.
- Browser-cookie support for content that requires an authenticated browser session.
- Configurable concurrent downloads.
- Watchdog/heartbeat diagnostics for long-running or stalled yt-dlp/FFmpeg processes.
- Adaptive retry handling for timeouts, SSL/network failures and temporary CDN problems.
- Detection and skipping of persistently slow/problematic downloads so one item does not block the entire queue.
- Separate list/folder for items that need manual processing later.
- Download history and duplicate detection.
- Validation of downloaded media with FFprobe.
- Video-to-MP3 conversion.
- Audio merging and splitting of long audio files.
- Structured problem logs with retention limits, compression and secret redaction.
- Separate `problem_log_validator.py` utility for validating diagnostic-log structure.

## Requirements

- Windows 10/11.
- Python 3.x with Tkinter.
- `yt-dlp` available through Python/pip or as an executable accessible to the application.
- FFmpeg and FFprobe available in `PATH` or otherwise accessible to the program.

Optional external tools may improve specific download scenarios:

- `aria2c` for supported download strategies.
- Node.js or Deno for yt-dlp scenarios that require an external JavaScript runtime.

## Installation

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
```

Install FFmpeg separately and make sure both commands work:

```powershell
ffmpeg -version
ffprobe -version
```

## Run

```powershell
py video_downloader.py
```

## Updating yt-dlp

The repository includes a Windows batch helper for updating yt-dlp. You can also update the Python package manually:

```powershell
py -m pip install --upgrade yt-dlp
```

## Runtime data

The application creates user/runtime folders next to the program, including settings, download history, download logs, problem logs, generated link lists and files that require manual processing. These folders are excluded from Git through `.gitignore`.

Typical runtime directories include:

- `Настройки/`
- `Логи скачивания/`
- `Логи проблем/`
- `История ссылок/`
- `Ссылки на скачивания/`
- `Обработать вручную/`

## Diagnostics and reliability

The program is designed for long and unreliable network operations. It keeps bounded logs, records stable error signatures, masks cookies/proxy credentials and other authentication values, tracks yt-dlp attempts and process state, and keeps enough context to investigate failures with ChatGPT/Codex without storing unlimited repetitive output.

The diagnostic format can be checked with:

```powershell
py problem_log_validator.py
```

## Responsible use

Use this application only to download content that you are authorized to access and in accordance with the source website's terms and applicable law. The project does not provide DRM circumvention.

## Notes

Online video platforms change frequently, so keeping `yt-dlp` up to date is important. Some sites may also require browser cookies, a working VPN/proxy route, or an external JavaScript runtime depending on the site's current behavior.
