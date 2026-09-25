import json
import logging
import re
from typing import Any

import requests
from bs4 import BeautifulSoup

from ttdl.config import AppConfig
from ttdl.downloader import MediaDownloader
from ttdl.events import Reporter
from ttdl.models import PostItem
from ttdl.scraper import MusicalDownScraper
from ttdl.utils import extract_video_nodes

log = logging.getLogger(__name__)


class UrlResolver:
    def __init__(self, url: str) -> None:
        self.url = url
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/127.0.0.0 Safari/537.36 Edg/127.0.0.0"
                )
            }
        )

    def resolve(self) -> dict[str, Any]:
        resp = self.session.get(self.url, allow_redirects=True, timeout=15)
        resp.raise_for_status()
        final_url = resp.url
        html = resp.text

        match = re.search(r"/(?:video|photo)/(\d+)", final_url)
        if not match:
            raise ValueError("Invalid TikTok URL format")
        video_id = match.group(1)

        user_match = re.search(r"/@([^/]+)", final_url)
        username = user_match.group(1) if user_match else "unknown_user"

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

        create_time = 0
        post_type = "video"
        if video_id in collection:
            create_time = collection[video_id].get("createTime", 0)
            post_type = collection[video_id].get("post_type", "video")

        return {
            "video_id": video_id,
            "username": username,
            "createTime": create_time,
            "post_type": post_type,
            "final_url": final_url,
        }


class DirectDownloader:
    def __init__(self, config: AppConfig, reporter: Reporter, url: str) -> None:
        self.config = config
        self.reporter = reporter
        self.url = url

    def execute(self) -> int:
        self.reporter.info("DIRECT Resolving URL %s", self.url)
        try:
            resolver = UrlResolver(self.url)
            data = resolver.resolve()
        except Exception as e:
            self.reporter.error("RESOLVE_FAIL %s", str(e))
            return 1

        video_id = data["video_id"]
        username = data["username"]
        create_time = data["createTime"]
        post_type = data["post_type"]
        final_url = data["final_url"]

        self.reporter.info(
            "RESOLVED user=%s id=%s type=%s", username, video_id, post_type
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
                    if post_type == "video":
                        pi = PostItem.create(
                            video_id, username, create_time, output_dir, is_photo=False
                        )
                        if not pi.output_path.exists():
                            dl_url = scraper.fetch_musicaldown_link(final_url)
                            if dl_url == "__IMAGE_POST__":
                                post_type = "photo"
                            else:
                                dl.download_and_process_video(pi, dl_url)
                        else:
                            self.reporter.info("SKIP %s already exists", video_id)

                    if post_type == "photo":
                        photos = scraper.fetch_musicaldown_photos(
                            final_url, video_id, create_time
                        )
                        if not photos:
                            failed = True
                            continue
                        for ph in photos:
                            dl.download_photo(ph)
                except Exception as e:
                    self.reporter.error("FAIL %s", str(e))
                    failed = True

            if failed and not self.reporter.ask_retry():
                break

        if failed:
            return 1

        self.reporter.info("EXEC_DONE Direct download complete")
        return 0
