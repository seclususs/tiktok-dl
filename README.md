# ttdl - TikTok Downloader

A utility to download TikTok videos, photos, and live streams.

- **Profile Mass-Downloader**: Download all posts from a specific username.
  Includes built-in filters (by date, duration, and video quality) and ensures
  the downloaded files keep their original upload timestamps.
- **Direct Link Downloader**: Download video or photo directly from its TikTok URL.
- **Profile Live-Downloader**: Capture and save ongoing TikTok live broadcasts
  directly to your device, complete with real-time download statistics.

## Prerequisites

- Python 3.11 or newer
- FFmpeg and FFprobe available on `PATH`
- Microsoft Edge or Google Chrome

## Installation

Install via pip:

```bash
pip install .
```

Or for development (editable mode):

```bash
pip install -e .
```

## Usage

Download everything from a profile:

```bash
ttdl batch username
```

Videos only:

```bash
ttdl video username
```

Photos only:

```bash
ttdl photo username
```

Filter by date:

```bash
ttdl batch username --date 2023
ttdl batch username --date 2023-10
ttdl batch username --date 2023-12-25
ttdl batch username --date 2021:2023
ttdl batch username --date 2023-01:2023-06
ttdl batch username --date 2024-12-22:2025-01-01
ttdl batch username --date 2023-03-15:2024
```

The `--date` filter can be combined with `batch`, `video`, or `photo`.

Scrape and save JSON cache only (no media download):

```bash
ttdl batch username --scrape
```

Download directly from existing JSON cache (skip browser scraping):

```bash
ttdl batch username --cached
```

Force full profile scrape:

```bash
ttdl batch username --force
```

Download a single direct link or a text file of URLs:

```bash
ttdl url "https://www.tiktok.com/@username/video/123456789"
ttdl url urls.txt
```

Output lands in the standard Downloads folder:

- **Windows**: `%USERPROFILE%\Downloads\ttdl\<username>\`
- **Linux / macOS**: `~/Downloads/ttdl/<username>/`
- **Termux (Android)**: `/storage/emulated/0/Download/ttdl/<username>/`

Download Live Stream:

```bash
ttdl live username
```

Output lands in the standard Downloads folder:

- **Windows**: `%USERPROFILE%\Downloads\ttdl\live\<username>\`
- **Linux / macOS**: `~/Downloads/ttdl/live/<username>/`
- **Termux (Android)**: `/storage/emulated/0/Download/ttdl/live/<username>/`

## Configuration

Tuning constants live in `config.toml` (located in your user configuration directory):

- **Windows**: `%APPDATA%\ttdl\config.toml`
- **Linux / macOS**: `~/.config/ttdl/config.toml`
- **Termux (Android)**: `~/.config/ttdl/config.toml`

Manage configuration directly from the CLI:

```bash
# Open config in your default text editor
ttdl config --edit

# Print path to active config file
ttdl config --path

# Display current configuration
ttdl config --show

# Get or set a specific configuration value
ttdl config get max_video_duration
ttdl config set max_video_duration 60

# Reset configuration to default template
ttdl config --reset
```

| Section / Key             | Default            | Meaning                                             |
| ------------------------- | ------------------ | --------------------------------------------------- |
| `min_video_height`        | `1080`             | Min resolution (px). Skips & purges sub-par videos. |
| `max_video_duration`      | `0`                | Max video length (s). `0` for unlimited.            |
| `scroll_wait_ms`          | `5000`             | Delay (ms) to allow profile grid lazy-loading.      |
| `max_stale_scrolls`       | `10`               | Max empty scrolls before ending profile scrape.     |
| `manual_wait_rounds`      | `60`               | Polling attempts during manual captcha/login block. |
| `manual_wait_interval_ms` | `5000`             | Polling interval (ms). Total wait = 5 mins.         |
| `musicaldown_max_retries` | `3`                | Max retries to extract URLs from MusicalDown.       |
| `min_file_size`           | `"100KB"`          | Min valid file size (avoids 0-byte error pages).    |
| `max_file_size`           | `"30MB"`           | Max video file size. Aborts if exceeded.            |
| `timeout`                 | `60`               | Request timeout (s) per download.                   |
| `chunk_size`              | `65536`            | Streaming chunk size (bytes).                       |
| `max_retries`             | `3`                | Max download retries on failure.                    |
| `download_dir`            | (Platform default) | Custom path for downloads                           |
| `executable`              | (Auto)             | Custom browser path (e.g. `C:\...\chrome.exe`).     |

## Cache Management

Clean temporary media chunks, old logs, and browser sessions:

```bash
# Clean temporary files, logs, and stale browser sessions
ttdl clean

# Clean everything including cached profile metadata JSON files
ttdl clean --all

# Clean only profile metadata JSON cache
ttdl clean --json
```

## Disclaimer - Read Before Use

The script drives a locally installed Microsoft Edge or Google Chrome
directly, it does not download its own Chromium build.

This software is provided "as is", without warranty of any kind,
express or implied, including but not limited to warranties of
merchantability, fitness for a particular purpose, and non-infringement.
Nothing in this repository constitutes legal advice.

**No liability, full stop.** By downloading, cloning, forking,
or executing this code, you accept complete and exclusive responsibility
for every outcome that follows - IP bans, account suspensions, rate-limiting,
service disruption, data loss, legal exposure, or any other direct, indirect,
incidental, or consequential damage. In no event will the author be liable for
any claim, damages, or other liability, whether in an action of contract, tort,
or otherwise, arising from the use of this software. This is DWYOR: Do What You Own Risk.
The author is not on the hook for what you do with it.

**No affiliation.** This project has no affiliation with, endorsement from,
or connection to TikTok, ByteDance, or any third-party endpoint it interacts with.
All trademarks and content belong to their respective owners.

**Educational purposes only.** This is a Proof of Concept for DOM analysis and browser
automation, published for research into how modern web apps structure and gate content.
It is not built, tested, or maintained as production scraping infrastructure, and ships
with zero guarantees of correctness, uptime, or continued functionality.

**Terms of Service.** Running this script violates the Terms of Service of TikTok and
of the third-party download endpoint it depends on to resolve media URLs. Both explicitly
prohibit this kind of automated access. That violation belongs to whoever runs the script,
not to whoever wrote it.

**Copyright and ethics.** Downloaded content remains the property of its original creator.
This tool is scoped to local, personal archiving only - no re-uploading, redistribution,
public rehosting, or commercial use in any form. Infringing on a creator's rights with this
tool is a decision you make, not one the tool makes for you.

**Compliance is on you.** Laws governing scraping, automated access, and data collection
vary by jurisdiction and change over time. Confirming your use case is legal where you live,
and where the target account is based, is entirely your responsibility.
If you need a real answer, consult a lawyer, not this file.

**Technical limitations.** Aggressive scraping trips TikTok's rate limits, CAPTCHAs,
or HTTP 403 blocks. When that happens the script pauses and waits for manual browser
intervention - solving the CAPTCHA or logging back in - before continuing.
That's expected behavior, not a bug.

Using this software means you have read this section and agree to it in full.
If you don't agree, don't run the code.
