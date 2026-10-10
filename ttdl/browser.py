import os
import shutil
import sys
from typing import Any, cast


def is_page_blocked(page: Any) -> bool:
    try:
        body = page.evaluate(
            "() => document.body ? document.body.innerText.substring(0, 500) : ''"
        )
        indicators = [
            "access denied",
            "http error 403",
            "you don't have the user rights",
            "please verify",
            "verify to continue",
        ]
        lower = body.lower()
        return any(i in lower for i in indicators)
    except Exception:
        return False


def count_video_links(page: Any) -> int:
    try:
        return cast(
            int,
            page.evaluate(
                """() => {
                const userPage = document.querySelector('[data-e2e="user-page"]') ||
                                 document.querySelector('[data-e2e="user-post-item-list"]') ||
                                 document;
                return userPage.querySelectorAll('a[href*="/video/"], a[href*="/photo/"], a[href*="/story/"]').length;
            }"""
            ),
        )
    except Exception:
        return 0


def collect_video_links(
    page: Any, target_username: str | None = None
) -> list[dict[str, str]]:
    try:
        target_clean = target_username.lstrip("@").lower() if target_username else ""
        return cast(
            list[dict[str, str]],
            page.evaluate(
                """(target) => {
                const seen = new Set();
                const out = [];
                const userPage = document.querySelector('[data-e2e="user-page"]') ||
                                 document.querySelector('[data-e2e="user-post-item-list"]') ||
                                 document;
                const selector = 'a[href*="/video/"], a[href*="/photo/"], a[href*="/story/"]';

                userPage.querySelectorAll(selector)
                    .forEach(a => {
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
            }""",
                target_clean,
            ),
        )
    except Exception:
        return []


def detect_browser() -> tuple[str, str]:
    if sys.platform == "win32":
        win_candidates = [
            (
                "edge",
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            ),
            ("edge", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
            ("chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            (
                "chrome",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            ),
        ]
        for name, path in win_candidates:
            if os.path.exists(path):
                return name, path

    elif sys.platform == "darwin":
        mac_candidates = [
            (
                "edge",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            ),
            (
                "chrome",
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            ),
        ]
        for name, path in mac_candidates:
            if os.path.exists(path):
                return name, path

    candidates = [
        ("edge", ["msedge", "microsoft-edge", "microsoft-edge-stable"]),
        ("chrome", ["chrome", "google-chrome", "google-chrome-stable"]),
    ]
    for name, bins in candidates:
        for b in bins:
            exe_path = shutil.which(b)
            if exe_path:
                return name, exe_path

    raise RuntimeError("NO BROWSER FOUND install Edge or Chrome")
