import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup
from playwright.sync_api import BrowserContext

from ttdl.browser import collect_video_links, count_video_links, is_page_blocked
from ttdl.config import AppConfig
from ttdl.events import Reporter
from ttdl.utils import extract_video_nodes

log = logging.getLogger(__name__)


class ProfileExtractor:
    def __init__(self, target_username: str, config: AppConfig, reporter: "Reporter"):
        self.reporter = reporter
        self.target_username = target_username
        self.config = config
        self.workspace_dir = config.workspace_dir
        self.logs_dir = config.logs_dir

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

            self.reporter.info(
                "API_INTERCEPT +%d total=%d",
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
        self.reporter.warning("WAIT max 5 min for manual intervention")

        for wr in range(self.config.manual_wait_rounds):
            page.wait_for_timeout(self.config.manual_wait_interval_ms)
            has_links = count_video_links(page) > 0
            has_dict = allow_empty and bool(dict_ref)
            if has_links or has_dict:
                self.reporter.info(success_msg)
                break
            if (wr + 1) % 6 == 0:
                self.reporter.info(
                    "WAIT %ds/%ds",
                    (wr + 1) * (self.config.manual_wait_interval_ms // 1000),
                    self.config.manual_wait_rounds
                    * (self.config.manual_wait_interval_ms // 1000),
                )

    def _scroll_and_collect(
        self,
        page: Any,
        videos_dict: dict[str, dict[str, Any]],
    ) -> None:
        self.reporter.info("SCROLL_START collecting video grid")
        stale = 0
        round_num = 0

        while True:
            round_num += 1
            prev = len(videos_dict)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(self.config.scroll_wait_ms)

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
            self.reporter.info(
                "SCROLL round=%d new=%d total=%d",
                round_num,
                delta,
                len(videos_dict),
            )
            if delta == 0:
                stale += 1
                if stale >= self.config.max_stale_scrolls:
                    self.reporter.info(
                        "SCROLL_DONE %d stale rounds total=%d",
                        self.config.max_stale_scrolls,
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
            self.reporter.info("FALLBACK static HTML parse")
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
                    extract_video_nodes(
                        json.loads(script_text.strip()),
                        videos_dict,
                    )
                except Exception:
                    pass

    def extract_profile_posts(
        self,
        context: BrowserContext,
    ) -> dict[str, dict[str, Any]]:
        self.reporter.info("EXTRACT_INIT target=%s", self.target_username)
        page = context.pages[0] if context.pages else context.new_page()
        profile_url = f"https://www.tiktok.com/@{self.target_username}"
        videos_dict: dict[str, dict[str, Any]] = {}
        html_content = ""

        page.on(
            "response",
            lambda r: self._handle_api_response(r, videos_dict),
        )

        try:
            self.reporter.info("NAV %s", profile_url)
            try:
                page.goto(
                    profile_url,
                    wait_until="domcontentloaded",
                    timeout=90000,
                )
            except Exception as e:
                self.reporter.warning(
                    "NAV_WARN %s",
                    str(e).split("\n")[0][:120],
                )
            page.wait_for_timeout(3000)

            if is_page_blocked(page):
                self.reporter.warning("BLOCKED 403/captcha detected")
                self.reporter.warning(
                    "ACTION navigate to %s in your browser manually",
                    profile_url,
                )
                self.reporter.warning(
                    "ACTION complete captcha/login ensure videos visible"
                )
                self._wait_for_manual_intervention(
                    page,
                    "[green]UNBLOCKED videos detected after intervention",
                )

            self.reporter.info("WAIT_SHELL user profile elements")
            try:
                page.wait_for_selector(
                    '[data-e2e="user-page"], [data-e2e="user-title"], script#__UNIVERSAL_DATA_FOR_REHYDRATION__',
                    state="attached",
                    timeout=30000,
                )
            except Exception:
                self.reporter.info("SHELL_TIMEOUT proceeding")

            self.reporter.info("CLICK_TAB videos")
            try:
                tab = page.locator('[data-e2e="videos-tab"]')
                if tab.count() > 0:
                    tab.first.click(force=True)
                    self.reporter.info("TAB_CLICKED videos")
                    page.wait_for_timeout(3000)
            except Exception:
                pass

            self.reporter.info("WAIT_ITEMS video links in DOM")
            if count_video_links(page) == 0:
                try:
                    page.wait_for_selector(
                        'a[href*="/video/"]',
                        state="attached",
                        timeout=30000,
                    )
                except Exception:
                    self.reporter.warning("ITEMS_TIMEOUT no video links after 30s")
                    page.evaluate("window.scrollTo(0, 500)")
                    page.wait_for_timeout(3000)
                    page.evaluate("window.scrollTo(0, 0)")
                    page.wait_for_timeout(3000)

            if count_video_links(page) == 0 and not videos_dict:
                self.reporter.warning("EMPTY_GRID likely requires TikTok login")
                self.reporter.warning(
                    "ACTION login in your browser open %s",
                    profile_url,
                )
                self._wait_for_manual_intervention(
                    page,
                    "[green]RESOLVED videos detected",
                    allow_empty=True,
                    dict_ref=videos_dict,
                )

            page.wait_for_timeout(3000)
            self._scroll_and_collect(page, videos_dict)
            html_content = page.content()
        except Exception as e:
            self.reporter.error("BROWSER_FAIL %s", str(e).split("\n")[0][:120])
            raise RuntimeError(f"Browser extraction failed: {e}")

        self._enrich_from_html(html_content, videos_dict)
        if not videos_dict:
            dump = self.workspace_dir / f"error_dump_{self.target_username}.html"
            dump.write_text(html_content, encoding="utf-8")
            self.reporter.error("ZERO_POSTS profile empty or blocked")
            self.reporter.error("DUMP %s", dump.name)
            raise RuntimeError("Profile empty or blocked")

        self.reporter.info("EXTRACT_DONE found=%d", len(videos_dict))
        return videos_dict
