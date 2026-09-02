# ttdl - TikTok Downloader

A utility to download TikTok videos, photos, and live streams.

- **Profile Mass-Downloader**: Download all posts from a specific username. Includes built-in filters (by date, duration, and video quality) and ensures the downloaded files keep their original upload timestamps.
- **Profile Live-Downloader**: Capture and save ongoing TikTok live broadcasts directly to your device, complete with real-time download statistics.

## Prerequisites

- Python 3.10 or newer
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

The --date filter can be combined with any mass download argument. You can use it with -tv to download only videos or -tp for only photos within a specific date or range.

Output lands in `downloads/<username>/`.

Download Live Stream:

```bash
python main.py -tl username
```

Output lands in `downloads/live/<username>/`.

## Configuration

Tuning constants live in `ttdl/constants.py`. There are no CLI flags for these - edit the source directly if you need different behavior.

| Constant                  | Default      | Meaning                                                                                                                                                                    |
| ------------------------- | ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `MIN_VIDEO_HEIGHT`        | `1080`       | Minimum vertical resolution (px) a video must have. Anything shorter is skipped on download, and existing files below this are purged from the output folder on every run. |
| `MAX_VIDEO_DURATION`      | `18`         | Maximum clip length in seconds. Longer videos are skipped entirely.                                                                                                        |
| `SCROLL_WAIT_MS`          | `5000`       | Delay in ms after each scroll-to-bottom, giving the profile grid time to lazy-load the next batch of posts.                                                                |
| `MAX_STALE_SCROLLS`       | `10`         | Consecutive scrolls returning zero new posts before the collector assumes it has reached the end of the profile and stops.                                                 |
| `MANUAL_WAIT_ROUNDS`      | `60`         | Polling rounds the script waits for manual intervention when blocked.                                                                                                      |
| `MANUAL_WAIT_INTERVAL_MS` | `5000`       | Delay in ms between polling rounds during manual intervention. `MANUAL_WAIT_ROUNDS * MANUAL_WAIT_INTERVAL_MS` gives the 5-minute wait ceiling.                             |
| `MUSICALDOWN_MAX_RETRIES` | `3`          | Attempts to submit a post URL to the download-resolver endpoint before giving up on that post.                                                                             |
| `MIN_FILE_SIZE_BYTES`     | `100_000`    | Minimum size in bytes a downloaded file must reach to count as a real video instead of an error page or empty response.                                                    |
| `MAX_FILE_SIZE_BYTES`     | `30_000_000` | Size ceiling in bytes for a single video. Downloads exceeding it are aborted mid-stream.                                                                                   |
| `DOWNLOAD_TIMEOUT`        | `60`         | HTTP request timeout in seconds per download attempt.                                                                                                                      |
| `DOWNLOAD_CHUNK_SIZE`     | `65536`      | Chunk size in bytes used while streaming a download to disk.                                                                                                               |
| `DOWNLOAD_MAX_RETRIES`    | `3`          | Retry attempts for a failed download before the file is marked failed.                                                                                                     |

## Disclaimer - Read Before Use

The script drives a locally installed Microsoft Edge or Google Chrome directly, it does not download its own Chromium build.

This software is provided "as is", without warranty of any kind, express or implied, including but not limited to warranties of merchantability, fitness for a particular purpose, and non-infringement. Nothing in this repository constitutes legal advice.

**No liability, full stop.** By downloading, cloning, forking, or executing this code, you accept complete and exclusive responsibility for every outcome that follows - IP bans, account suspensions, rate-limiting, service disruption, data loss, legal exposure, or any other direct, indirect, incidental, or consequential damage. In no event will the author be liable for any claim, damages, or other liability, whether in an action of contract, tort, or otherwise, arising from the use of this software. This is DWYOR: Do What You Own Risk. The author is not on the hook for what you do with it.

**No affiliation.** This project has no affiliation with, endorsement from, or connection to TikTok, ByteDance, or any third-party endpoint it interacts with. All trademarks and content belong to their respective owners.

**Educational purposes only.** This is a Proof of Concept for DOM analysis and browser automation, published for research into how modern web apps structure and gate content. It is not built, tested, or maintained as production scraping infrastructure, and ships with zero guarantees of correctness, uptime, or continued functionality.

**Terms of Service.** Running this script violates the Terms of Service of TikTok and of the third-party download endpoint it depends on to resolve media URLs. Both explicitly prohibit this kind of automated access. That violation belongs to whoever runs the script, not to whoever wrote it.

**Copyright and ethics.** Downloaded content remains the property of its original creator. This tool is scoped to local, personal archiving only - no re-uploading, redistribution, public rehosting, or commercial use in any form. Infringing on a creator's rights with this tool is a decision you make, not one the tool makes for you.

**Compliance is on you.** Laws governing scraping, automated access, and data collection vary by jurisdiction and change over time. Confirming your use case is legal where you live, and where the target account is based, is entirely your responsibility. If you need a real answer, consult a lawyer, not this file.

**Technical limitations.** Aggressive scraping trips TikTok's rate limits, CAPTCHAs, or HTTP 403 blocks. When that happens the script pauses and waits for manual browser intervention - solving the CAPTCHA or logging back in - before continuing. That's expected behavior, not a bug.

Using this software means you have read this section and agree to it in full. If you don't agree, don't run the code.
