import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import yt_dlp
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from ttdl.logger import console

log = logging.getLogger(__name__)


class YtLog:
    def __init__(self, target_log: logging.Logger) -> None:
        self.log = target_log
        self.offline = False

    def debug(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        clean = msg.replace("(", "").replace(")", "").lower()
        self.log.error(f"fail {clean}")

    def info(self, msg: str) -> None:
        if "offline" in msg.lower():
            self.offline = True
            self.log.warning("target offline")


class SizeTracker:
    def __init__(self, target: str, workspace_dir: Path) -> None:
        self.out_dir = workspace_dir / "downloads" / "live" / target
        self.ui = Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]live stream"),
            TextColumn("[green]{task.fields[size]}"),
            TextColumn("[yellow]{task.fields[speed]}"),
            TimeElapsedColumn(),
            transient=True,
            console=console,
        )
        self.task = self.ui.add_task("dl", size="0.00MB", speed="0.00KB/s")
        self.active = False
        self.worker = threading.Thread(target=self._scan, daemon=True)

    def start(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.active = True
        self.ui.start()
        self.worker.start()

    def stop(self) -> None:
        self.active = False
        self.ui.stop()

    def _scan(self) -> None:
        last_b = 0
        last_t = time.time()

        while self.active:
            time.sleep(0.5)
            try:
                items: list[str] = [
                    os.path.join(self.out_dir, f) for f in os.listdir(self.out_dir)
                ]
                if not items:
                    continue

                latest = max(items, key=os.path.getmtime)
                b = os.path.getsize(latest)
            except Exception:
                continue

            now = time.time()
            dt = now - last_t
            if dt >= 1.0:
                speed = (b - last_b) / dt if b > last_b else 0.0
                self.ui.update(
                    self.task,
                    size=f"{b / 1048576:.2f}MB",
                    speed=f"{speed / 1024:.2f}KB/s",
                )
                last_b = b
                last_t = now


def make_cfg(target: str, yt_log: YtLog, workspace_dir: Path) -> dict[str, Any]:
    outtmpl = str(
        workspace_dir
        / "downloads"
        / "live"
        / target
        / f"{target}_live_%(id)s_%(epoch)s.%(ext)s"
    )
    cfg = {
        "format": "bestvideo+bestaudio/best",
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Linux; Android 13; SM-G998B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36",
            "Sec-Ch-Ua-Mobile": "?1",
            "Sec-Ch-Ua-Platform": '"Android"',
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.tiktok.com/",
            "Connection": "keep-alive",
        },
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "logger": yt_log,
        "socket_timeout": 30.0,
        "retries": 50,
        "fragment_retries": 50,
        "extractor_retries": 10,
        "retry_sleep": {"http": 2, "fragment": 3, "extractor": 2},
        "hls_use_mpegts": True,
        "hls_prefer_native": True,
        "buffersize": 1024 * 32,
    }
    return cfg


def run_dl(target: str, workspace_dir: Path) -> int:
    url = f"https://www.tiktok.com/@{target}/live"
    log.info(f"target {target}")
    log.info(f"out downloads/live/{target}")

    yt_log = YtLog(log)
    cfg = make_cfg(target, yt_log, workspace_dir)
    tracker = SizeTracker(target, workspace_dir)

    try:
        tracker.start()
        with yt_dlp.YoutubeDL(cfg) as ydl:
            ydl.download([url])

        if not yt_log.offline:
            log.info("stream end")
        return 0
    except KeyboardInterrupt:
        log.info("abort")
        return 130
    except Exception as err:
        log.error(f"fail {str(err).replace('(', '').replace(')', '').lower()}")
        return 1
    finally:
        tracker.stop()
