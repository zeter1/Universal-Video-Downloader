**Язык / Language:** [Русский](README.md) · **English**

# Universal Video Downloader

**Universal Video Downloader** is a Python/Tkinter Windows application for downloading video from sites supported by `yt-dlp`. It is designed around a convenient final artifact: video is delivered as MP4 at up to **1080p**, while audio can be saved as MP3.

The project tries to avoid unnecessary transcoding. It first selects suitable MP4/M4A streams, uses fast lossless remuxing when needed, and falls back to H.264/AAC transcoding only when a compatible MP4 cannot be produced more directly.

## Download and run on Windows

> Windows builds are marked **Prerelease**: they are preliminary builds for testing. A release build may differ from the current source.

1. Open [GitHub Releases](https://github.com/zeter1/Universal-Video-Downloader/releases) and expand **Assets** for the build you choose.
2. Download and run `Universal-Video-Downloader.exe`.
3. Paste a video URL, choose video or MP3, and select an output folder.

Python, `yt-dlp`, FFmpeg/ffprobe, and Deno are included in the EXE. Internet access is required; individual sites depend on their restrictions and `yt-dlp` support. A SHA-256 file is published beside the binary. Offline and packaged self-tests do not establish live website availability.

> The EXE is not commercially code-signed; Windows SmartScreen may warn about a new or rarely downloaded file.

## What the project demonstrates

- integration of `yt-dlp`, FFmpeg, and ffprobe into one user workflow;
- media-processing strategy focused on quality and avoiding unnecessary transcoding;
- validation of the final file after download and stream assembly;
- fallback from compatible source streams to remux and only then to transcoding;
- modular structure for a growing desktop application;
- diagnostic artifacts for failures that depend on external sites and media tools;
- a dedicated code map and tooling for finding the minimum change scope during AI-assisted development.

## Features

- video downloading through `yt-dlp`;
- quality up to 1080p;
- MP4 output;
- audio download and MP3 conversion;
- FFmpeg / ffprobe processing and result validation;
- lossless remux preferred where possible;
- H.264/AAC fallback when a compatible MP4 cannot be produced more directly;
- modular architecture;
- diagnostic logs;
- tools that help Codex navigate the codebase efficiently.

## Installation from source

1. Install a current Python version for Windows.
2. Install FFmpeg and ffprobe and add them to the system `PATH`.
3. Download the project with **Code → Download ZIP** or Git:

```bash
git clone https://github.com/zeter1/Universal-Video-Downloader.git
cd Universal-Video-Downloader
```

4. Install Python dependencies:

```powershell
python -m pip install -r requirements.txt
```

## Launch from source

```powershell
python video_downloader.py
```

## Usage

1. Start the application.
2. Copy a video URL from a supported site.
3. Paste it into the application.
4. Select video or MP3 mode.
5. For video, choose an available quality up to 1080p.
6. Select the output folder.
7. Start the download.
8. After downloading, the application combines streams through FFmpeg when necessary and validates the resulting media file.

When source streams are already MP4-compatible, the application tries to avoid re-encoding them. This is faster and avoids an additional quality loss.

## How the final MP4 is produced

```text
Compatible MP4/M4A streams
        ↓
Download through yt-dlp
        ↓
Lossless MP4 remux when necessary
        ↓
H.264/AAC transcoding only as a fallback
        ↓
Result validation through ffprobe
```

## Requirements

- Windows;
- Python;
- `yt-dlp`;
- FFmpeg;
- ffprobe.

Python dependencies are listed in [`requirements.txt`](requirements.txt).

## Architecture and development

The application is split into modules so a focused downloader change does not require reading the whole codebase.

See:

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — architecture and quality rules;
- [`docs/CODE_MAP.md`](docs/CODE_MAP.md) — project code map.

To identify a minimal code scope before making a change:

```bash
python scripts/codex_scope.py "task description"
```

To check project structure and AI-assisted development efficiency:

```bash
python scripts/check_codex_efficiency.py
```

## Verification

CI compiles the Python sources and runs the full offline regression-test suite. The same baseline can be run locally:

```powershell
python -m compileall -q video_downloader.py problem_log_validator.py src scripts tests
python -m unittest discover -s tests -v
```

These checks do not replace a real download from a specific site: site availability and format behavior depend on external services and the current `yt-dlp` version.

## Support and security

- [`SUPPORT.md`](SUPPORT.md) — useful data for a bug report;
- [GitHub Issues](https://github.com/zeter1/Universal-Video-Downloader/issues) — reproducible bugs and regular support issues;
- [`SECURITY.md`](SECURITY.md) — reporting potential security vulnerabilities.

## Important limitation

Support for individual sites depends on `yt-dlp` and may change when sites update their APIs, protection mechanisms, or media formats. When a download breaks, update `yt-dlp` first and inspect the application's diagnostics.

## License

The source code is published as a portfolio project and for implementation review. Use of third-party services and downloading content must comply with the relevant site's terms and applicable law.

## Developer contacts

**Язык / Language:** [Русский](README.md#контакты-для-связи-с-разработчиком) · **English**

- [zeter11@gmail.com](mailto:zeter11@gmail.com)
- [https://t.me/zeter1](https://t.me/zeter1)
- [https://www.linkedin.com/in/zeter/](https://www.linkedin.com/in/zeter/)
- [https://www.facebook.com/zeter1](https://www.facebook.com/zeter1)
- [https://vk.ru/zeter1](https://vk.ru/zeter1)
- [https://www.instagram.com/zeter1992/](https://www.instagram.com/zeter1992/)
- [https://tenchat.ru/zeter](https://tenchat.ru/zeter)
- [https://x.com/zeter1](https://x.com/zeter1)
- [https://www.threads.com/@zeter1992](https://www.threads.com/@zeter1992)
- [https://ok.ru/zeter1](https://ok.ru/zeter1)
- [https://t.me/zeterchat](https://t.me/zeterchat) — Telegram group: about me, projects and chat
