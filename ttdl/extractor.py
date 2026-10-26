import json
import logging
import re
from datetime import UTC, datetime
from typing import Any, Final

from bs4 import BeautifulSoup
from playwright.sync_api import BrowserContext

from ttdl.browser import collect_video_links, count_video_links, is_page_blocked
from ttdl.config import AppConfig
from ttdl.events import Reporter
from ttdl.utils import SNOWFLAKE_TIMESTAMP_SHIFT, extract_video_nodes

log = logging.getLogger(__name__)

API_IGNORE_PATTERNS: Final[tuple[str, ...]] = (
    "/recommend/",
    "/related/",
    "/explore/",
    "/feed/",
)
API_ITEM_PATTERNS: Final[tuple[str, ...]] = (
    "/api/post/item_list",
    "/api/story/item_list",
    "/api/creator/item_list",
    "/api/mix/item_list",
    "/api/playlist/item_list",
    "/api/user/playlist",
)
SELECTOR_USER_SHELL: Final[str] = (
    '[data-e2e="user-page"], [data-e2e="user-title"], '
    "script#__UNIVERSAL_DATA_FOR_REHYDRATION__"
)
SELECTOR_VIDEOS_TAB: Final[str] = '[data-e2e="videos-tab"]'
SELECTOR_VIDEO_LINKS: Final[str] = (
    'a[href*="/video/"], a[href*="/photo/"], a[href*="/story/"]'
)
HTML_ITEM_REGEX: Final[re.Pattern[str]] = re.compile(
    r"/@([^/]+)/(?:video|photo|story)/(\d+)"
)
CONSECUTIVE_KNOWN_THRESHOLD: Final[int] = 5
GOTO_TIMEOUT_MS: Final[int] = 90000
SELECTOR_TIMEOUT_MS: Final[int] = 30000


def _is_target_author(author_name: str | None, target_username: str) -> bool:
    if not author_name:
        return True

    return author_name.lstrip("@").lower() == target_username.lower()


