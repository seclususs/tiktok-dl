import os
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from ttdl.termux import is_termux

BYTES_PER_KB: Final[int] = 1024
BYTES_PER_MB: Final[int] = 1024 * 1024
BYTES_PER_GB: Final[int] = 1024 * 1024 * 1024

DEFAULT_TOML_CONTENT: Final[str] = """[scraper]
min_video_height = 1080
max_video_duration = 0

[download]
download_dir = ""
max_file_size = "30MB"
timeout = 60
max_retries = 3

[browser]
executable = ""
"""

CONFIG_SCHEMA: Final[dict[str, tuple[str, type]]] = {
    "min_video_height": ("scraper", int),
    "max_video_duration": ("scraper", int),
    "download_dir": ("download", str),
    "max_file_size": ("download", str),
    "timeout": ("download", int),
    "max_retries": ("download", int),
    "executable": ("browser", str),
}

CONFIG_ATTR_MAP: Final[dict[str, str]] = {
    "timeout": "download_timeout",
    "max_retries": "download_max_retries",
    "executable": "browser_executable",
    "download_dir": "downloads_dir",
}

TMP_CLEAN_PATTERNS: Final[tuple[str, ...]] = (
    "temp_raw_*.mp4",
    "temp_proc_*.mp4",
    "*.download",
    "*.tmp",
    "*.mp4",
)

LOG_CLEAN_PATTERNS: Final[tuple[str, ...]] = ("*.log*", "*.html")
CONFIG_CLEAN_FILES: Final[tuple[str, ...]] = ("config.toml", "cookies.txt")


def _parse_size(size_str: str) -> int:
    raw = size_str.upper().strip()

    if raw.endswith("GB"):
        return int(float(raw[:-2]) * BYTES_PER_GB)

    if raw.endswith("MB"):
        return int(float(raw[:-2]) * BYTES_PER_MB)

    if raw.endswith("KB"):
        return int(float(raw[:-2]) * BYTES_PER_KB)

    if raw.endswith("B"):
        return int(raw[:-1])

    return int(raw)


def format_size(num_bytes: int) -> str:
    if num_bytes >= BYTES_PER_GB:
        return f"{num_bytes / BYTES_PER_GB:.2f} GB"

    if num_bytes >= BYTES_PER_MB:
        return f"{num_bytes / BYTES_PER_MB:.2f} MB"

    if num_bytes >= BYTES_PER_KB:
        return f"{num_bytes / BYTES_PER_KB:.2f} KB"

    return f"{num_bytes} B"


def get_config_dir() -> Path:
    if is_termux():
        base = Path(os.environ.get("HOME", "/data/data/com.termux/files/home"))
        return base / ".config" / "ttdl"

    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "ttdl"
        return Path.home() / ".config" / "ttdl"

    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "ttdl"
    return Path.home() / ".config" / "ttdl"


def get_cache_dir() -> Path:
    if is_termux():
        base = Path(os.environ.get("HOME", "/data/data/com.termux/files/home"))
        return base / ".cache" / "ttdl"

    if sys.platform == "win32":
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            return Path(local_appdata) / "ttdl"
        return Path.home() / ".cache" / "ttdl"

    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "ttdl"
    return Path.home() / ".cache" / "ttdl"


def get_default_downloads_dir() -> Path:
    if is_termux():
        return Path("/storage/emulated/0/Download/ttdl")

    if sys.platform not in ("win32", "darwin"):
        xdg_dl = os.environ.get("XDG_DOWNLOAD_DIR")
        if xdg_dl and os.path.isdir(xdg_dl):
            return Path(xdg_dl) / "ttdl"

    return Path.home() / "Downloads" / "ttdl"


def get_active_config_path(workspace_dir: Path | None = None) -> Path:
    if workspace_dir and (workspace_dir / "config.toml").is_file():
        return workspace_dir / "config.toml"

    cwd_cfg = Path.cwd() / "config.toml"
    if cwd_cfg.is_file():
        return cwd_cfg

    return get_config_dir() / "config.toml"


