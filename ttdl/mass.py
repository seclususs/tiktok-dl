import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext, sync_playwright
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from ttdl.browser import detect_browser
from ttdl.constants import MAX_VIDEO_DURATION
from ttdl.downloader import MediaDownloader
from ttdl.extractor import ProfileExtractor
from ttdl.logger import console
from ttdl.models import DateFilter, VideoMetadata
from ttdl.scraper import MusicalDownScraper

log = logging.getLogger(__name__)


class TikTokDownloader:
    def __init__(
        self,
        target_username: str,
        workspace_dir: Path,
        mode: str = "all",
        date_filter: DateFilter | None = None,
    ) -> None:
        self.target_username = target_username.lstrip("@")
        self.mode = mode
        self.date_filter = date_filter
        self.workspace_dir = workspace_dir

        self.browser_name, self.browser_path = detect_browser()
        self.session_dir = self.workspace_dir / ".sessions" / self.browser_name
        self.tmp_dir = self.workspace_dir / ".tmp"
        self.logs_dir = self.workspace_dir / "logs"
        self.output_dir = self.workspace_dir / "downloads" / self.target_username

        self._setup_environment()

    def _setup_environment(self) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        if self.mode in ("all", "video"):
            for binary in ("ffmpeg", "ffprobe"):
                if not shutil.which(binary):
                    raise RuntimeError(f"{binary.upper()} NOT FOUND")

        for pattern in ("temp_raw_*.mp4", "temp_proc_*.mp4"):
            for tmp in self.tmp_dir.glob(pattern):
                tmp.unlink()

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

        log.info(
            "[blue]DATE_FILTER[/] %d->%d posts in range",
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
        log.info(
            "[cyan]BROWSER_INIT[/] browser=%s session=%s",
            self.browser_name,
            self.session_dir.name,
        )
        log.info("[cyan]BROWSER_PATH[/] %s", self.browser_path)

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

    def _make_progress(self) -> Progress:
        return Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
        )

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
    ) -> list[VideoMetadata]:
        videos: list[VideoMetadata] = []
        for vid_id, data in posts.items():
            author = data.get("author")
            author_str = self.target_username
            if isinstance(author, dict) and "uniqueId" in author:
                author_str = author["uniqueId"]
            elif isinstance(author, str) and author.strip():
                author_str = author

            create_time = data.get("createTime", 0)
            dt_obj = datetime.fromtimestamp(create_time, tz=timezone.utc)
            ts = dt_obj.strftime("%Y%m%d_%H%M%S")
            filename = f"VID_{ts}_{vid_id}.mp4"

            videos.append(
                VideoMetadata(
                    video_id=vid_id,
                    url=f"https://www.tiktok.com/@{author_str}/video/{vid_id}",
                    create_time=create_time,
                    datetime_obj=dt_obj,
                    target_filename=filename,
                    output_path=self.output_dir / filename,
                ),
            )

        return videos

    def _process_single_video(
        self,
        context,
        video,
        do_photo,
        progress,
        scraper: MusicalDownScraper,
        dl: MediaDownloader,
    ):
        try:
            dl_url = scraper.fetch_musicaldown_link(context, video.url)
            if dl_url == "__IMAGE_POST__":
                if not do_photo:
                    log.info("[yellow]IMG_SKIP[/] %s image post", video.video_id)
                    return 0, 0, 1, 0

                photos = scraper.fetch_musicaldown_photos(
                    context,
                    video.url,
                    video.video_id,
                    video.create_time,
                )
                photos_done = 0
                for ph in photos:
                    if dl.download_photo(context, ph, progress):
                        photos_done += 1
                return 0, photos_done, 0, 0

            dl.download_and_process_video(context, video, dl_url, progress)
            return 1, 0, 0, 0
        except Exception as e:
            msg = str(e).split("\n")[0][:120]
            log.error("[red]FAIL[/] %s %s", video.video_id, msg)
            return 0, 0, 0, 1

    def _process_videos(
        self,
        context,
        posts,
        processed_ids,
        do_photo,
        progress,
        scraper: MusicalDownScraper,
        dl: MediaDownloader,
    ):
        videos_done = 0
        photos_done = 0
        skipped = 0
        failed = 0

        video_posts = {k: v for k, v in posts.items() if v.get("post_type") != "photo"}
        videos = self._build_video_list(video_posts)
        total_v = len(videos)
        for idx, video in enumerate(videos, 1):
            processed_ids.add(video.video_id)
            if video.output_path.exists():
                skipped += 1
                continue

            dur = video_posts.get(video.video_id, {}).get("duration", 0)
            if dur > MAX_VIDEO_DURATION:
                log.info(
                    "[yellow]DUR_SKIP[/] %s duration=%ds exceeds %ds",
                    video.video_id,
                    dur,
                    MAX_VIDEO_DURATION,
                )
                skipped += 1
                continue

            log.info("[magenta]PROCESS[/] %d/%d id=%s", idx, total_v, video.video_id)
            pt = video_posts.get(video.video_id, {}).get("post_type")
            if pt == "photo":
                log.info("[yellow]IMG_SKIP[/] %s photo post", video.video_id)
                skipped += 1
                continue

            vd, pd, sd, fd = self._process_single_video(
                context, video, do_photo, progress, scraper, dl
            )
            videos_done += vd
            photos_done += pd
            skipped += sd
            failed += fd
        return videos_done, photos_done, skipped, failed

    def _process_single_photo_post(
        self,
        context,
        post_url,
        post_id,
        create_time,
        progress,
        scraper: MusicalDownScraper,
        dl: MediaDownloader,
    ):
        try:
            photos = scraper.fetch_musicaldown_photos(
                context,
                post_url,
                post_id,
                create_time,
            )
            if not photos:
                return 0, 1, 0

            all_exist = all(ph.output_path.exists() for ph in photos)
            if all_exist:
                return 0, 1, 0

            photos_done = 0
            for ph in photos:
                if dl.download_photo(context, ph, progress):
                    photos_done += 1
            return photos_done, 0, 0
        except Exception as e:
            msg = str(e).split("\n")[0][:120]
            log.error("[red]FAIL[/] %s %s", post_id, msg)
            return 0, 0, 1

    def _process_photos(
        self,
        context,
        posts,
        processed_ids,
        progress,
        scraper: MusicalDownScraper,
        dl: MediaDownloader,
    ):
        photos_done = 0
        skipped = 0
        failed = 0

        photo_posts = {k: v for k, v in posts.items() if k not in processed_ids}
        total_p = len(photo_posts)

        for idx, (post_id, data) in enumerate(photo_posts.items(), 1):
            if data.get("post_type") == "video":
                log.info("[yellow]VID_SKIP[/] %s video post", post_id)
                skipped += 1
                continue

            create_time = data.get("createTime", 0)
            post_url = self._build_post_url(post_id, data)
            log.info("[magenta]PHOTO_PROCESS[/] %d/%d id=%s", idx, total_p, post_id)
            pd, sd, fd = self._process_single_photo_post(
                context, post_url, post_id, create_time, progress, scraper, dl
            )
            photos_done += pd
            skipped += sd
            failed += fd
        return photos_done, skipped, failed

    def _run(
        self,
        do_video: bool,
        do_photo: bool,
    ) -> None:
        log.info(
            "[green]EXEC_START[/] target=%s mode=%s",
            self.target_username,
            self.mode,
        )

        if self.date_filter:
            log.info("[blue]DATE_RANGE[/] %s", self.date_filter.describe())

        dl = MediaDownloader(self.tmp_dir, self.output_dir)
        scraper = MusicalDownScraper(self.logs_dir, self.output_dir)
        extractor = ProfileExtractor(self.target_username, self.workspace_dir)

        if do_video:
            dl.purge_low_res()

        with sync_playwright() as p:
            try:
                context = self._launch_browser(p)
            except Exception as e:
                log.error("[red]LAUNCH_FAIL[/] %s", str(e).split("\n")[0][:120])
                log.error("[red]ENSURE[/] Edge is installed and fully closed")
                sys.exit(1)

            posts = extractor.extract_profile_posts(context)
            if self.date_filter:
                posts = self._apply_date_filter(posts)

            processed_ids: set[str] = set()
            videos_done = 0
            photos_done = 0
            skipped = 0
            failed = 0

            with self._make_progress() as progress:
                if do_video:
                    vd, pd, sd, fd = self._process_videos(
                        context, posts, processed_ids, do_photo, progress, scraper, dl
                    )
                    videos_done += vd
                    photos_done += pd
                    skipped += sd
                    failed += fd
                if do_photo:
                    pd, sd, fd = self._process_photos(
                        context, posts, processed_ids, progress, scraper, dl
                    )
                    photos_done += pd
                    skipped += sd
                    failed += fd
            context.close()

        log.info(
            "[green]EXEC_DONE[/] videos=%d photos=%d skipped=%d failed=%d",
            videos_done,
            photos_done,
            skipped,
            failed,
        )

    def execute(self) -> None:
        if self.mode == "video":
            self._run(do_video=True, do_photo=False)
        elif self.mode == "photo":
            self._run(do_video=False, do_photo=True)
        else:
            self._run(do_video=True, do_photo=True)
