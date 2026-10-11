import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from bs4 import BeautifulSoup
from playwright.sync_api import BrowserContext

from ttdl.browser import collect_video_links, count_video_links, is_page_blocked
from ttdl.config import AppConfig
from ttdl.events import Reporter
from ttdl.utils import extract_video_nodes

log = logging.getLogger(__name__)


class ProfileExtractor:
    def __init__(
        self,
        target_username: str,
        config: AppConfig,
        reporter: "Reporter",
        usejson: bool = False,
        force: bool = False,
    ):
        self.reporter = reporter
        self.target_username = target_username.lstrip("@")
        self.config = config
        self.workspace_dir = config.workspace_dir
        self.logs_dir = config.logs_dir
        self.usejson = usejson
        self.force = force
        self.json_dir = self.config.json_dir
        self.json_dir.mkdir(parents=True, exist_ok=True)
        self.json_path = self.json_dir / f"{self.target_username}.json"

    def _is_target_author(self, author_name: str | None) -> bool:
        if not author_name:
            return True
        return author_name.lstrip("@").lower() == self.target_username.lower()

    def _handle_api_response(
        self,
        response: Any,
        videos_dict: dict[str, dict[str, Any]],
    ) -> None:
        try:
            url = response.url
            if any(
                ign in url
                for ign in ["/recommend/", "/related/", "/explore/", "/feed/"]
            ):
                return

            api_patterns = [
                "/api/post/item_list",
                "/api/story/item_list",
                "/api/creator/item_list",
                "/api/mix/item_list",
                "/api/playlist/item_list",
                "/api/user/playlist",
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

                author = item.get("author")
                author_name = ""
                if isinstance(author, dict):
                    author_name = str(
                        author.get("uniqueId") or author.get("unique_id") or ""
                    )
                elif isinstance(author, str):
                    author_name = author

                if author_name and not self._is_target_author(author_name):
                    self.reporter.info(
                        "AUTHOR_SKIP id=%s author=%s (target=%s)",
                        vid_id,
                        author_name,
                        self.target_username,
                    )
                    continue

                c_time = item.get("createTime") or item.get("create_time") or 0
                post_type = "photo" if "imagePost" in item else "video"
                duration = 0
                vid_data = item.get("video")
                if isinstance(vid_data, dict):
                    duration = int(vid_data.get("duration", 0))

                c_time_int = int(c_time) if c_time else 0
                if not c_time_int and vid_id.isdigit():
                    c_time_int = int(vid_id) >> 32

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
        dict_ref: dict[str, dict[str, Any]] | None = None,
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
        cached_ids: set[str],
    ) -> None:
        self.reporter.info("SCROLL_START collecting video grid")
        stale = 0
        round_num = 0

        while True:
            round_num += 1
            prev = len(videos_dict)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(self.config.scroll_wait_ms)

            consecutive_known = 0
            smart_stop_triggered = False

            for vl in collect_video_links(page, self.target_username):
                author = vl.get("author", "")
                if author and not self._is_target_author(author):
                    self.reporter.info(
                        "AUTHOR_SKIP id=%s author=%s (target=%s)",
                        vl.get("videoId"),
                        author,
                        self.target_username,
                    )
                    continue
                vid_id = vl["videoId"]
                if vid_id not in videos_dict:
                    videos_dict[vid_id] = {
                        "id": vid_id,
                        "author": vl["author"],
                        "createTime": (
                            int(vid_id) >> 32
                            if vid_id.isdigit()
                            else int(datetime.now(UTC).timestamp())
                        ),
                        "post_type": None,
                        "duration": 0,
                    }
                    if not self.force:
                        consecutive_known = 0
                elif vid_id in cached_ids:
                    if not self.force:
                        consecutive_known += 1
                        if consecutive_known >= 5:
                            smart_stop_triggered = True
                            break
                else:
                    if not self.force:
                        consecutive_known = 0

            if smart_stop_triggered:
                self.reporter.info("SCROLL_STOP consecutive known videos detected")
                break

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
                match = re.search(
                    r"/@([^/]+)/(?:video|photo|story)/(\d+)",
                    str(a_tag["href"] or ""),
                )
                if match:
                    author_match = match.group(1)
                    if not self._is_target_author(author_match):
                        continue
                    vid_id = match.group(2)
                    if vid_id not in videos_dict:
                        videos_dict[vid_id] = {
                            "id": vid_id,
                            "author": author_match,
                            "createTime": (
                                int(vid_id) >> 32
                                if vid_id.isdigit()
                                else int(datetime.now(UTC).timestamp())
                            ),
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
                    raw_data = json.loads(script_text.strip())
                    user_scope = (
                        raw_data.get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail")
                        if isinstance(raw_data, dict)
                        else None
                    )
                    data_to_extract = user_scope if user_scope else raw_data
                    extract_video_nodes(
                        data_to_extract,
                        videos_dict,
                        self.target_username,
                    )
                except Exception:
                    pass

    def extract_profile_posts(
        self,
        context: BrowserContext | None,
    ) -> dict[str, dict[str, Any]]:
        self.reporter.info("EXTRACT_INIT target=%s", self.target_username)
        videos_dict: dict[str, dict[str, Any]] = {}
        cached_ids: set[str] = set()

        if self.json_path.exists():
            try:
                with open(self.json_path, encoding="utf-8") as f:
                    videos_dict = json.load(f)
                cached_ids = set(videos_dict.keys())
                self.reporter.info("CACHE_LOAD found %d posts", len(videos_dict))
            except Exception as e:
                self.reporter.error("CACHE_FAIL %s", str(e))
                videos_dict = {}

        if self.usejson:
            return videos_dict

        if context is None:
            raise RuntimeError("BrowserContext is None but usejson is False")

        page = context.pages[0] if context.pages else context.new_page()
        profile_url = f"https://www.tiktok.com/@{self.target_username}"
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
                        'a[href*="/video/"], a[href*="/photo/"], a[href*="/story/"]',
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
            self._scroll_and_collect(page, videos_dict, cached_ids)
            html_content = page.content()
        except Exception as e:
            self.reporter.error("BROWSER_FAIL %s", str(e).split("\n")[0][:120])
            raise RuntimeError(f"Browser extraction failed: {e}")

        self._enrich_from_html(html_content, videos_dict)
        if not videos_dict:
            dump = self.logs_dir / f"error_dump_{self.target_username}.html"
            dump.write_text(html_content, encoding="utf-8")
            self.reporter.error("ZERO_POSTS profile empty or blocked")
            self.reporter.error("DUMP %s", dump)
            raise RuntimeError("Profile empty or blocked")

        try:
            with open(self.json_path, "w", encoding="utf-8") as f:
                json.dump(videos_dict, f, indent=4)
        except Exception as e:
            self.reporter.error("CACHE_SAVE_FAIL %s", str(e))

        self.reporter.info("EXTRACT_DONE found=%d", len(videos_dict))
        return videos_dict
