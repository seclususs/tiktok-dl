import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

from ttdl.config import AppConfig
from ttdl.events import Reporter
from ttdl.models import PhotoMetadata

log = logging.getLogger(__name__)


class MusicalDownScraper:
    def __init__(self, config: AppConfig, output_dir: Path, reporter: "Reporter"):
        self.reporter = reporter
        self.config = config
        self.logs_dir = config.logs_dir
        self.output_dir = output_dir
        self._last_url = ""
        self._last_html = ""

    def _musicaldown_submit(
        self,
        post_url: str,
    ) -> str | None:
        if post_url == self._last_url and self._last_html:
            self.reporter.info("MUSICALDOWN using cached HTML for %s", post_url)
            return self._last_html

        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/127.0.0.0 "
                    "Safari/537.36 Edg/127.0.0.0"
                ),
            }
        )

        for attempt in range(1, self.config.musicaldown_max_retries + 1):
            self.reporter.info(
                "MUSICALDOWN attempt=%d/%d url=%s",
                attempt,
                self.config.musicaldown_max_retries,
                post_url,
            )
            html_content = ""
            try:
                r = session.get("https://musicaldown.com/en", timeout=15)
                r.raise_for_status()
                soup = BeautifulSoup(r.text, "html.parser")
                form = soup.find("form")
                if not isinstance(form, Tag):
                    raise TypeError("MUSICALDOWN_REJECT Form not found")

                action = form.get("action")
                if not isinstance(action, str):
                    action = str(action) if action else ""

                action_url = (
                    f"https://musicaldown.com{action}"
                    if action.startswith("/")
                    else action
                )

                data = {}
                for inp in form.find_all("input"):
                    name = inp.get("name")
                    if not name:
                        continue
                    if inp.get("type") in ["text", "url"]:
                        data[name] = post_url
                    else:
                        data[name] = inp.get("value", "")

                self.reporter.info("MUSICALDOWN_SUBMIT")
                session.headers.update({"Referer": "https://musicaldown.com/en"})
                r_post = session.post(action_url, data=data, timeout=15)
                r_post.raise_for_status()

                html_content = r_post.text
                error_match = re.search(
                    r"M\.toast\(\{\s*html:\s*'([^']+)'",
                    html_content,
                )
                if error_match:
                    raise ValueError(f"MUSICALDOWN_REJECT {error_match.group(1)}")

                self._last_url = post_url
                self._last_html = html_content
                return html_content
            except Exception as e:
                msg = str(e).split("\n")[0][:120]
                self.reporter.warning(
                    "MUSICALDOWN_FAIL attempt=%d err=%s",
                    attempt,
                    msg,
                )
                if attempt == self.config.musicaldown_max_retries:
                    dump = self.logs_dir / f"error_md_{attempt}.html"
                    dump.write_text(html_content, encoding="utf-8")
                    self.reporter.info("DUMP %s", dump.name)
                    raise ValueError("MUSICALDOWN_MAX_RETRIES_EXHAUSTED")
                time.sleep(2)

        raise ValueError("MUSICALDOWN_UNREACHABLE")

    def fetch_musicaldown_link(
        self,
        video_url: str,
    ) -> str:
        html_content = self._musicaldown_submit(video_url)
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
                self.reporter.info("MUSICALDOWN_OK link=HD")
                return href

        self.reporter.info("MUSICALDOWN_OK link=%s", valid_links[0][1])
        return valid_links[0][0]

    def fetch_musicaldown_photos(
        self,
        post_url: str,
        post_id: str,
        create_time: int,
    ) -> list[PhotoMetadata]:
        html_content = self._musicaldown_submit(post_url)
        if not html_content:
            raise ValueError("MUSICALDOWN_EMPTY_RESPONSE")

        html_upper = html_content.upper()
        if "CONVERT VIDEO NOW" not in html_upper:
            self.reporter.info("VID_SKIP %s video post detected", post_id)
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
            self.reporter.warning(
                "PHOTO_NO_LINKS %s no download buttons",
                post_id,
            )
            return []

        self.reporter.info("PHOTO_FOUND %s slides=%d", post_id, len(photo_links))

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