@dataclass
class AppConfig:
    workspace_dir: Path = field(default_factory=Path.cwd)
    config_dir: Path = field(init=False)
    cache_dir: Path = field(init=False)
    cfg_path: Path = field(init=False)
    downloads_dir: Path = field(init=False)
    tmp_dir: Path = field(init=False)
    logs_dir: Path = field(init=False)
    session_dir: Path = field(init=False)
    json_dir: Path = field(init=False)

    # General constraints
    min_video_height: int = 1080
    max_video_duration: int = 0

    # Playwright / Scraper
    scroll_wait_ms: int = 5000
    max_stale_scrolls: int = 10
    manual_wait_rounds: int = 60
    manual_wait_interval_ms: int = 5000
    musicaldown_max_retries: int = 3

    # Download constraints
    min_file_size: str = "100KB"
    max_file_size: str = "30MB"
    min_file_size_bytes: int = 100_000
    max_file_size_bytes: int = 30_000_000
    download_timeout: int = 60
    download_chunk_size: int = 65536
    download_max_retries: int = 3

    # Browser
    browser_executable: str | None = None

    def __post_init__(self) -> None:
        self.config_dir = get_config_dir()
        self.cache_dir = get_cache_dir()
        self.cfg_path = get_active_config_path(self.workspace_dir)
        self.downloads_dir = get_default_downloads_dir()

        self.tmp_dir = self.cache_dir / "tmp"
        self.logs_dir = self.cache_dir / "logs"
        self.session_dir = self.cache_dir / "sessions"
        self.json_dir = self.cache_dir / "json"

        self._load_toml()
        self._ensure_folders()

    def _ensure_folders(self) -> None:
        folders = (
            self.downloads_dir,
            self.tmp_dir,
            self.logs_dir,
            self.session_dir,
            self.json_dir,
            self.config_dir,
        )
        for folder in folders:
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass

    def _load_scraper_section(self, section: dict[str, Any]) -> None:
        self.min_video_height = section.get("min_video_height", self.min_video_height)
        self.max_video_duration = section.get(
            "max_video_duration", self.max_video_duration
        )
        self.scroll_wait_ms = section.get("scroll_wait_ms", self.scroll_wait_ms)
        self.max_stale_scrolls = section.get(
            "max_stale_scrolls", self.max_stale_scrolls
        )
        self.manual_wait_rounds = section.get(
            "manual_wait_rounds", self.manual_wait_rounds
        )
        self.manual_wait_interval_ms = section.get(
            "manual_wait_interval_ms", self.manual_wait_interval_ms
        )
        self.musicaldown_max_retries = section.get(
            "musicaldown_max_retries", self.musicaldown_max_retries
        )

    def _load_download_section(self, section: dict[str, Any]) -> None:
        if "min_file_size" in section:
            self.min_file_size = str(section["min_file_size"])
            self.min_file_size_bytes = _parse_size(self.min_file_size)

        if "max_file_size" in section:
            self.max_file_size = str(section["max_file_size"])
            self.max_file_size_bytes = _parse_size(self.max_file_size)

        if section.get("download_dir"):
            self.downloads_dir = Path(os.path.expanduser(str(section["download_dir"])))

        self.download_timeout = section.get("timeout", self.download_timeout)
        self.download_chunk_size = section.get("chunk_size", self.download_chunk_size)
        self.download_max_retries = section.get(
            "max_retries", self.download_max_retries
        )

    def _load_browser_section(self, section: dict[str, Any]) -> None:
        self.browser_executable = section.get("executable") or None

    def _load_toml(self) -> None:
        self.downloads_dir = get_default_downloads_dir()
        if not self.cfg_path.exists():
            self.reset_config()

        try:
            with open(self.cfg_path, "rb") as f:
                data = tomllib.load(f)
        except Exception:
            return

        if "scraper" in data:
            self._load_scraper_section(data["scraper"])

        if "download" in data:
            self._load_download_section(data["download"])

        if "browser" in data:
            self._load_browser_section(data["browser"])

    def reset_config(self) -> None:
        try:
            self.cfg_path.parent.mkdir(parents=True, exist_ok=True)
            self.cfg_path.write_text(DEFAULT_TOML_CONTENT, encoding="utf-8")
        except OSError:
            pass

    def get_setting(self, key: str) -> Any:
        normalized_key = key.split(".")[-1].strip()
        if normalized_key not in CONFIG_SCHEMA:
            return None

        attr = CONFIG_ATTR_MAP.get(normalized_key, normalized_key)
        val = getattr(self, attr, None)

        if isinstance(val, Path):
            return str(val)

        if normalized_key == "executable" and val is None:
            return ""

        return val

    def set_setting(self, raw_key: str, raw_value: str) -> bool:
        normalized_key = raw_key.split(".")[-1].strip()
        if normalized_key not in CONFIG_SCHEMA:
            return False

        section, target_type = CONFIG_SCHEMA[normalized_key]
        try:
            if target_type is int:
                rendered = str(int(raw_value))
            else:
                rendered = f'"{str(raw_value).strip(chr(39) + chr(34))}"'
        except ValueError:
            return False

        if not self.cfg_path.exists():
            self.reset_config()

        content = self.cfg_path.read_text(encoding="utf-8")
        pattern = rf"(?m)^(\s*{re.escape(normalized_key)}\s*=\s*).*$"

        if re.search(pattern, content):
            new_content = re.sub(pattern, rf"\g<1>{rendered}", content)
        else:
            sec_pattern = rf"(?m)^(\[{re.escape(section)}\]\s*)$"
            if re.search(sec_pattern, content):
                new_content = re.sub(
                    sec_pattern, rf"\1{normalized_key} = {rendered}\n", content
                )
            else:
                new_content = (
                    content + f"\n[{section}]\n{normalized_key} = {rendered}\n"
                )

        self.cfg_path.write_text(new_content, encoding="utf-8")
        self._load_toml()
        self._ensure_folders()
        return True

    def validate_dependencies(self, require_ffmpeg: bool = False) -> None:
        clean_patterns = (
            "temp_raw_*.mp4",
            "temp_proc_*.mp4",
            "*.download",
            "*.tmp",
        )
        for pattern in clean_patterns:
            for f in self.tmp_dir.glob(pattern):
                try:
                    f.unlink(missing_ok=True)
                except Exception:
                    pass

        if require_ffmpeg:
            for binary in ("ffmpeg", "ffprobe"):
                if not shutil.which(binary):
                    raise RuntimeError(f"{binary.upper()} NOT FOUND in system PATH")


