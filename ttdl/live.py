import os
import threading
import time
from pathlib import Path
from typing import Any

import yt_dlp

from ttdl.config import AppConfig
from ttdl.events import Reporter


class LiveDownloader:
    def __init__(self, target: str, config: AppConfig, reporter: Reporter) -> None:
        self.target = target.lstrip("@")
        self.config = config
        self.reporter = reporter
        self.workspace_dir = config.workspace_dir

    def execute(self) -> int:
        url = f"https://www.tiktok.com/@{self.target}/live"
        self.reporter.info(f"target {self.target}")
        self.reporter.info(f"out downloads/live/{self.target}")

        yt_log = _YtLog(self.reporter)
        cfg = self._make_cfg(yt_log)
        tracker = _SizeTracker(self.target, self.workspace_dir, self.reporter)

        try:
            with self.reporter:
                tracker.start()
                with yt_dlp.YoutubeDL(cfg) as ydl:
                    code = ydl.download([url])

                if not yt_log.offline:
                    self.reporter.info("stream end")
                return code
        except KeyboardInterrupt:
            raise
        except Exception:
            return 1
        finally:
            tracker.stop()

    def _make_cfg(self, yt_log: "_YtLog") -> dict[str, Any]:
        outtmpl = str(
            self.workspace_dir
            / "downloads"
            / "live"
            / self.target
            / f"{self.target}_live_%(id)s_%(epoch)s.%(ext)s"
        )
        return {
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


class _YtLog:
    def __init__(self, reporter: Reporter) -> None:
        self.reporter = reporter
        self.offline = False

    def debug(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        import re

        clean = re.sub(r"\x1b\[[0-9;]*m", "", msg)
        clean = clean.replace("(", "").replace(")", "").lower().strip()
        if clean and clean != "error:":
            if clean.startswith("error:"):
                clean = clean[6:].strip()
            self.reporter.error(f"fail {clean}")

    def info(self, msg: str) -> None:
        if "offline" in msg.lower():
            self.offline = True
            self.reporter.warning("target offline")


class _SizeTracker:
    def __init__(self, target: str, workspace_dir: Path, reporter: Reporter) -> None:
        self.out_dir = workspace_dir / "downloads" / "live" / target
        self.reporter = reporter
        self.active = False
        self.worker = threading.Thread(target=self._scan, daemon=True)

    def start(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.active = True
        self.worker.start()

    def stop(self) -> None:
        self.active = False

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
                self.reporter.on_live_update(
                    size=f"{b / 1048576:.2f}MB",
                    speed=f"{speed / 1024:.2f}KB/s",
                )
                last_b = b
                last_t = now
