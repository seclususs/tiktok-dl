import json
import logging
import os
import shutil
import subprocess
import sys
import urllib.request

log = logging.getLogger(__name__)


def is_termux() -> bool:
    return "com.termux" in os.environ.get("PREFIX", "")


def setup_termux() -> None:
    if not is_termux():
        return

    if not shutil.which("adb"):
        log.error("adb not found. Please install android-tools.")
        sys.exit(1)

    if not shutil.which("node"):
        log.error("nodejs not found. Please install nodejs.")
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

        req_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "requirements.txt",
        )
        target_version = None
        if os.path.exists(req_path):
            with open(req_path, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("playwright=="):
                        target_version = line.split("==")[1].split(";")[0].strip()
                        break

        if target_version and installed_version != target_version:
            log.info(
                "PLAYWRIGHT_UPDATE mismatch detected (installed: %s, target: %s)",
                installed_version,
                target_version,
            )
            raise ImportError("Version mismatch")
    except ImportError:
        log.info("PLAYWRIGHT_INSTALL fetching manylinux1_x86_64 wheel...")
        try:
            req = urllib.request.Request("https://pypi.org/pypi/playwright/json")
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())

            if "target_version" in locals() and target_version:
                version_to_fetch = target_version
            else:
                version_to_fetch = data["info"]["version"]

            if version_to_fetch not in data["releases"]:
                log.error("Playwright version %s not found on PyPI.", version_to_fetch)
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
                log.error("Could not find Playwright manylinux1_x86_64 wheel.")
                sys.exit(1)

            whl_name = f"playwright-{version_to_fetch}-py3-none-any.whl"
            log.info("PLAYWRIGHT_INSTALL downloading %s...", target_url)
            urllib.request.urlretrieve(target_url, whl_name)
            log.info("PLAYWRIGHT_INSTALL running pip install...")
            subprocess.run(
                [sys.executable, "-m", "pip", "install", whl_name], check=True
            )
            os.remove(whl_name)
            log.info("PLAYWRIGHT_INSTALL success!")
        except Exception as e:
            log.error("Failed to install Playwright: %s", e)
            sys.exit(1)

    node_path = shutil.which("node")
    if node_path:
        os.environ["PLAYWRIGHT_NODEJS_PATH"] = node_path
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"

    adb_check = subprocess.run(
        ["adb", "devices"], capture_output=True, text=True, check=False
    )
    if "device\n" not in adb_check.stdout and "\tdevice" not in adb_check.stdout:
        log.error(
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
