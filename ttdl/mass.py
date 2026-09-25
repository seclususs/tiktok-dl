import logging
from datetime import datetime, timezone
from typing import Any

from playwright.sync_api import BrowserContext, sync_playwright

from ttdl.browser import detect_browser
from ttdl.config import AppConfig
from ttdl.downloader import MediaDownloader
from ttdl.events import Reporter
from ttdl.extractor import ProfileExtractor
from ttdl.models import DateFilter, PostItem
from ttdl.scraper import MusicalDownScraper

log = logging.getLogger(__name__)


class TikTokDownloader:
    def __init__(
        self,
        config: AppConfig,
        target_username: str,
        reporter: "Reporter",
        mode: str = "all",
        date_filter: DateFilter | None = None,
    ) -> None:
        self.config = config
        self.reporter = reporter
        self.target_username = target_username.lstrip("@")
        self.mode = mode
        self.date_filter = date_filter
        self.browser_name, default_path = detect_browser()
        self.browser_path = self.config.browser_executable or default_path
        self.session_dir = self.config.session_dir / self.browser_name
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir = self.config.downloads_dir / self.target_username
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _apply_date_filter(
        self,
        posts: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        if not self.date_filter:
            return posts

        filtered: dict[str, dict[str, Any]] = {}
        for k, v in posts.items():
            ct = v.get("createTime", 0)
            if ct == 0:
                filtered[k] = v
                continue
            dt = datetime.fromtimestamp(ct, tz=timezone.utc)
            if self.date_filter.matches(dt):
                filtered[k] = v

        self.reporter.info(
            "DATE_FILTER %d->%d posts in range",
            len(posts),
            len(filtered),
        )
        return filtered

    def _browser_user_agent(self) -> str:
        if self.browser_name == "edge":
            return (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                " AppleWebKit/537.36 Chrome/127.0.0.0"
                " Safari/537.36 Edg/127.0.0.0"
            )
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
            " AppleWebKit/537.36 Chrome/127.0.0.0"
            " Safari/537.36"
        )

    def _launch_browser(self, p: Any) -> BrowserContext:
        self.reporter.info(
            "BROWSER_INIT browser=%s session=%s",
            self.browser_name,
            self.session_dir.name,
        )
        self.reporter.info("BROWSER_PATH %s", self.browser_path)

        context: BrowserContext = p.chromium.launch_persistent_context(
            user_data_dir=str(self.session_dir),
            executable_path=self.browser_path,
            headless=False,
            user_agent=self._browser_user_agent(),
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
                "--window-position=100,100",
                "--window-size=1280,900",
                "--no-first-run",
                "--force-device-scale-factor=1",
            ],
            ignore_default_args=[
                "--enable-automation",
            ],
            no_viewport=True,
        )

        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        return context

    def _build_post_url(
        self,
        post_id: str,
        data: dict[str, Any],
    ) -> str:
        author = data.get("author")
        author_str = self.target_username
        if isinstance(author, dict) and "uniqueId" in author:
            author_str = author["uniqueId"]
        elif isinstance(author, str) and author.strip():
            author_str = author

        return f"https://www.tiktok.com/@{author_str}/video/{post_id}"

    def _build_video_list(
        self,
        posts: dict[str, dict[str, Any]],
    ) -> list[PostItem]:
        videos: list[PostItem] = []
        for vid_id, data in posts.items():
            author = data.get("author")
            author_str = self.target_username
            if isinstance(author, dict) and "uniqueId" in author:
                author_str = author["uniqueId"]
            elif isinstance(author, str) and author.strip():
                author_str = author

            create_time = data.get("createTime", 0)
            videos.append(
                PostItem.create(
                    video_id=vid_id,
                    username=author_str,
                    create_time=create_time,
                    output_dir=self.output_dir,
                    is_photo=False,
                )
            )

        return videos

    def _process_single_video(self, video, do_photo, scraper, dl):
        try:
            dl_url = scraper.fetch_musicaldown_link(video.url)
            if dl_url == "__IMAGE_POST__":
                if not do_photo:
                    self.reporter.info("IMG_SKIP %s image post", video.video_id)
                    return 0, 0, 1, []

                photos = scraper.fetch_musicaldown_photos(
                    video.url,
                    video.video_id,
                    video.create_time,
                )
                photos_done = 0
                for ph in photos:
                    if dl.download_photo(ph):
                        photos_done += 1
                return 0, photos_done, 0, []

            dl.download_and_process_video(video, dl_url)
            return 1, 0, 0, []
        except Exception as e:
            msg = str(e).split("\n")[0][:120]
            self.reporter.error("FAIL %s %s", video.video_id, msg)
            return 0, 0, 0, [video]

    def _process_single_photo_post(self, photo_post, scraper, dl):
        try:
            photos = scraper.fetch_musicaldown_photos(
                photo_post.url,
                photo_post.video_id,
                photo_post.create_time,
            )
            if not photos:
                return 0, 1, []

            all_exist = all(ph.output_path.exists() for ph in photos)
            if all_exist:
                return 0, 1, []

            photos_done = 0
            for ph in photos:
                if dl.download_photo(ph):
                    photos_done += 1
            return photos_done, 0, []
        except Exception as e:
            msg = str(e).split("\n")[0][:120]
            self.reporter.error("FAIL %s %s", photo_post.video_id, msg)
            return 0, 0, [photo_post]

    def _run_pass(self, items, do_video, scraper, dl):
        videos_done = 0
        photos_done = 0
        skipped = 0
        failed_items = []

        total = len(items)
        for idx, item in enumerate(items, 1):
            if item.output_path.exists() and not item.is_photo:
                skipped += 1
                continue

            self.reporter.info("PROCESS %d/%d id=%s", idx, total, item.video_id)

            if not item.is_photo:
                vd, pd, sd, fl = self._process_single_video(
                    item, do_photo=True, scraper=scraper, dl=dl
                )
                videos_done += vd
                photos_done += pd
                skipped += sd
                failed_items.extend(fl)
            else:
                pd, sd, fl = self._process_single_photo_post(item, scraper, dl)
                photos_done += pd
                skipped += sd
                failed_items.extend(fl)
        return videos_done, photos_done, skipped, failed_items

    def _run(
        self,
        do_video: bool,
        do_photo: bool,
    ) -> int:
        self.reporter.info(
            "EXEC_START target=%s mode=%s",
            self.target_username,
            self.mode,
        )

        if self.date_filter:
            self.reporter.info("DATE_RANGE %s", self.date_filter.describe())

        dl = MediaDownloader(self.config, self.reporter)
        scraper = MusicalDownScraper(self.config, self.output_dir, self.reporter)
        extractor = ProfileExtractor(self.target_username, self.config, self.reporter)

        if do_video:
            dl.purge_low_res(self.output_dir)

        with sync_playwright() as p:
            try:
                context = self._launch_browser(p)
            except Exception as e:
                self.reporter.error("LAUNCH_FAIL %s", str(e).split("\n")[0][:120])
                self.reporter.error("ENSURE Chrome/Edge is installed and fully closed")
                return 1

            try:
                posts = extractor.extract_profile_posts(context)
                if self.date_filter:
                    posts = self._apply_date_filter(posts)

                processed_ids: set[str] = set()
                videos_done = 0
                photos_done = 0
                skipped = 0
                failed_items: list[PostItem] = []
                items_to_process = []

                if do_video:
                    video_posts = {
                        k: v for k, v in posts.items() if v.get("post_type") != "photo"
                    }
                    for v in self._build_video_list(video_posts):
                        dur = video_posts.get(v.video_id, {}).get("duration", 0)
                        if dur > self.config.max_video_duration:
                            self.reporter.info(
                                "DUR_SKIP %s duration=%ds exceeds %ds",
                                v.video_id,
                                dur,
                                self.config.max_video_duration,
                            )
                            skipped += 1
                            continue

                        pt = video_posts.get(v.video_id, {}).get("post_type")
                        if pt == "photo":
                            self.reporter.info("IMG_SKIP %s photo post", v.video_id)
                            skipped += 1
                            continue

                        processed_ids.add(v.video_id)
                        items_to_process.append(v)

                if do_photo:
                    photo_posts = {
                        k: v for k, v in posts.items() if k not in processed_ids
                    }
                    for post_id, data in photo_posts.items():
                        if data.get("post_type") == "video":
                            self.reporter.info("VID_SKIP %s video post", post_id)
                            skipped += 1
                            continue

                        author = data.get("author")
                        author_str = self.target_username
                        if isinstance(author, dict) and "uniqueId" in author:
                            author_str = author["uniqueId"]
                        elif isinstance(author, str) and author.strip():
                            author_str = author

                        pi = PostItem.create(
                            video_id=post_id,
                            username=author_str,
                            create_time=data.get("createTime", 0),
                            output_dir=self.output_dir,
                            is_photo=True,
                        )
                        items_to_process.append(pi)

                while True:
                    with self.reporter:
                        vd, pd, sd, fl = self._run_pass(
                            items_to_process, do_video, scraper, dl
                        )
                        videos_done += vd
                        photos_done += pd
                        skipped += sd
                        failed_items = fl

                    if not failed_items:
                        break

                    if self.reporter.ask_retry():
                        items_to_process = failed_items
                    else:
                        break
            finally:
                try:
                    context.close()
                except Exception:
                    pass

        self.reporter.info(
            "EXEC_DONE videos=%d photos=%d skipped=%d failed=%d",
            videos_done,
            photos_done,
            skipped,
            len(failed_items),
        )
        return 0

    def execute(self) -> int:
        if self.mode == "video":
            return self._run(do_video=True, do_photo=False)
        elif self.mode == "photo":
            return self._run(do_video=False, do_photo=True)
        else:
            return self._run(do_video=True, do_photo=True)