def _extract_items_from_api_payload(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []

    items = data.get("itemList") or data.get("items") or []
    if isinstance(items, list) and items:
        return [it for it in items if isinstance(it, dict)]

    for val in data.values():
        if isinstance(val, list) and val:
            first = val[0]
            if isinstance(first, dict) and ("id" in first or "video" in first):
                return [it for it in val if isinstance(it, dict)]

    return []


def _parse_api_item_data(
    item: dict[str, Any], target_username: str
) -> tuple[str, dict[str, Any]] | None:
    vid_id = str(item.get("id", item.get("item_id", item.get("video_id", ""))))
    if not (vid_id.isdigit() and len(vid_id) >= 15):
        return None

    author_val = item.get("author")
    author_name = ""
    if isinstance(author_val, dict):
        author_name = str(
            author_val.get("uniqueId") or author_val.get("unique_id") or ""
        )

    elif isinstance(author_val, str):
        author_name = author_val

    if author_name and not _is_target_author(author_name, target_username):
        return None

    c_time = item.get("createTime") or item.get("create_time") or 0
    post_type = "photo" if "imagePost" in item else "video"
    duration = 0
    vid_data = item.get("video")
    if isinstance(vid_data, dict):
        duration = int(vid_data.get("duration", 0))

    c_time_int = int(c_time) if c_time else 0
    if not c_time_int and vid_id.isdigit():
        c_time_int = int(vid_id) >> SNOWFLAKE_TIMESTAMP_SHIFT

    record = {
        "id": vid_id,
        "createTime": c_time_int,
        "author": author_val,
        "post_type": post_type,
        "duration": duration,
    }
    return vid_id, record


class ProfileExtractor:
    def __init__(
        self,
        target_username: str,
        config: AppConfig,
        reporter: "Reporter",
        usejson: bool = False,
        force: bool = False,
    ) -> None:
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

    def _is_target(self, author_name: str | None) -> bool:
        return _is_target_author(author_name, self.target_username)

    def _handle_api_response(
        self,
        response: Any,
        videos_dict: dict[str, dict[str, Any]],
    ) -> None:
        try:
            url: str = response.url
            if any(ign in url for ign in API_IGNORE_PATTERNS):
                return
            if not any(p in url for p in API_ITEM_PATTERNS):
                return
            if response.status != 200:
                return

            data = json.loads(response.body())
            items = _extract_items_from_api_payload(data)
            if not items:
                return

            added_count = 0
            for item in items:
                parsed = _parse_api_item_data(item, self.target_username)
                if not parsed:
                    continue

                vid_id, record = parsed
                if vid_id in videos_dict:
                    if record["createTime"]:
                        videos_dict[vid_id]["createTime"] = record["createTime"]

                    if record["author"]:
                        videos_dict[vid_id]["author"] = record["author"]

                    videos_dict[vid_id]["post_type"] = record["post_type"]
                    videos_dict[vid_id]["duration"] = record["duration"]
                else:
                    videos_dict[vid_id] = record
                    added_count += 1

            self.reporter.debug(
                "API_INTERCEPT +%d total=%d", len(items), len(videos_dict)
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
        interval_s = self.config.manual_wait_interval_ms // 1000

        for wr in range(self.config.manual_wait_rounds):
            page.wait_for_timeout(self.config.manual_wait_interval_ms)
            has_links = count_video_links(page) > 0
            has_dict = allow_empty and bool(dict_ref)
            if has_links or has_dict:
                self.reporter.info(success_msg)
                break

            if (wr + 1) % 6 == 0:
                elapsed_s = (wr + 1) * interval_s
                total_s = self.config.manual_wait_rounds * interval_s
                self.reporter.info("WAIT %ds/%ds", elapsed_s, total_s)

    def _scroll_and_collect(
        self,
        page: Any,
        videos_dict: dict[str, dict[str, Any]],
        cached_ids: set[str],
    ) -> None:
        self.reporter.debug("SCROLL_START collecting video grid")
        stale = 0
        round_num = 0

        try:
            while True:
                round_num += 1
                self.reporter.set_status(
                    f"Collecting @{self.target_username} posts: {len(videos_dict)} found (scroll {round_num})"
                )
                prev = len(videos_dict)
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(self.config.scroll_wait_ms)

                consecutive_known = 0
                smart_stop = False

                for vl in collect_video_links(page, self.target_username):
                    author = vl.get("author", "")
                    if author and not self._is_target(author):
                        continue

                    vid_id = vl["videoId"]
                    if vid_id not in videos_dict:
                        videos_dict[vid_id] = {
                            "id": vid_id,
                            "author": vl["author"],
                            "createTime": (
                                int(vid_id) >> SNOWFLAKE_TIMESTAMP_SHIFT
                                if vid_id.isdigit()
                                else int(datetime.now(UTC).timestamp())
                            ),
                            "post_type": None,
                            "duration": 0,
                        }
                        if not self.force:
                            consecutive_known = 0

                    elif vid_id in cached_ids and not self.force:
                        consecutive_known += 1
                        if consecutive_known >= CONSECUTIVE_KNOWN_THRESHOLD:
                            smart_stop = True
                            break

                    elif not self.force:
                        consecutive_known = 0

                if smart_stop:
                    self.reporter.debug("SCROLL_STOP consecutive known videos detected")
                    break

                delta = len(videos_dict) - prev
                self.reporter.debug(
                    "SCROLL round=%d new=%d total=%d",
                    round_num,
                    delta,
                    len(videos_dict),
                )

                if delta == 0:
                    stale += 1
                    if stale >= self.config.max_stale_scrolls:
                        self.reporter.debug(
                            "SCROLL_DONE %d stale rounds total=%d",
                            self.config.max_stale_scrolls,
                            len(videos_dict),
                        )
                        break
                else:
                    stale = 0
        finally:
            self.reporter.clear_status()

    def _enrich_from_html(
        self,
        html_content: str,
        videos_dict: dict[str, dict[str, Any]],
    ) -> None:
        soup = BeautifulSoup(html_content, "html.parser")

        if not videos_dict:
            self.reporter.info("FALLBACK static HTML parse")

            for a_tag in soup.find_all("a", href=True):
                match = HTML_ITEM_REGEX.search(str(a_tag["href"] or ""))
                if not match:
                    continue

                author_match, vid_id = match.group(1), match.group(2)
                if not self._is_target(author_match):
                    continue

                if vid_id not in videos_dict:
                    videos_dict[vid_id] = {
                        "id": vid_id,
                        "author": author_match,
                        "createTime": (
                            int(vid_id) >> SNOWFLAKE_TIMESTAMP_SHIFT
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

    def _navigate_profile(self, page: Any, profile_url: str) -> None:
        self.reporter.debug("NAV %s", profile_url)
        try:
            page.goto(
                profile_url,
                wait_until="domcontentloaded",
                timeout=GOTO_TIMEOUT_MS,
            )
        except Exception as e:
            self.reporter.warning("NAV_WARN %s", str(e).split("\n")[0][:120])
        page.wait_for_timeout(3000)

        if is_page_blocked(page):
            self.reporter.warning("BLOCKED 403/captcha detected")
            self.reporter.warning(
                "ACTION navigate to %s in your browser manually", profile_url
            )
            self.reporter.warning("ACTION complete captcha/login ensure videos visible")
            self._wait_for_manual_intervention(
                page,
                "[green]UNBLOCKED videos detected after intervention",
            )

        self.reporter.debug("WAIT_SHELL user profile elements")
        try:
            page.wait_for_selector(
                SELECTOR_USER_SHELL,
                state="attached",
                timeout=SELECTOR_TIMEOUT_MS,
            )
        except Exception:
            self.reporter.debug("SHELL_TIMEOUT proceeding")

        try:
            tab = page.locator(SELECTOR_VIDEOS_TAB)
            if tab.count() > 0:
                tab.first.click(force=True)
                self.reporter.debug("TAB_CLICKED videos")
                page.wait_for_timeout(3000)
        except Exception:
            pass

    def _ensure_items_visible(
        self,
        page: Any,
        profile_url: str,
        videos_dict: dict[str, dict[str, Any]],
    ) -> None:
        self.reporter.debug("WAIT_ITEMS video links in DOM")
        if count_video_links(page) == 0:
            try:
                page.wait_for_selector(
                    SELECTOR_VIDEO_LINKS,
                    state="attached",
                    timeout=SELECTOR_TIMEOUT_MS,
                )
            except Exception:
                self.reporter.warning("ITEMS_TIMEOUT no video links after 30s")
                page.evaluate("window.scrollTo(0, 500)")
                page.wait_for_timeout(3000)
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(3000)

        if count_video_links(page) == 0 and not videos_dict:
            self.reporter.warning("EMPTY_GRID likely requires TikTok login")
            self.reporter.warning("ACTION login in your browser open %s", profile_url)
            self._wait_for_manual_intervention(
                page,
                "[green]RESOLVED videos detected",
                allow_empty=True,
                dict_ref=videos_dict,
            )

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

        page.on("response", lambda r: self._handle_api_response(r, videos_dict))

        try:
            self._navigate_profile(page, profile_url)
            self._ensure_items_visible(page, profile_url, videos_dict)
            page.wait_for_timeout(3000)
            self._scroll_and_collect(page, videos_dict, cached_ids)
            html_content = page.content()
        except Exception as e:
            self.reporter.error("BROWSER_FAIL %s", str(e).split("\n")[0][:120])
            raise RuntimeError(f"Browser extraction failed: {e}") from e

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
