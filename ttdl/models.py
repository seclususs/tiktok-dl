import calendar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class DateFilter:
    start_dt: datetime
    end_dt: datetime

    def matches(self, dt: datetime) -> bool:
        return self.start_dt <= dt <= self.end_dt

    def describe(self) -> str:
        fmt = "%Y-%m-%d"
        if self.start_dt.date() == self.end_dt.date():
            return self.start_dt.strftime(fmt)
        return f"{self.start_dt.strftime(fmt)} to {self.end_dt.strftime(fmt)}"

    @staticmethod
    def _parse_date(date_str: str, is_end: bool = False) -> datetime:
        parts = date_str.split("-")
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else (12 if is_end else 1)

        if len(parts) > 2:
            day = int(parts[2])
        else:
            day = calendar.monthrange(year, month)[1] if is_end else 1

        if is_end:
            return datetime(year, month, day, 23, 59, 59, tzinfo=timezone.utc)
        return datetime(year, month, day, 0, 0, 0, tzinfo=timezone.utc)

    @classmethod
    def build(cls, date_input: str | None) -> Optional["DateFilter"]:
        if not date_input:
            return None

        try:
            if ":" in date_input:
                start_str, end_str = date_input.split(":", 1)
                start_dt = cls._parse_date(start_str, is_end=False)
                end_dt = cls._parse_date(end_str, is_end=True)
            else:
                start_dt = cls._parse_date(date_input, is_end=False)
                end_dt = cls._parse_date(date_input, is_end=True)

            return cls(start_dt=start_dt, end_dt=end_dt)
        except (ValueError, IndexError):
            raise ValueError(f"Invalid date format: {date_input}")


@dataclass
class PostItem:
    video_id: str
    url: str
    create_time: int
    datetime_obj: datetime
    target_filename: str
    output_path: Path
    is_photo: bool

    @classmethod
    def create(
        cls,
        video_id: str,
        username: str,
        create_time: int,
        output_dir: Path,
        is_photo: bool = False,
    ) -> "PostItem":
        dt_obj = (
            datetime.fromtimestamp(create_time, tz=timezone.utc)
            if create_time > 0
            else datetime.now(timezone.utc)
        )
        ts_str = dt_obj.strftime("%Y%m%d_%H%M%S")
        url = f"https://www.tiktok.com/@{username}/video/{video_id}"

        if is_photo:
            filename = f"IMG_{ts_str}_{video_id}_carousel"
        else:
            filename = f"VID_{ts_str}_{video_id}.mp4"

        return cls(
            video_id=video_id,
            url=url,
            create_time=create_time,
            datetime_obj=dt_obj,
            target_filename=filename,
            output_path=output_dir / filename,
            is_photo=is_photo,
        )


@dataclass
class PhotoMetadata:
    post_id: str
    slide_index: int
    url: str
    download_url: str
    create_time: int
    datetime_obj: datetime
    target_filename: str
    output_path: Path

    @classmethod
    def create(
        cls,
        post_id: str,
        slide_index: int,
        post_url: str,
        download_url: str,
        create_time: int,
        output_dir: Path,
    ) -> "PhotoMetadata":
        dt_obj = (
            datetime.fromtimestamp(create_time, tz=timezone.utc)
            if create_time > 0
            else datetime.now(timezone.utc)
        )
        ts_str = dt_obj.strftime("%Y%m%d_%H%M%S")
        filename = f"IMG_{ts_str}_{post_id}_s{slide_index}.jpg"

        return cls(
            post_id=post_id,
            slide_index=slide_index,
            url=post_url,
            download_url=download_url,
            create_time=create_time,
            datetime_obj=dt_obj,
            target_filename=filename,
            output_path=output_dir / filename,
        )
