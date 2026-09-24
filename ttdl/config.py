import shutil
from dataclasses import dataclass, field
from pathlib import Path

import tomllib


def _parse_size(size_str: str) -> int:
    size_str = size_str.upper().strip()

    if size_str.endswith("MB"):
        return int(float(size_str[:-2]) * 1024 * 1024)

    if size_str.endswith("KB"):
        return int(float(size_str[:-2]) * 1024)

    if size_str.endswith("GB"):
        return int(float(size_str[:-2]) * 1024 * 1024 * 1024)

    if size_str.endswith("B"):
        return int(size_str[:-1])

    return int(size_str)


@dataclass
class AppConfig:
    workspace_dir: Path
    downloads_dir: Path = field(init=False)
    tmp_dir: Path = field(init=False)
    logs_dir: Path = field(init=False)
    session_dir: Path = field(init=False)

    # General constraints
    min_video_height: int = 1080
    max_video_duration: int = 16

    # Playwright / Scraper
    scroll_wait_ms: int = 5000
    max_stale_scrolls: int = 10
    manual_wait_rounds: int = 60
    manual_wait_interval_ms: int = 5000
    musicaldown_max_retries: int = 3

    # Download constraints
    min_file_size_bytes: int = 100_000
    max_file_size_bytes: int = 30_000_000
    download_timeout: int = 60
    download_chunk_size: int = 65536
    download_max_retries: int = 3

    # Browser
    browser_executable: str | None = None

    def __post_init__(self) -> None:
        self.downloads_dir = self.workspace_dir / "downloads"
        self.tmp_dir = self.workspace_dir / ".tmp"
        self.logs_dir = self.workspace_dir / "logs"
        self.session_dir = self.workspace_dir / ".sessions"
        self._ensure_folders()
        self._load_toml()

    def _ensure_folders(self) -> None:
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def _load_toml(self) -> None:
        cfg_path = self.workspace_dir / "config.toml"
        if not cfg_path.exists():
            self._generate_default_toml(cfg_path)

        with open(cfg_path, "rb") as f:
            data = tomllib.load(f)

        if "scraper" in data:
            s = data["scraper"]
            self.min_video_height = s.get("min_video_height", self.min_video_height)
            self.max_video_duration = s.get(
                "max_video_duration", self.max_video_duration
            )
            self.scroll_wait_ms = s.get("scroll_wait_ms", self.scroll_wait_ms)
            self.max_stale_scrolls = s.get("max_stale_scrolls", self.max_stale_scrolls)
            self.manual_wait_rounds = s.get(
                "manual_wait_rounds", self.manual_wait_rounds
            )
            self.manual_wait_interval_ms = s.get(
                "manual_wait_interval_ms", self.manual_wait_interval_ms
            )
            self.musicaldown_max_retries = s.get(
                "musicaldown_max_retries", self.musicaldown_max_retries
            )

        if "download" in data:
            d = data["download"]
            if "min_file_size" in d:
                self.min_file_size_bytes = _parse_size(d["min_file_size"])
            if "max_file_size" in d:
                self.max_file_size_bytes = _parse_size(d["max_file_size"])
            self.download_timeout = d.get("timeout", self.download_timeout)
            self.download_chunk_size = d.get("chunk_size", self.download_chunk_size)
            self.download_max_retries = d.get("max_retries", self.download_max_retries)

        if "browser" in data:
            b = data["browser"]
            self.browser_executable = b.get("executable")

    def _generate_default_toml(self, cfg_path: Path) -> None:
        content = """[scraper]
min_video_height = 1080
max_video_duration = 16
scroll_wait_ms = 5000
max_stale_scrolls = 10
manual_wait_rounds = 60
manual_wait_interval_ms = 5000
musicaldown_max_retries = 3

[download]
min_file_size = "100KB"
max_file_size = "30MB"
timeout = 60
chunk_size = 65536
max_retries = 3

[browser]
# Uncomment to use a custom browser executable path
# executable = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
"""
        cfg_path.write_text(content, encoding="utf-8")

    def validate_dependencies(self, require_ffmpeg: bool = False) -> None:
        for pattern in ("temp_raw_*.mp4", "temp_proc_*.mp4"):
            for f in self.tmp_dir.glob(pattern):
                try:
                    f.unlink(missing_ok=True)
                except Exception:
                    pass

        if require_ffmpeg:
            for binary in ("ffmpeg", "ffprobe"):
                if not shutil.which(binary):
                    raise RuntimeError(f"{binary.upper()} NOT FOUND in system PATH")
