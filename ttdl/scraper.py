import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.sync_api import BrowserContext

from ttdl.constants import MUSICALDOWN_MAX_RETRIES
from ttdl.models import PhotoMetadata

log = logging.getLogger(__name__)


class MusicalDownScraper:
    def __init__(self, logs_dir: Path, output_dir: Path):
        self.logs_dir = logs_dir
        self.output_dir = output_dir

    def _musicaldown_submit(
        self,
        context: BrowserContext,
        post_url: str,
    ) -> str | None:
        for attempt in range(1, MUSICALDOWN_MAX_RETRIES + 1):
            log.info(
                "[blue]MUSICALDOWN[/] attempt=%d/%d url=%s",
                attempt,
                MUSICALDOWN_MAX_RETRIES,
                post_url,
            )
            page = context.new_page()
            html_content = ""

            try:
                page.goto(
                    "https://musicaldown.com/en",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                input_loc = page.locator(
                    "form input[type='text'], form input[type='url']"
                ).first
                input_loc.wait_for(state="visible", timeout=10000)
                input_loc.fill(post_url)
                btn = page.locator("form button[type='submit']").first
                log.info("[magenta]MUSICALDOWN_SUBMIT[/]")
                try:
                    with page.expect_navigation(
                        wait_until="domcontentloaded", timeout=15000
                    ):
                        btn.click(force=True)
                except Exception:
                    pass

                html_content = page.content()
                error_match = re.search(
                    r"M\.toast\(\{\s*html:\s*'([^']+)'",
                    html_content,
                )
                if error_match:
                    raise ValueError(f"MUSICALDOWN_REJECT {error_match.group(1)}")

                return html_content
            except Exception as e:
                msg = str(e).split("\n")[0][:120]
                log.warning(
                    "[bright_yellow]MUSICALDOWN_FAIL[/] attempt=%d err=%s",
                    attempt,
                    msg,
                )
                if attempt == MUSICALDOWN_MAX_RETRIES:
                    if not html_content:
                        html_content = page.content()
                    dump = self.logs_dir / f"error_md_{attempt}.html"
                    dump.write_text(html_content, encoding="utf-8")
                    log.info("[blue]DUMP[/] %s", dump.name)
                    raise ValueError("MUSICALDOWN_MAX_RETRIES_EXHAUSTED")
                page.wait_for_timeout(2000)
            finally:
                page.close()

        raise ValueError("MUSICALDOWN_UNREACHABLE")

    def fetch_musicaldown_link(
        self,
        context: BrowserContext,
        video_url: str,
    ) -> str:
        html_content = self._musicaldown_submit(context, video_url)
        if not html_content:
            raise ValueError("MUSICALDOWN_EMPTY_RESPONSE")
        if "CONVERT VIDEO NOW" in html_content.upper():
            return "__IMAGE_POST__"

        soup = BeautifulSoup(html_content, "html.parser")
        valid_links = []
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            text = link.get_text(strip=True).upper()
            if href and "MP4" in text:
                valid_links.append(
                    (
                        urljoin("https://musicaldown.com", href),
                        text,
                    ),
                )
        if not valid_links:
            raise ValueError("MUSICALDOWN_NO_MP4_BUTTON")

        for href, text in valid_links:
            if "HD" in text:
                log.info("[green]MUSICALDOWN_OK[/] link=HD")
                return href

        log.info("[green]MUSICALDOWN_OK[/] link=%s", valid_links[0][1])
        return valid_links[0][0]

    def fetch_musicaldown_photos(
        self,
        context: BrowserContext,
        post_url: str,
        post_id: str,
        create_time: int,
    ) -> list[PhotoMetadata]:
        html_content = self._musicaldown_submit(context, post_url)
        if not html_content:
            raise ValueError("MUSICALDOWN_EMPTY_RESPONSE")

        html_upper = html_content.upper()
        if "CONVERT VIDEO NOW" not in html_upper:
            log.info("[yellow]VID_SKIP[/] %s video post detected", post_id)
            return []

        soup = BeautifulSoup(html_content, "html.parser")
        photo_links: list[str] = []
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            text = link.get_text(strip=True).upper()
            if text == "DOWNLOAD" and href and "musicaldown" not in href.lower():
                photo_links.append(href)
            elif text == "DOWNLOAD" and href and href.startswith("/"):
                photo_links.append(urljoin("https://musicaldown.com", href))
        if not photo_links:
            for img in soup.find_all("img"):
                src = img.get("src", "")
                if src and ("tiktokcdn" in src or "p16" in src):
                    photo_links.append(src)
        if not photo_links:
            log.warning(
                "[bright_yellow]PHOTO_NO_LINKS[/] %s no download buttons",
                post_id,
            )
            return []

        log.info("[blue]PHOTO_FOUND[/] %s slides=%d", post_id, len(photo_links))

        base_dt = datetime.fromtimestamp(create_time, tz=timezone.utc)
        photos: list[PhotoMetadata] = []
        for idx, dl_url in enumerate(photo_links):
            slide_dt = base_dt + timedelta(seconds=idx)
            slide_ts = int(slide_dt.timestamp())
            ts_str = slide_dt.strftime("%Y%m%d_%H%M%S")
            filename = f"IMG_{ts_str}_{post_id}_s{idx + 1}.jpg"
            photos.append(
                PhotoMetadata(
                    post_id=post_id,
                    slide_index=idx + 1,
                    url=post_url,
                    download_url=dl_url,
                    create_time=slide_ts,
                    datetime_obj=slide_dt,
                    target_filename=filename,
                    output_path=self.output_dir / filename,
                )
            )

        return photos
