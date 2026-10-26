import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.request
from importlib.metadata import version as get_package_version
from pathlib import Path
from typing import Any, Final

from ttdl.events import Reporter

log = logging.getLogger(__name__)

PYPI_PLAYWRIGHT_URL: Final[str] = "https://pypi.org/pypi/playwright/json"
ADB_OFFLINE_BANNER: Final[str] = (
    "\n"
    "=================================================\n"
    "                   ADB OFFLINE                   \n"
    "=================================================\n"
    " Please connect ADB Wireless Debugging           \n"
    "  1. Enable Developer Options and Wireless Debug \n"
    "  2. Split screen with Termux                    \n"
    "  3. Run: adb pair <ip>:<port>                   \n"
    "  4. Run: adb connect <ip>:<port>                \n"
    "=================================================\n"
)


def is_termux() -> bool:
    return "com.termux" in os.environ.get("PREFIX", "")


def _version_to_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v))


def _extract_target_playwright_version(pyproject_path: Path) -> str | None:
    if not pyproject_path.is_file():
        return None

    try:
        with open(pyproject_path, "rb") as f:
            pdata = tomllib.load(f)

        for dep in pdata.get("project", {}).get("dependencies", []):
            dep_str = str(dep)
            if dep_str.startswith("playwright"):
                part = dep_str.split(";")[0].strip()

                if ">=" in part:
                    return str(part.split(">=")[1].strip())

                if "==" in part:
                    return str(part.split("==")[1].strip())
    except Exception:
        pass

    return None


def _find_compatible_wheel(release_files: list[dict[str, Any]]) -> str | None:
    for f in release_files:
        filename = str(f.get("filename", ""))
        if "manylinux1_x86_64" in filename and filename.endswith(".whl"):
            return str(f["url"])

    return None


def _download_and_install_wheel(
    wheel_url: str, version_str: str, reporter: Reporter | None
) -> None:
    whl_name = f"playwright-{version_str}-py3-none-any.whl"
    _report(reporter, "info", "PLAYWRIGHT_INSTALL downloading v%s...", version_str)
    if reporter is not None:
        reporter.set_status("Downloading Playwright wheel...")

    try:
        urllib.request.urlretrieve(wheel_url, whl_name)
    finally:
        if reporter is not None:
            reporter.clear_status()

    _report(reporter, "info", "PLAYWRIGHT_INSTALL running pip install...")
    res = subprocess.run(
        [sys.executable, "-m", "pip", "install", whl_name],
        capture_output=True,
        text=True,
        check=False,
    )
    if os.path.exists(whl_name):
        try:
            os.remove(whl_name)
        except OSError:
            pass

    if res.returncode != 0:
        _report(
            reporter,
            "error",
            "Failed to install Playwright: %s",
            res.stderr.strip(),
        )
        sys.exit(1)

    _report(reporter, "info", "PLAYWRIGHT_INSTALL success!")


def _report(reporter: Reporter | None, level: str, msg: str, *args: Any) -> None:
    if reporter is not None:
        fn = getattr(reporter, level, None)
        if callable(fn):
            fn(msg, *args)
            return

    log_fn = getattr(log, level, log.info)
    log_fn(msg, *args)


def _ensure_playwright_installed(
    reporter: Reporter | None, target_version: str | None
) -> None:
    needs_install = False
    if importlib.util.find_spec("playwright") is None:
        needs_install = True

    else:
        try:
            installed = get_package_version("playwright")
        except Exception:
            installed = ""

        if (
            target_version
            and installed
            and _version_to_tuple(installed) < _version_to_tuple(target_version)
        ):
            _report(
                reporter,
                "info",
                "PLAYWRIGHT_UPDATE mismatch detected (installed: %s, target: %s)",
                installed,
                target_version,
            )
            needs_install = True

    if not needs_install:
        return

    _report(reporter, "info", "PLAYWRIGHT_INSTALL fetching wheel...")
    try:
        req = urllib.request.Request(PYPI_PLAYWRIGHT_URL)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())

        version_to_fetch = target_version or data["info"]["version"]
        releases = data.get("releases", {})
        if version_to_fetch not in releases:
            _report(
                reporter,
                "error",
                "Playwright version %s not found on PyPI.",
                version_to_fetch,
            )
            sys.exit(1)

        target_url = _find_compatible_wheel(releases[version_to_fetch])
        if not target_url:
            _report(
                reporter, "error", "Could not find Playwright manylinux1_x86_64 wheel."
            )
            sys.exit(1)

        _download_and_install_wheel(target_url, version_to_fetch, reporter)
    except Exception as e:
        _report(reporter, "error", "Failed to install Playwright: %s", e)
        sys.exit(1)


def _check_adb_status(reporter: Reporter | None) -> None:
    adb_check = subprocess.run(
        ["adb", "devices"], capture_output=True, text=True, check=False
    )

    stdout = adb_check.stdout
    if "device\n" not in stdout and "\tdevice" not in stdout:
        _report(reporter, "error", ADB_OFFLINE_BANNER)
        sys.exit(1)


def setup_termux(reporter: Reporter | None = None) -> None:
    if not is_termux():
        return

    for tool in ("adb", "node"):
        if not shutil.which(tool):
            _report(
                reporter,
                "error",
                "%s not found. Please install %s.",
                tool,
                "android-tools" if tool == "adb" else "nodejs",
            )
            sys.exit(1)

    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    target_version = _extract_target_playwright_version(pyproject_path)
    _ensure_playwright_installed(reporter, target_version)

    node_path = shutil.which("node")
    if node_path:
        os.environ["PLAYWRIGHT_NODEJS_PATH"] = node_path
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"

    _check_adb_status(reporter)
