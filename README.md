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

## Usage

Install dependencies:

```bash
pip install -r requirements.txt
```

Download everything from a profile:

```bash
python main.py -tm username
```

Videos only:

```bash
python main.py -tv username
```

Photos only:

```bash
python main.py -tp username
```

Filter by date:

```bash
python main.py -tm username --date 2023
python main.py -tm username --date 2023-10
python main.py -tm username --date 2023-12-25
python main.py -tm username --date 2021:2023
python main.py -tm username --date 2023-01:2023-06
python main.py -tm username --date 2024-12-22:2025-01-01
python main.py -tm username --date 2023-03-15:2024
```

The `--date` filter can be combined with any mass download argument.
You can use it with `-tv` to download only videos or `-tp` for
only photos within a specific date or range.

Output lands in `downloads/<username>/`.

Download a single direct link:

```bash
python main.py -dl "https://www.tiktok.com/@username/video/123456789"
```

Download Live Stream:

```bash
python main.py -tl username
```

Output lands in `downloads/live/<username>/`.

## Configuration

Tuning constants live in `config.toml`, which is auto-generated
in the root directory on the first run. Edit this file directly
to change scraper and download limits.

| Section / Key             | Default   | Meaning                                             |
| ------------------------- | --------- | --------------------------------------------------- |
| `min_video_height`        | `1080`    | Min resolution (px). Skips & purges sub-par videos. |
| `max_video_duration`      | `16`      | Max video length (s). Skips longer clips.           |
| `scroll_wait_ms`          | `5000`    | Delay (ms) to allow profile grid lazy-loading.      |
| `max_stale_scrolls`       | `10`      | Max empty scrolls before ending profile scrape.     |
| `manual_wait_rounds`      | `60`      | Polling attempts during manual captcha/login block. |
| `manual_wait_interval_ms` | `5000`    | Polling interval (ms). Total wait = 5 mins.         |
| `musicaldown_max_retries` | `3`       | Max retries to extract URLs from MusicalDown.       |
| `min_file_size`           | `"100KB"` | Min valid file size (avoids 0-byte error pages).    |
| `max_file_size`           | `"30MB"`  | Max video file size. Aborts if exceeded.            |
| `timeout`                 | `60`      | Request timeout (s) per download.                   |
| `chunk_size`              | `65536`   | Streaming chunk size (bytes).                       |
| `max_retries`             | `3`       | Max download retries on failure.                    |
| `executable`              | (Auto)    | Custom browser path (e.g. `C:\...\chrome.exe`).     |

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
