import os
import shutil
import sys
from typing import Any, Final, cast

BLOCK_INDICATORS: Final[tuple[str, ...]] = (
    "access denied",
    "http error 403",
    "you don't have the user rights",
    "please verify",
    "verify to continue",
)

JS_CHECK_BLOCKED: Final[str] = (
    "() => document.body ? document.body.innerText.substring(0, 500) : ''"
)

JS_COUNT_LINKS: Final[str] = """() => {
    const userPage = document.querySelector('[data-e2e="user-page"]') ||
                     document.querySelector('[data-e2e="user-post-item-list"]') ||
                     document;
    return userPage.querySelectorAll(
        'a[href*="/video/"], a[href*="/photo/"], a[href*="/story/"]'
    ).length;
}"""

JS_COLLECT_LINKS: Final[str] = """(target) => {
    const seen = new Set();
    const out = [];
    const userPage = document.querySelector('[data-e2e="user-page"]') ||
                     document.querySelector('[data-e2e="user-post-item-list"]') ||
                     document;
    const selector = 'a[href*="/video/"], a[href*="/photo/"], a[href*="/story/"]';

    userPage.querySelectorAll(selector).forEach(a => {
        const m = (a.getAttribute('href') || '')
            .match(/@([^/]+)\\/(?:video|photo|story)\\/(\\d+)/);
        if (m && !seen.has(m[2])) {
            const author = m[1];
            if (target && author.toLowerCase() !== target) {
                return;
            }
            seen.add(m[2]);
            out.push({
                author: author,
                videoId: m[2],
            });
        }
    });
    return out;
}"""

WINDOWS_BROWSER_CANDIDATES: Final[tuple[tuple[str, str], ...]] = (
    ("edge", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    ("edge", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ("chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    ("chrome", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
)

MACOS_BROWSER_CANDIDATES: Final[tuple[tuple[str, str], ...]] = (
    ("edge", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    ("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
)

LINUX_BROWSER_CANDIDATES: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("edge", ("msedge", "microsoft-edge", "microsoft-edge-stable")),
    ("chrome", ("chrome", "google-chrome", "google-chrome-stable")),
)


def is_page_blocked(page: Any) -> bool:
    try:
        body: str = page.evaluate(JS_CHECK_BLOCKED)
    except Exception:
        return False
    else:
        lower = body.lower()
        return any(indicator in lower for indicator in BLOCK_INDICATORS)


def count_video_links(page: Any) -> int:
    try:
        return cast(int, page.evaluate(JS_COUNT_LINKS))
    except Exception:
        return 0


def collect_video_links(
    page: Any, target_username: str | None = None
) -> list[dict[str, str]]:
    try:
        target_clean = target_username.lstrip("@").lower() if target_username else ""
        return cast(list[dict[str, str]], page.evaluate(JS_COLLECT_LINKS, target_clean))
    except Exception:
        return []


def _find_windows_browser() -> tuple[str, str] | None:
    for name, path in WINDOWS_BROWSER_CANDIDATES:
        if os.path.exists(path):
            return name, path

    return None


def _find_macos_browser() -> tuple[str, str] | None:
    for name, path in MACOS_BROWSER_CANDIDATES:
        if os.path.exists(path):
            return name, path

    return None


def _find_path_browser() -> tuple[str, str] | None:
    for name, binaries in LINUX_BROWSER_CANDIDATES:
        for b in binaries:
            exe_path = shutil.which(b)
            if exe_path:
                return name, exe_path

    return None


def detect_browser() -> tuple[str, str]:
    if sys.platform == "win32":
        detected = _find_windows_browser()
        if detected:
            return detected

    if sys.platform == "darwin":
        detected = _find_macos_browser()
        if detected:
            return detected

    detected = _find_path_browser()
    if detected:
        return detected

    raise RuntimeError("NO BROWSER FOUND install Edge or Chrome")
