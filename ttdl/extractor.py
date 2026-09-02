import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from playwright.sync_api import BrowserContext

from ttdl.browser import collect_video_links, count_video_links, is_page_blocked
from ttdl.constants import (
    MANUAL_WAIT_INTERVAL_MS,
    MANUAL_WAIT_ROUNDS,
    MAX_STALE_SCROLLS,
    SCROLL_WAIT_MS,
)

log = logging.getLogger(__name__)


class ProfileExtractor:
    def __init__(self, target_username: str, workspace_dir: Path):
        self.target_username = target_username
        self.workspace_dir = workspace_dir

    def _extract_video_nodes(
        self,
        node: Any,
        collection: dict[str, dict[str, Any]],
    ) -> None:
        if isinstance(node, list):
            for item in node:
                self._extract_video_nodes(item, collection)
            return

        if not isinstance(node, dict):
            return

        vid_id = str(node.get("id", node.get("item_id", node.get("video_id", ""))))
        if vid_id.isdigit() and len(vid_id) >= 15:
            c_time = node.get("createTime") or node.get("create_time")
            if c_time and ("video" in node or "imagePost" in node):
                post_type = "photo" if "imagePost" in node else "video"
                duration = 0
                vid_data = node.get("video")
                if isinstance(vid_data, dict):
                    duration = int(vid_data.get("duration", 0))

                try:
                    c_time_int = int(c_time)
                    if vid_id in collection:
                        collection[vid_id]["createTime"] = c_time_int
                        collection[vid_id]["post_type"] = post_type
                        collection[vid_id]["duration"] = duration
                        if "author" in node:
                            collection[vid_id]["author"] = node["author"]
                    else:
                        collection[vid_id] = {
                            "id": vid_id,
                            "createTime": c_time_int,
                            "author": node.get("author"),
                            "post_type": post_type,
                            "duration": duration,
                        }
                except ValueError:
                    pass
        for value in node.values():
            self._extract_video_nodes(value, collection)

    def _handle_api_response(
        self,
        response: Any,
        videos_dict: dict[str, dict[str, Any]],
    ) -> None:
        try:
            url = response.url
            api_patterns = [
                "/api/post/item_list",
                "/api/creator/item_list",
                "item_list",
            ]
            if not any(p in url for p in api_patterns):
                return
            if response.status != 200:
                return

            data = json.loads(response.body())
            items = data.get("itemList") or data.get("items") or []
            if not items and isinstance(data, dict):
                for val in data.values():
                    if not isinstance(val, list) or not val:
                        continue
                    first = val[0]
                    if isinstance(first, dict) and ("id" in first or "video" in first):
                        items = val
                        break

            if not items:
                return

            for item in items:
                if not isinstance(item, dict):
                    continue

                vid_id = str(
                    item.get("id", item.get("item_id", item.get("video_id", "")))
                )
                if not (vid_id.isdigit() and len(vid_id) >= 15):
                    continue

                c_time = item.get("createTime") or item.get("create_time") or 0
                author = item.get("author")
                post_type = "photo" if "imagePost" in item else "video"
                duration = 0
                vid_data = item.get("video")
                if isinstance(vid_data, dict):
                    duration = int(vid_data.get("duration", 0))

                c_time_int = int(c_time) if c_time else 0

                if vid_id in videos_dict:
                    if c_time_int:
                        videos_dict[vid_id]["createTime"] = c_time_int
                    if author:
                        videos_dict[vid_id]["author"] = author
                    videos_dict[vid_id]["post_type"] = post_type
                    videos_dict[vid_id]["duration"] = duration
                else:
                    videos_dict[vid_id] = {
                        "id": vid_id,
                        "createTime": c_time_int,
                        "author": author,
                        "post_type": post_type,
                        "duration": duration,
                    }

            log.info(
                "[blue]API_INTERCEPT[/] +%d total=%d",
                len(items),
                len(videos_dict),
            )
        except Exception:
            pass

    def _wait_for_manual_intervention(
        self,
        page: Any,
        success_msg: str,
        allow_empty: bool = False,
        dict_ref: dict | None = None,
    ) -> None:
        log.warning("[bright_yellow]WAIT[/] max 5 min for manual intervention")

        for wr in range(MANUAL_WAIT_ROUNDS):
            page.wait_for_timeout(MANUAL_WAIT_INTERVAL_MS)
            has_links = count_video_links(page) > 0
            has_dict = allow_empty and bool(dict_ref)
            if has_links or has_dict:
                log.info(success_msg)
                break
            if (wr + 1) % 6 == 0:
                log.info("[cyan]WAIT[/] %ds/300s", (wr + 1) * 5)

    def _scroll_and_collect(
        self,
        page: Any,
        videos_dict: dict[str, dict[str, Any]],
    ) -> None:
        log.info("[cyan]SCROLL_START[/] collecting video grid")
        stale = 0
        round_num = 0

        while True:
            round_num += 1
            prev = len(videos_dict)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(SCROLL_WAIT_MS)

            for vl in collect_video_links(page):
                vid_id = vl["videoId"]
                if vid_id not in videos_dict:
                    videos_dict[vid_id] = {
                        "id": vid_id,
                        "author": vl["author"],
                        "createTime": int(datetime.now(timezone.utc).timestamp()),
                        "post_type": None,
                        "duration": 0,
                    }

            delta = len(videos_dict) - prev
            log.info(
                "[cyan]SCROLL[/] round=%d new=%d total=%d",
                round_num,
                delta,
                len(videos_dict),
            )
            if delta == 0:
                stale += 1
                if stale >= MAX_STALE_SCROLLS:
                    log.info(
                        "[cyan]SCROLL_DONE[/] %d stale rounds total=%d",
                        MAX_STALE_SCROLLS,
                        len(videos_dict),
                    )
                    break
            else:
                stale = 0

    def _enrich_from_html(
        self,
        html_content: str,
        videos_dict: dict[str, dict[str, Any]],
    ) -> None:
        soup = BeautifulSoup(html_content, "html.parser")

        if not videos_dict:
            log.info("[blue]FALLBACK[/] static HTML parse")
            for a_tag in soup.find_all("a", href=True):
                match = re.search(r"/@([^/]+)/video/(\d+)", a_tag["href"])
                if match:
                    vid_id = match.group(2)
                    if vid_id not in videos_dict:
                        videos_dict[vid_id] = {
                            "id": vid_id,
                            "author": match.group(1),
                            "createTime": int(datetime.now(timezone.utc).timestamp()),
                            "post_type": None,
                            "duration": 0,
                        }

        script_tag = soup.find(
            "script", id="__UNIVERSAL_DATA_FOR_REHYDRATION__"
        ) or soup.find("script", id="sigi-state")
        if script_tag:
            script_text = getattr(script_tag, "string", None)
            if script_text:
                try:
                    self._extract_video_nodes(
                        json.loads(script_text.strip()),
                        videos_dict,
                    )
                except Exception:
                    pass

    def extract_profile_posts(
        self,
        context: BrowserContext,
    ) -> dict[str, dict[str, Any]]:
        log.info("[blue]EXTRACT_INIT[/] target=%s", self.target_username)
        page = context.pages[0] if context.pages else context.new_page()
        profile_url = f"https://www.tiktok.com/@{self.target_username}"
        videos_dict: dict[str, dict[str, Any]] = {}
        html_content = ""

        page.on(
            "response",
            lambda r: self._handle_api_response(r, videos_dict),
        )

        try:
            log.info("[cyan]NAV[/] %s", profile_url)
            try:
                page.goto(
                    profile_url,
                    wait_until="domcontentloaded",
                    timeout=90000,
                )
            except Exception as e:
                log.warning(
                    "[bright_yellow]NAV_WARN[/] %s",
                    str(e).split("\n")[0][:120],
                )
            page.wait_for_timeout(3000)

            if is_page_blocked(page):
                log.warning("[bright_yellow]BLOCKED[/] 403/captcha detected")
                log.warning(
                    "[bright_yellow]ACTION[/] navigate to %s in Edge manually",
                    profile_url,
                )
                log.warning(
                    "[bright_yellow]ACTION[/] complete captcha/login ensure videos visible"
                )
                self._wait_for_manual_intervention(
                    page,
                    "[green]UNBLOCKED[/] videos detected after intervention",
                )

            log.info("[cyan]WAIT_SHELL[/] user profile elements")
            try:
                page.wait_for_selector(
                    '[data-e2e="user-page"], [data-e2e="user-title"], script#__UNIVERSAL_DATA_FOR_REHYDRATION__',
                    state="attached",
                    timeout=30000,
                )
            except Exception:
                log.info("[cyan]SHELL_TIMEOUT[/] proceeding")

            log.info("[cyan]CLICK_TAB[/] videos")
            try:
                tab = page.locator('[data-e2e="videos-tab"]')
                if tab.count() > 0:
                    tab.first.click(force=True)
                    log.info("[green]TAB_CLICKED[/] videos")
                    page.wait_for_timeout(3000)
            except Exception:
                pass

            log.info("[cyan]WAIT_ITEMS[/] video links in DOM")
            if count_video_links(page) == 0:
                try:
                    page.wait_for_selector(
                        'a[href*="/video/"]',
                        state="attached",
                        timeout=30000,
                    )
                except Exception:
                    log.warning(
                        "[bright_yellow]ITEMS_TIMEOUT[/] no video links after 30s"
                    )
                    page.evaluate("window.scrollTo(0, 500)")
                    page.wait_for_timeout(3000)
                    page.evaluate("window.scrollTo(0, 0)")
                    page.wait_for_timeout(3000)

            if count_video_links(page) == 0 and not videos_dict:
                log.warning("[bright_yellow]EMPTY_GRID[/] likely requires TikTok login")
                log.warning(
                    "[bright_yellow]ACTION[/] login in Edge open %s",
                    profile_url,
                )
                self._wait_for_manual_intervention(
                    page,
                    "[green]RESOLVED[/] videos detected",
                    allow_empty=True,
                    dict_ref=videos_dict,
                )

            page.wait_for_timeout(3000)
            self._scroll_and_collect(page, videos_dict)
            html_content = page.content()
        except Exception as e:
            log.error("[red]BROWSER_FAIL[/] %s", str(e).split("\n")[0][:120])
            raise RuntimeError(f"Browser extraction failed: {e}")

        self._enrich_from_html(html_content, videos_dict)
        if not videos_dict:
            dump = self.workspace_dir / f"error_dump_{self.target_username}.html"
            dump.write_text(html_content, encoding="utf-8")
            log.error("[red]ZERO_POSTS[/] profile empty or blocked")
            log.error("[red]DUMP[/] %s", dump.name)
            raise RuntimeError("Profile empty or blocked")

        log.info("[blue]EXTRACT_DONE[/] found=%d", len(videos_dict))
        return videos_dict
