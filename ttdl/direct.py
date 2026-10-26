import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Final

import requests
from bs4 import BeautifulSoup

from ttdl.config import AppConfig
from ttdl.downloader import MediaDownloader
from ttdl.events import Reporter
from ttdl.models import PostItem
from ttdl.scraper import IMAGE_POST_MARKER, MusicalDownScraper
from ttdl.utils import SNOWFLAKE_TIMESTAMP_SHIFT, extract_video_nodes

log = logging.getLogger(__name__)

DEFAULT_UA: Final[str] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/127.0.0.0 Safari/537.36 Edg/127.0.0.0"
)
TIKTOK_ITEM_REGEX: Final[re.Pattern[str]] = re.compile(r"/(?:video|photo)/(\d+)")
TIKTOK_USER_REGEX: Final[re.Pattern[str]] = re.compile(r"/@([^/]+)")
RESOLVER_MAX_RETRIES: Final[int] = 3
RESOLVER_TIMEOUT: Final[int] = 30
RETRY_BACKOFF_SECONDS: Final[float] = 2.0


def _extract_url_identifiers(final_url: str) -> tuple[str, str]:
    item_match = TIKTOK_ITEM_REGEX.search(final_url)
    if not item_match:
        raise ValueError("Invalid TikTok URL format")

    video_id = item_match.group(1)
    user_match = TIKTOK_USER_REGEX.search(final_url)
    username = user_match.group(1) if user_match else "unknown_user"
    return video_id, username


def _extract_html_hydration(html: str) -> dict[str, dict[str, Any]]:
    collection: dict[str, dict[str, Any]] = {}
    soup = BeautifulSoup(html, "html.parser")
    script_tag = soup.find(
        "script", id="__UNIVERSAL_DATA_FOR_REHYDRATION__"
    ) or soup.find("script", id="sigi-state")

    if script_tag:
        script_str = getattr(script_tag, "string", None)
        if script_str:
            try:
                extract_video_nodes(json.loads(script_str.strip()), collection)
            except Exception:
                pass

    return collection


class UrlResolver:
    def __init__(self, url: str) -> None:
        self.url = url
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": DEFAULT_UA})

    def resolve(self) -> dict[str, Any]:
        resp: requests.Response | None = None
        last_err: Exception | None = None

        for attempt in range(1, RESOLVER_MAX_RETRIES + 1):
            try:
                resp = self.session.get(
                    self.url, allow_redirects=True, timeout=RESOLVER_TIMEOUT
                )
                resp.raise_for_status()
                break
            except Exception as e:
                last_err = e
                log.warning("UrlResolver attempt %d failed: %s", attempt, e)
                if attempt < RESOLVER_MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_SECONDS)

        if not resp:
            raise ValueError(
                f"Failed to resolve URL after {RESOLVER_MAX_RETRIES} attempts: {last_err}"
            )

        final_url = resp.url
        video_id, username = _extract_url_identifiers(final_url)
        collection = _extract_html_hydration(resp.text)

        create_time = 0
        post_type = "video"
        if video_id in collection:
            create_time = collection[video_id].get("createTime", 0)
            post_type = collection[video_id].get("post_type", "video")

        if not create_time and video_id.isdigit():
            create_time = int(video_id) >> SNOWFLAKE_TIMESTAMP_SHIFT

        return {
            "video_id": video_id,
            "username": username,
            "createTime": create_time,
            "post_type": post_type,
            "final_url": final_url,
        }


class DirectDownloader:
    def __init__(self, config: AppConfig, reporter: Reporter, urls: list[str]) -> None:
        self.config = config
        self.reporter = reporter
        self.urls = urls

    def _process_item_media(
        self,
        data: dict[str, Any],
        output_dir: Path,
        dl: MediaDownloader,
        scraper: MusicalDownScraper,
    ) -> None:
        video_id = data["video_id"]
        username = data["username"]
        create_time = data["createTime"]
        post_type = data["post_type"]
        final_url = data["final_url"]

        if post_type == "video":
            pi = PostItem.create(
                video_id,
                username,
                create_time,
                output_dir,
                is_photo=False,
            )

            if not pi.output_path.exists():
                dl_url = scraper.fetch_musicaldown_link(final_url)
                if dl_url == IMAGE_POST_MARKER:
                    post_type = "photo"
                else:
                    dl.download_and_process_video(pi, dl_url)
            else:
                self.reporter.debug("SKIP %s already exists", video_id)

        if post_type == "photo":
            photos = scraper.fetch_musicaldown_photos(final_url, video_id, create_time)
            if not photos:
                raise ValueError(f"No photo links found for {video_id}")
            for ph in photos:
                dl.download_photo(ph)

    def execute(self) -> int:
        overall_failed = False

        for url in self.urls:
            self.reporter.debug("DIRECT Resolving URL %s", url)
            try:
                resolver = UrlResolver(url)
                data = resolver.resolve()
            except Exception as e:
                self.reporter.error("RESOLVE_FAIL %s", str(e))
                overall_failed = True
                continue

            username = data["username"]
            self.reporter.info(
                "RESOLVED user=%s id=%s type=%s",
                username,
                data["video_id"],
                data["post_type"],
            )

            output_dir = self.config.downloads_dir / username
            output_dir.mkdir(parents=True, exist_ok=True)

            dl = MediaDownloader(self.config, self.reporter)
            scraper = MusicalDownScraper(self.config, output_dir, self.reporter)

            failed = True
            while failed:
                failed = False
                with self.reporter:
                    try:
                        self._process_item_media(data, output_dir, dl, scraper)
                    except Exception as e:
                        self.reporter.error("FAIL %s", str(e))
                        failed = True

                if failed and not self.reporter.ask_retry():
                    overall_failed = True
                    break

        if overall_failed:
            return 1

        self.reporter.info("EXEC_DONE Direct download complete")
        return 0
