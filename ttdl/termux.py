import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.request
from typing import Any

from ttdl.events import Reporter

log = logging.getLogger(__name__)


def is_termux() -> bool:
    return "com.termux" in os.environ.get("PREFIX", "")


def setup_termux(reporter: Reporter | None = None) -> None:
    if not is_termux():
        return

    def _info(msg: str, *args: Any) -> None:
        if reporter is not None:
            reporter.info(msg, *args)
        else:
            log.info(msg, *args)

    def _error(msg: str, *args: Any) -> None:
        if reporter is not None:
            reporter.error(msg, *args)
        else:
            log.error(msg, *args)

    if not shutil.which("adb"):
        _error("adb not found. Please install android-tools.")
        sys.exit(1)

    if not shutil.which("node"):
        _error("nodejs not found. Please install nodejs.")
        sys.exit(1)

    try:
        import importlib.util

        if importlib.util.find_spec("playwright") is None:
            raise ImportError("Playwright not installed")

        try:
            from importlib.metadata import version

            installed_version = version("playwright")
        except Exception:
            installed_version = ""

        target_version = None
        pyproject_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "pyproject.toml",
        )
        if os.path.exists(pyproject_path):
            try:
                with open(pyproject_path, "rb") as f:
                    pdata = tomllib.load(f)
                for dep in pdata.get("project", {}).get("dependencies", []):
                    if dep.startswith("playwright"):
                        part = dep.split(";")[0].strip()
                        if ">=" in part:
                            target_version = part.split(">=")[1].strip()
                        elif "==" in part:
                            target_version = part.split("==")[1].strip()
                        break
            except Exception:
                pass

        def _vtuple(v: str) -> tuple[int, ...]:
            return tuple(int(x) for x in re.findall(r"\d+", v))

        if (
            target_version
            and installed_version
            and _vtuple(installed_version) < _vtuple(target_version)
        ):
            _info(
                "PLAYWRIGHT_UPDATE mismatch detected (installed: %s, target: %s)",
                installed_version,
                target_version,
            )
            raise ImportError("Version mismatch")
    except ImportError:
        _info("PLAYWRIGHT_INSTALL fetching manylinux1_x86_64 wheel...")
        try:
            req = urllib.request.Request("https://pypi.org/pypi/playwright/json")
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())

            if target_version:
                version_to_fetch = target_version
            else:
                version_to_fetch = data["info"]["version"]

            if version_to_fetch not in data["releases"]:
                _error("Playwright version %s not found on PyPI.", version_to_fetch)
                sys.exit(1)

            target_url = next(
                (
                    f["url"]
                    for f in data["releases"][version_to_fetch]
                    if "manylinux1_x86_64" in f["filename"]
                    and f["filename"].endswith(".whl")
                ),
                None,
            )

            if not target_url:
                _error("Could not find Playwright manylinux1_x86_64 wheel.")
                sys.exit(1)

            whl_name = f"playwright-{version_to_fetch}-py3-none-any.whl"
            _info("PLAYWRIGHT_INSTALL downloading %s...", target_url)
            if reporter is not None:
                reporter.set_status("Downloading Playwright wheel...")
            try:
                urllib.request.urlretrieve(target_url, whl_name)
            finally:
                if reporter is not None:
                    reporter.clear_status()

            _info("PLAYWRIGHT_INSTALL running pip install...")
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
                _error("Failed to install Playwright: %s", res.stderr.strip())
                sys.exit(1)

            _info("PLAYWRIGHT_INSTALL success!")
        except Exception as e:
            if reporter is not None:
                reporter.clear_status()
            _error("Failed to install Playwright: %s", e)
            sys.exit(1)

    node_path = shutil.which("node")
    if node_path:
        os.environ["PLAYWRIGHT_NODEJS_PATH"] = node_path
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"

    adb_check = subprocess.run(
        ["adb", "devices"], capture_output=True, text=True, check=False
    )
    if "device\n" not in adb_check.stdout and "\tdevice" not in adb_check.stdout:
        _error(
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
        sys.exit(1)
