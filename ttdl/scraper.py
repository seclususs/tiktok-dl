import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

from ttdl.config import AppConfig
from ttdl.events import Reporter
from ttdl.models import PhotoMetadata

BASE_URL: Final[str] = "https://musicaldown.com"
HOMEPAGE_URL: Final[str] = f"{BASE_URL}/en"
DEFAULT_UA: Final[str] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/127.0.0.0 Safari/537.36 Edg/127.0.0.0"
)
REQUEST_TIMEOUT: Final[int] = 15
RETRY_SLEEP_SECONDS: Final[float] = 2.0
IMAGE_POST_MARKER: Final[str] = "__IMAGE_POST__"
CONVERT_KEYWORD: Final[str] = "CONVERT VIDEO NOW"
TOAST_REGEX: Final[re.Pattern[str]] = re.compile(r"M\.toast\(\{\s*html:\s*'([^']+)'")


def _parse_musicaldown_form(
    soup: BeautifulSoup, post_url: str
) -> tuple[str, dict[str, str]]:
    form = soup.find("form")
    if not isinstance(form, Tag):
        raise TypeError("Scraper form not found")

    action_raw = form.get("action")
    action_str = str(action_raw) if action_raw else ""
    action_url = f"{BASE_URL}{action_str}" if action_str.startswith("/") else action_str

    data: dict[str, str] = {}
    for inp in form.find_all("input"):
        name = str(inp.get("name") or "").strip()
        if not name:
            continue

        if str(inp.get("type") or "") in ("text", "url"):
            data[name] = post_url
        else:
            data[name] = str(inp.get("value") or "")

    return action_url, data


def _check_toast_error(html_content: str) -> None:
    error_match = TOAST_REGEX.search(html_content)
    if error_match:
        raise ValueError(f"Scraper rejected: {error_match.group(1)}")


def _extract_mp4_links(html_content: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html_content, "html.parser")
    valid_links: list[tuple[str, str]] = []

    for link in soup.find_all("a", href=True):
        href = str(link.get("href", ""))
        text = str(link.get_text(strip=True)).upper()
        if href and "MP4" in text:
            valid_links.append((urljoin(BASE_URL, href), text))

    return valid_links


def _extract_photo_urls(html_content: str) -> list[str]:
    soup = BeautifulSoup(html_content, "html.parser")
    photo_links: list[str] = []

    for link in soup.find_all("a", href=True):
        href = str(link.get("href") or "").strip()
        text = link.get_text(strip=True).upper()

        if text == "DOWNLOAD":
            if href.startswith("/"):
                photo_links.append(urljoin(BASE_URL, href))

            elif "musicaldown" not in href.lower():
                photo_links.append(href)

    if not photo_links:
        for img in soup.find_all("img"):
            src = str(img.get("src") or "")
            if src and ("tiktokcdn" in src or "p16" in src):
                photo_links.append(src)

    return photo_links


class MusicalDownScraper:
    def __init__(self, config: AppConfig, output_dir: Path, reporter: Reporter) -> None:
        self.reporter = reporter
        self.config = config
        self.logs_dir = config.logs_dir
        self.output_dir = output_dir
        self._last_url = ""
        self._last_html = ""

    def _musicaldown_submit(self, post_url: str) -> str | None:
        if post_url == self._last_url and self._last_html:
            return self._last_html

        max_retries = self.config.musicaldown_max_retries

        with requests.Session() as session:
            session.headers.update({"User-Agent": DEFAULT_UA})

            for attempt in range(1, max_retries + 1):
                html_content = ""

                try:
                    resp_home = session.get(HOMEPAGE_URL, timeout=REQUEST_TIMEOUT)
                    resp_home.raise_for_status()

                    soup = BeautifulSoup(resp_home.text, "html.parser")
                    action_url, form_data = _parse_musicaldown_form(soup, post_url)

                    session.headers.update({"Referer": HOMEPAGE_URL})
                    r_post = session.post(
                        action_url, data=form_data, timeout=REQUEST_TIMEOUT
                    )
                    r_post.raise_for_status()

                    html_content = r_post.text
                    _check_toast_error(html_content)

                    self._last_url = post_url
                    self._last_html = html_content
                    return str(html_content)
                except Exception as e:
                    msg = str(e).split("\n")[0]
                    self.reporter.warning("Scrape retry %d: %s", attempt, msg)

                    if attempt == max_retries:
                        dump = self.logs_dir / f"error_md_{attempt}.html"
                        dump.write_text(html_content, encoding="utf-8")
                        raise ValueError("Scraper max retries reached") from e

                    time.sleep(RETRY_SLEEP_SECONDS)

        raise ValueError("Scraper service unreachable")

    def fetch_musicaldown_link(self, video_url: str) -> str:
        html_content = self._musicaldown_submit(video_url)
        if not html_content:
            raise ValueError("Empty response from scraper")

        if CONVERT_KEYWORD in html_content.upper():
            return IMAGE_POST_MARKER

        valid_links = _extract_mp4_links(html_content)
        if not valid_links:
            raise ValueError("No download link found on page")

        for href, text in valid_links:
            if "HD" in text:
                return href

        return valid_links[0][0]

    def fetch_musicaldown_photos(
        self,
        post_url: str,
        post_id: str,
        create_time: int,
    ) -> list[PhotoMetadata]:
        html_content = self._musicaldown_submit(post_url)
        if not html_content:
            raise ValueError("Empty response from scraper")

        if CONVERT_KEYWORD not in html_content.upper():
            return []

        photo_links = _extract_photo_urls(html_content)
        if not photo_links:
            self.reporter.warning("No photo links found for %s", post_id)
            return []

        base_dt = datetime.fromtimestamp(create_time, tz=UTC)
        photos: list[PhotoMetadata] = []

        for idx, dl_url in enumerate(photo_links):
            slide_dt = base_dt + timedelta(seconds=idx)
            slide_ts = int(slide_dt.timestamp())
            photos.append(
                PhotoMetadata.create(
                    post_id=post_id,
                    slide_index=idx + 1,
                    post_url=post_url,
                    download_url=dl_url,
                    create_time=slide_ts,
                    output_dir=self.output_dir,
                )
            )

        return photos
