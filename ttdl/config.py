import os
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ttdl.termux import is_termux


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


def format_size(num_bytes: int) -> str:
    if num_bytes >= 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024 * 1024):.2f} GB"

    if num_bytes >= 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.2f} MB"

    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.2f} KB"

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

    if sys.platform != "win32" and sys.platform != "darwin":
        xdg_dl = os.environ.get("XDG_DOWNLOAD_DIR")
        if xdg_dl and os.path.isdir(xdg_dl):
            return Path(xdg_dl) / "ttdl"

    home_downloads = Path.home() / "Downloads"
    return home_downloads / "ttdl"


def get_active_config_path(workspace_dir: Path | None = None) -> Path:
    if workspace_dir and (workspace_dir / "config.toml").is_file():
        return workspace_dir / "config.toml"

    cwd_cfg = Path.cwd() / "config.toml"
    if cwd_cfg.is_file():
        return cwd_cfg

    return get_config_dir() / "config.toml"


DEFAULT_TOML_CONTENT = """[scraper]
min_video_height = 1080
max_video_duration = 0
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
# download_dir = ""

[browser]
# Uncomment to use a custom browser executable path
# executable = "C:\\\\Program Files\\\\Google\\\\Chrome\\\\Application\\\\chrome.exe"
"""

CONFIG_SCHEMA: dict[str, tuple[str, type]] = {
    "min_video_height": ("scraper", int),
    "max_video_duration": ("scraper", int),
    "scroll_wait_ms": ("scraper", int),
    "max_stale_scrolls": ("scraper", int),
    "manual_wait_rounds": ("scraper", int),
    "manual_wait_interval_ms": ("scraper", int),
    "musicaldown_max_retries": ("scraper", int),
    "min_file_size": ("download", str),
    "max_file_size": ("download", str),
    "timeout": ("download", int),
    "chunk_size": ("download", int),
    "max_retries": ("download", int),
    "download_dir": ("download", str),
    "executable": ("browser", str),
}


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
        for folder in (
            self.downloads_dir,
            self.tmp_dir,
            self.logs_dir,
            self.session_dir,
            self.json_dir,
            self.config_dir,
        ):
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass

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
            if "download_dir" in d and d["download_dir"]:
                self.downloads_dir = Path(os.path.expanduser(str(d["download_dir"])))
            self.download_timeout = d.get("timeout", self.download_timeout)
            self.download_chunk_size = d.get("chunk_size", self.download_chunk_size)
            self.download_max_retries = d.get("max_retries", self.download_max_retries)

        if "browser" in data:
            b = data["browser"]
            self.browser_executable = b.get("executable")

    def reset_config(self) -> None:
        try:
            self.cfg_path.parent.mkdir(parents=True, exist_ok=True)
            self.cfg_path.write_text(DEFAULT_TOML_CONTENT, encoding="utf-8")
        except OSError:
            pass

    def get_setting(self, key: str) -> Any:
        normalized_key = key.split(".")[-1].strip()
        if normalized_key == "download_dir":
            return str(self.downloads_dir)

        return getattr(self, normalized_key, None)

    def set_setting(self, raw_key: str, raw_value: str) -> bool:
        normalized_key = raw_key.split(".")[-1].strip()
        if normalized_key not in CONFIG_SCHEMA:
            return False

        section, target_type = CONFIG_SCHEMA[normalized_key]
        try:
            val_to_store: Any
            if target_type is int:
                val_to_store = int(raw_value)
                rendered = str(val_to_store)
            else:
                val_to_store = str(raw_value).strip("'\"")
                rendered = f'"{val_to_store}"'
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


def clean_cache(
    config: AppConfig, clean_all: bool = False, clean_json: bool = False
) -> tuple[int, int]:
    files_removed = 0
    bytes_freed = 0

    def _delete_file(p: Path) -> None:
        nonlocal files_removed, bytes_freed
        try:
            if p.is_file():
                bytes_freed += p.stat().st_size
                p.unlink(missing_ok=True)
                files_removed += 1
        except OSError:
            pass

    def _delete_dir_contents(d: Path) -> None:
        if not d.exists():
            return
        for root, dirs, files in os.walk(d, topdown=False):
            for name in files:
                _delete_file(Path(root) / name)
            for name in dirs:
                try:
                    (Path(root) / name).rmdir()
                except OSError:
                    pass

    # Clean JSON cache
    if clean_all or clean_json:
        if config.json_dir.exists():
            for f in config.json_dir.glob("*.json"):
                _delete_file(f)

    if clean_json and not clean_all:
        return files_removed, bytes_freed

    # Clean tmp files
    if config.tmp_dir.exists():
        for pat in ("temp_raw_*.mp4", "temp_proc_*.mp4", "*.tmp", "*.mp4"):
            for f in config.tmp_dir.glob(pat):
                _delete_file(f)

    # Clean logs
    if config.logs_dir.exists():
        for f in config.logs_dir.glob("*.log"):
            _delete_file(f)

    # Clean sessions
    if config.session_dir.exists():
        _delete_dir_contents(config.session_dir)

    return files_removed, bytes_freed
