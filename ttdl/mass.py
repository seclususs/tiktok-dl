import logging
import subprocess
import time
from datetime import UTC, datetime
from typing import Any, Final

from playwright.sync_api import BrowserContext, Playwright, sync_playwright

from ttdl.browser import detect_browser
from ttdl.config import AppConfig
from ttdl.downloader import MediaDownloader
from ttdl.events import Reporter
from ttdl.extractor import ProfileExtractor
from ttdl.models import DateFilter, PostItem
from ttdl.scraper import IMAGE_POST_MARKER, MusicalDownScraper
from ttdl.termux import is_termux

log = logging.getLogger(__name__)

EDGE_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/127.0.0.0 Safari/537.36 Edg/127.0.0.0"
)
CHROME_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/127.0.0.0 Safari/537.36"
)
CDP_ENDPOINT: Final[str] = "http://localhost:9222"
JS_WEBDRIVER_BYPASS: Final[str] = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
)
JS_MOBILE_BYPASS: Final[str] = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'maxTouchPoints', {get: () => 0});
delete window.ontouchstart;
"""


def _resolve_author_string(author: Any, default_username: str) -> str:
    if isinstance(author, dict) and "uniqueId" in author:
        return str(author["uniqueId"])

    if isinstance(author, str) and author.strip():
        return author.strip()

    return default_username


class TikTokDownloader:
    def __init__(
        self,
        config: AppConfig,
        target_username: str,
        reporter: "Reporter",
        mode: str = "all",
        date_filter: DateFilter | None = None,
        jsononly: bool = False,
        usejson: bool = False,
        force: bool = False,
    ) -> None:
        self.config = config
        self.reporter = reporter
        self.target_username = target_username.lstrip("@")
        self.mode = mode
        self.date_filter = date_filter
        self.jsononly = jsononly
        self.usejson = usejson
        self.force = force

        if is_termux():
            self.browser_name = "termux_cdp"
            self.browser_path = ""
        else:
            self.browser_name, default_path = detect_browser()
            self.browser_path = self.config.browser_executable or default_path

        self.session_dir = self.config.session_dir / self.browser_name
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir = self.config.downloads_dir / self.target_username
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _apply_date_filter(
        self, posts: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        if not self.date_filter:
            return posts

        filtered: dict[str, dict[str, Any]] = {}
        for k, v in posts.items():
            ct = v.get("createTime", 0)
            if ct == 0:
                filtered[k] = v
                continue
            dt = datetime.fromtimestamp(ct, tz=UTC)
            if self.date_filter.matches(dt):
                filtered[k] = v

        self.reporter.info(
            "DATE_FILTER %d->%d posts in range", len(posts), len(filtered)
        )
        return filtered

    def _browser_user_agent(self) -> str:
        return EDGE_USER_AGENT if self.browser_name == "edge" else CHROME_USER_AGENT

    def _launch_termux_browser(self, p: Playwright) -> BrowserContext:
        self.reporter.debug("BROWSER_INIT mode=termux_cdp")
        subprocess.run(
            [
                "adb",
                "forward",
                "tcp:9222",
                "localabstract:chrome_devtools_remote",
            ],
            capture_output=True,
            check=False,
        )
        subprocess.run(
            [
                "adb",
                "shell",
                "am",
                "start",
                "-n",
                "com.android.chrome/com.google.android.apps.chrome.Main",
                "-d",
                "about:blank",
            ],
            capture_output=True,
            check=False,
        )
        time.sleep(3)
        try:
            browser = p.chromium.connect_over_cdp(CDP_ENDPOINT)
        except Exception:
            self.reporter.error("CDP_FAIL Failed to connect to Android Chrome")
            raise

        context = browser.contexts[0]
        if context.pages:
            page = context.pages[0]
            for tab in context.pages[1:]:
                try:
                    tab.close()
                except Exception:
                    pass
        else:
            page = context.new_page()

        client = context.new_cdp_session(page)
        client.send(
            "Network.setUserAgentOverride",
            {
                "userAgent": self._browser_user_agent(),
                "platform": "Win32",
                "userAgentMetadata": {
                    "architecture": "x86",
                    "bitness": "64",
                    "mobile": False,
                    "model": "",
                    "platform": "Windows",
                    "platformVersion": "10.0.0",
                },
            },
        )
        page.add_init_script(JS_MOBILE_BYPASS)
        return context

    def _launch_desktop_browser(self, p: Playwright) -> BrowserContext:
        self.reporter.debug(
            "BROWSER_INIT browser=%s session=%s",
            self.browser_name,
            self.session_dir.name,
        )
        self.reporter.debug("BROWSER_PATH %s", self.browser_path)

        context = p.chromium.launch_persistent_context(
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
            ignore_default_args=["--enable-automation"],
            no_viewport=True,
        )
        context.add_init_script(JS_WEBDRIVER_BYPASS)
        return context

    def _launch_browser(self, p: Playwright) -> BrowserContext:
        if is_termux():
            return self._launch_termux_browser(p)

        return self._launch_desktop_browser(p)

    def _build_video_list(self, posts: dict[str, dict[str, Any]]) -> list[PostItem]:
        videos: list[PostItem] = []
        for vid_id, data in posts.items():
            author_str = _resolve_author_string(
                data.get("author"), self.target_username
            )
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

    def _process_single_video(
        self,
        video: PostItem,
        do_photo: bool,
        scraper: MusicalDownScraper,
        dl: MediaDownloader,
    ) -> tuple[int, int, int, list[PostItem]]:
        try:
            dl_url = scraper.fetch_musicaldown_link(video.url)
            if dl_url == IMAGE_POST_MARKER:
                if not do_photo:
                    self.reporter.debug("IMG_SKIP %s image post", video.video_id)
                    return 0, 0, 1, []

                photos = scraper.fetch_musicaldown_photos(
                    video.url, video.video_id, video.create_time
                )
                photos_done = sum(1 for ph in photos if dl.download_photo(ph))
                return 0, photos_done, 0, []

            dl.download_and_process_video(video, dl_url)
            return 1, 0, 0, []
        except Exception as e:
            msg = str(e).split("\n")[0][:120]
            self.reporter.error("FAIL %s %s", video.video_id, msg)
            return 0, 0, 0, [video]

    def _process_single_photo_post(
        self,
        photo_post: PostItem,
        scraper: MusicalDownScraper,
        dl: MediaDownloader,
    ) -> tuple[int, int, list[PostItem]]:
        try:
            photos = scraper.fetch_musicaldown_photos(
                photo_post.url, photo_post.video_id, photo_post.create_time
            )
            if not photos or all(ph.output_path.exists() for ph in photos):
                return 0, 1, []

            photos_done = sum(1 for ph in photos if dl.download_photo(ph))
            return photos_done, 0, []
        except Exception as e:
            msg = str(e).split("\n")[0][:120]
            self.reporter.error("FAIL %s %s", photo_post.video_id, msg)
            return 0, 0, [photo_post]

    def _run_pass(
        self,
        items: list[PostItem],
        do_video: bool,
        scraper: MusicalDownScraper,
        dl: MediaDownloader,
    ) -> tuple[int, int, int, list[PostItem]]:
        videos_done = 0
        photos_done = 0
        skipped = 0
        failed_items: list[PostItem] = []

        total = len(items)
        overall_task = self.reporter.add_task(
            "[bold green]Overall",
            total=total,
            unit="posts",
        )
        try:
            for idx, item in enumerate(items, 1):
                if item.output_path.exists() and not item.is_photo:
                    skipped += 1
                    self.reporter.update_task(overall_task, advance=1)
                    continue

                self.reporter.debug("PROCESS %d/%d id=%s", idx, total, item.video_id)
                if not item.is_photo:
                    vd, pd, sd, fl = self._process_single_video(
                        item, do_photo=True, scraper=scraper, dl=dl
                    )
                    videos_done += vd
                    photos_done += pd
                    skipped += sd
                    failed_items.extend(fl)
                else:
                    pd, sd, fl_p = self._process_single_photo_post(item, scraper, dl)
                    photos_done += pd
                    skipped += sd
                    failed_items.extend(fl_p)

                self.reporter.update_task(overall_task, advance=1)
        finally:
            self.reporter.remove_task(overall_task)

        return videos_done, photos_done, skipped, failed_items

    def _fetch_profile_posts(
        self, extractor: ProfileExtractor
    ) -> dict[str, dict[str, Any]] | None:
        if self.usejson:
            return extractor.extract_profile_posts(None)

        with sync_playwright() as p:
            try:
                context = self._launch_browser(p)
            except Exception as e:
                self.reporter.error("LAUNCH_FAIL %s", str(e).split("\n")[0][:120])
                self.reporter.error("ENSURE Chrome/Edge is installed and fully closed")
                return None

            try:
                return extractor.extract_profile_posts(context)
            finally:
                try:
                    context.close()
                except Exception:
                    pass

    def _prepare_work_items(
        self,
        posts: dict[str, dict[str, Any]],
        do_video: bool,
        do_photo: bool,
    ) -> tuple[list[PostItem], int]:
        items: list[PostItem] = []
        processed_ids: set[str] = set()
        skipped = 0

        if do_video:
            video_posts = {
                k: v for k, v in posts.items() if v.get("post_type") != "photo"
            }
            for v in self._build_video_list(video_posts):
                dur = video_posts.get(v.video_id, {}).get("duration", 0)
                max_dur = self.config.max_video_duration
                if max_dur > 0 and dur > max_dur:
                    self.reporter.debug(
                        "DUR_SKIP %s duration=%ds exceeds %ds",
                        v.video_id,
                        dur,
                        max_dur,
                    )
                    skipped += 1
                    processed_ids.add(v.video_id)
                    continue

                if video_posts.get(v.video_id, {}).get("post_type") == "photo":
                    self.reporter.debug("IMG_SKIP %s photo post", v.video_id)
                    skipped += 1
                    processed_ids.add(v.video_id)
                    continue

                processed_ids.add(v.video_id)
                items.append(v)

        if do_photo:
            photo_posts = {k: v for k, v in posts.items() if k not in processed_ids}
            for post_id, data in photo_posts.items():
                if data.get("post_type") == "video":
                    self.reporter.debug("VID_SKIP %s video post", post_id)
                    skipped += 1
                    continue

                author_str = _resolve_author_string(
                    data.get("author"), self.target_username
                )
                pi = PostItem.create(
                    video_id=post_id,
                    username=author_str,
                    create_time=data.get("createTime", 0),
                    output_dir=self.output_dir,
                    is_photo=True,
                )
                items.append(pi)

        return items, skipped

    def _run(self, do_video: bool, do_photo: bool) -> int:
        self.reporter.info(
            "EXEC_START target=%s mode=%s", self.target_username, self.mode
        )
        if self.date_filter:
            self.reporter.info("DATE_RANGE %s", self.date_filter.describe())

        dl = MediaDownloader(self.config, self.reporter)
        scraper = MusicalDownScraper(self.config, self.output_dir, self.reporter)
        extractor = ProfileExtractor(
            self.target_username,
            self.config,
            self.reporter,
            usejson=self.usejson,
            force=self.force,
        )

        if do_video:
            dl.purge_low_res(self.output_dir)

        posts = self._fetch_profile_posts(extractor)
        if posts is None:
            return 1

        if self.date_filter:
            posts = self._apply_date_filter(posts)

        if self.jsononly:
            self.reporter.info("EXEC_DONE scraped %d posts to JSON cache", len(posts))
            return 0

        items_to_process, init_skipped = self._prepare_work_items(
            posts, do_video, do_photo
        )

        videos_done = 0
        photos_done = 0
        skipped = init_skipped
        failed_items: list[PostItem] = []

        while True:
            with self.reporter:
                vd, pd, sd, fl = self._run_pass(items_to_process, do_video, scraper, dl)
                videos_done += vd
                photos_done += pd
                skipped += sd
                failed_items = fl

            if not failed_items or not self.reporter.ask_retry():
                break

            items_to_process = failed_items

        self.reporter.info(
            "EXEC_DONE videos=%d photos=%d skipped=%d failed=%d",
            videos_done,
            photos_done,
            skipped,
            len(failed_items),
        )
        self.reporter.print_summary(
            "Download Summary",
            [
                ("Target", f"@{self.target_username}"),
                ("Videos", str(videos_done)),
                ("Photos", str(photos_done)),
                ("Skipped", str(skipped)),
                ("Failed", str(len(failed_items))),
                ("Output", str(self.output_dir)),
            ],
            style="green" if not failed_items else "yellow",
        )
        return 0

    def execute(self) -> int:
        if self.mode == "video":
            return self._run(do_video=True, do_photo=False)

        if self.mode == "photo":
            return self._run(do_video=False, do_photo=True)

        return self._run(do_video=True, do_photo=True)