def open_config_editor(cfg_path: Path) -> None:
    if not cfg_path.exists():
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(DEFAULT_TOML_CONTENT, encoding="utf-8")

    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if editor:
        subprocess.run([editor, str(cfg_path)], check=False)
        return

    if sys.platform == "win32":
        startfile = getattr(os, "startfile", None)
        if callable(startfile):
            try:
                startfile(str(cfg_path))
                return
            except Exception:
                pass
        subprocess.run(["notepad.exe", str(cfg_path)], check=False)
        return

    if sys.platform == "darwin":
        subprocess.run(["open", str(cfg_path)], check=False)
        return

    for ed in ("xdg-open", "nano", "vi"):
        if shutil.which(ed):
            subprocess.run([ed, str(cfg_path)], check=False)
            return


def _purge_file(p: Path) -> tuple[int, int]:
    try:
        if p.is_file():
            size = p.stat().st_size
            p.unlink(missing_ok=True)
            return 1, size
    except OSError:
        pass

    return 0, 0


def _purge_directory_contents(d: Path) -> tuple[int, int]:
    if not d.exists():
        return 0, 0

    removed = 0
    freed = 0

    for root, dirs, files in os.walk(d, topdown=False):
        for name in files:
            r, b = _purge_file(Path(root) / name)
            removed += r
            freed += b

        for name in dirs:
            try:
                (Path(root) / name).rmdir()
            except OSError:
                pass

    return removed, freed


def clean_cache(
    config: AppConfig, clean_all: bool = False, clean_json: bool = False
) -> tuple[int, int]:
    files_removed = 0
    bytes_freed = 0

    def _track(count: int, bytes_count: int) -> None:
        nonlocal files_removed, bytes_freed
        files_removed += count
        bytes_freed += bytes_count

    # Clean JSON cache
    if (clean_all or clean_json) and config.json_dir.exists():
        for json_file in config.json_dir.glob("*.json"):
            _track(*_purge_file(json_file))

    if clean_json and not clean_all:
        return files_removed, bytes_freed

    # Clean temporary downloads and fragments
    if config.tmp_dir.exists():
        for pat in TMP_CLEAN_PATTERNS:
            for tmp_file in config.tmp_dir.glob(pat):
                _track(*_purge_file(tmp_file))

    # Clean logs
    if config.logs_dir.exists():
        for pat in LOG_CLEAN_PATTERNS:
            for log_file in config.logs_dir.glob(pat):
                _track(*_purge_file(log_file))

    # Clean browser sessions
    if config.session_dir.exists():
        _track(*_purge_directory_contents(config.session_dir))

    # Clean configs and cookies when clean all
    if clean_all and config.config_dir.exists():
        for cfg_name in CONFIG_CLEAN_FILES:
            _track(*_purge_file(config.config_dir / cfg_name))

    return files_removed, bytes_freed
