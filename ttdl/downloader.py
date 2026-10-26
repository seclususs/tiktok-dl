import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import piexif
import requests

from ttdl.config import AppConfig
from ttdl.events import Reporter
from ttdl.models import PhotoMetadata, PostItem

log = logging.getLogger(__name__)

DEFAULT_REFERER: Final[str] = "https://musicaldown.com/"
DEFAULT_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/127.0.0.0 Safari/537.36 Edg/127.0.0.0"
)
SHIFTED_START_THRESHOLD: Final[float] = 0.110
EXIF_DATETIME_FORMAT: Final[str] = "%Y:%m:%d %H:%M:%S"
ISO_DATETIME_FORMAT: Final[str] = "%Y-%m-%dT%H:%M:%SZ"
MICROS_PER_SECOND: Final[int] = 1_000_000


@dataclass(frozen=True)
class VideoStreamInfo:
    width: int
    height: int
    duration_s: float
    start_time_s: float

    @property
    def min_dimension(self) -> int:
        return min(self.width, self.height)

    @property
    def duration_us(self) -> int | None:
        return int(self.duration_s * MICROS_PER_SECOND) if self.duration_s > 0 else None

    @property
    def is_shifted(self) -> bool:
        return (self.start_time_s < 0.0) or (
            self.start_time_s >= SHIFTED_START_THRESHOLD
        )


def _probe_video_file(path: Path) -> VideoStreamInfo:
    probe_out = subprocess.check_output(
        [
            "ffprobe",
            "-ignore_editlist",
            "1",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,duration,start_time",
            "-of",
            "json",
            str(path),
        ],
        stderr=subprocess.DEVNULL,
    )
    probe_data = json.loads(probe_out.decode("utf-8"))
    streams = probe_data.get("streams")
    if not streams:
        raise ValueError("No video stream found")

    stream_info = streams[0]
    width = int(stream_info.get("width", 0))
    height = int(stream_info.get("height", 0))

    try:
        duration_s = float(stream_info.get("duration", 0.0))
    except (ValueError, TypeError):
        duration_s = 0.0

    start_val = stream_info.get("start_time")
    if start_val is None or start_val == "N/A":
        start_time_s = -1.0
    else:
        try:
            start_time_s = float(start_val)
        except (ValueError, TypeError):
            start_time_s = -1.0

    return VideoStreamInfo(
        width=width,
        height=height,
        duration_s=duration_s,
        start_time_s=start_time_s,
    )


def _build_ffmpeg_copy_cmd(source: Path, target: Path, iso_time: str) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-c",
        "copy",
        "-metadata",
        f"creation_time={iso_time}",
        "-progress",
        "pipe:1",
        "-nostats",
        str(target),
    ]


def _build_ffmpeg_encode_cmd(source: Path, target: Path, iso_time: str) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-c:v",
        "libx265",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-tag:v",
        "hvc1",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        "-metadata",
        f"creation_time={iso_time}",
        "-progress",
        "pipe:1",
        "-nostats",
        str(target),
    ]


def _inject_exif_datetime(photo_path: Path, datetime_obj: datetime) -> None:
    try:
        exif_dict = piexif.load(str(photo_path))
        local_dt = datetime_obj.astimezone()
        exif_time = local_dt.strftime(EXIF_DATETIME_FORMAT).encode("utf-8")

        exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = exif_time
        exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = exif_time
        exif_dict["0th"][piexif.ImageIFD.DateTime] = exif_time

        exif_bytes = piexif.dump(exif_dict)
        piexif.insert(exif_bytes, str(photo_path))
    except Exception as e:
        log.warning("EXIF_INJECT_FAIL %s", e)


def _apply_file_timestamp(path: Path, timestamp: int) -> None:
    try:
        os.utime(str(path), (timestamp, timestamp))
    except OSError:
        pass


class MediaDownloader:
    def __init__(self, config: AppConfig, reporter: "Reporter") -> None:
        self.reporter = reporter
        self.config = config
        self.tmp_dir = config.tmp_dir

    def _stream_response_to_file(
        self,
        resp: requests.Response,
        temp_dest: Path,
        max_size: int | None,
        task_id: Any,
    ) -> bool:
        downloaded = 0

        with open(temp_dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=self.config.download_chunk_size):
                if not chunk:
                    continue

                f.write(chunk)
                downloaded += len(chunk)
                self.reporter.update_task(task_id, advance=len(chunk))
                if max_size and downloaded > max_size:
                    return False

        return True

    def _download_file(
        self,
        url: str,
        dest: Path,
        label: str,
        max_size: int | None = None,
    ) -> bool:
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Referer": DEFAULT_REFERER,
        }
        temp_dest = dest.with_suffix(dest.suffix + ".download")
        task_id = None

        try:
            for attempt in range(1, self.config.download_max_retries + 1):
                try:
                    resp = requests.get(
                        url,
                        stream=True,
                        headers=headers,
                        timeout=self.config.download_timeout,
                    )
                    resp.raise_for_status()

                    total = int(resp.headers.get("content-length", "0"))
                    if max_size and total > max_size:
                        resp.close()
                        self.reporter.debug(
                            "SIZE_SKIP %s content_length=%dMB exceeds %dMB",
                            label,
                            total // MICROS_PER_SECOND,
                            max_size // MICROS_PER_SECOND,
                        )
                        return False

                    task_id = self.reporter.add_task(
                        f"[cyan]DL {label}",
                        total=total if total > 0 else None,
                    )
                    ok = self._stream_response_to_file(
                        resp, temp_dest, max_size, task_id
                    )

                    self.reporter.remove_task(task_id)
                    task_id = None

                    if not ok:
                        self.reporter.debug(
                            "SIZE_SKIP %s exceeded %dMB",
                            label,
                            (max_size or 0) // MICROS_PER_SECOND,
                        )
                        if temp_dest.exists():
                            temp_dest.unlink()
                        return False

                    temp_dest.rename(dest)
                    return True
                except (
                    requests.exceptions.Timeout,
                    requests.exceptions.ConnectionError,
                    requests.exceptions.ChunkedEncodingError,
                ) as e:
                    if task_id is not None:
                        self.reporter.remove_task(task_id)
                        task_id = None

                    msg = str(e).split("\n")[0][:80]
                    self.reporter.warning(
                        "DL_TIMEOUT %s attempt=%d/%d err=%s",
                        label,
                        attempt,
                        self.config.download_max_retries,
                        msg,
                    )

                    if temp_dest.exists():
                        temp_dest.unlink()

                    if dest.exists():
                        dest.unlink()

                    if attempt == self.config.download_max_retries:
                        self.reporter.error("DL_FAIL %s max retries exhausted", label)
                        return False
        finally:
            if task_id is not None:
                self.reporter.remove_task(task_id)
            if temp_dest.exists():
                try:
                    temp_dest.unlink()
                except Exception:
                    pass

        return False

    def _execute_ffmpeg_process(
        self,
        cmd: list[str],
        duration_us: int | None,
        task_label: str,
    ) -> None:
        task_id = self.reporter.add_task(
            task_label,
            total=duration_us,
            is_ffmpeg=True,
        )
        proc: subprocess.Popen[str] | None = None

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
            )

            if proc.stdout:
                for line in proc.stdout:
                    if line.startswith("out_time_us="):
                        try:
                            us_val = line.strip().split("=")[1]
                            if us_val != "N/A":
                                us = int(us_val)

                                if duration_us and us > duration_us:
                                    us = duration_us

                                if us >= 0:
                                    self.reporter.update_task(task_id, completed=us)
                        except (ValueError, IndexError):
                            pass

            proc.wait()
            if proc.returncode != 0:
                raise subprocess.CalledProcessError(proc.returncode, proc.args)
        finally:
            self.reporter.remove_task(task_id)
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                    proc.wait()
                except Exception:
                    pass

    def download_and_process_video(
        self,
        metadata: PostItem,
        download_url: str,
    ) -> None:
        temp_raw = self.tmp_dir / f"temp_raw_{metadata.video_id}.mp4"
        temp_proc = self.tmp_dir / f"temp_proc_{metadata.video_id}.mp4"

        try:
            self.reporter.debug("DL_START %s", metadata.video_id)
            ok = self._download_file(
                download_url,
                temp_raw,
                metadata.video_id,
                max_size=self.config.max_file_size_bytes,
            )
            if not ok:
                return

            actual_size = temp_raw.stat().st_size
            if actual_size < self.config.min_file_size_bytes:
                self.reporter.warning(
                    "DL_SKIP %s too_small size=%d",
                    metadata.video_id,
                    actual_size,
                )
                return

            try:
                info = _probe_video_file(temp_raw)
            except subprocess.CalledProcessError:
                self.reporter.error("PROBE_FAIL %s corrupt file", metadata.video_id)
                return
            except (
                json.JSONDecodeError,
                IndexError,
                ValueError,
                TypeError,
            ) as e:
                self.reporter.warning(
                    "PROBE_SKIP %s unparseable stream info: %s",
                    metadata.video_id,
                    str(e),
                )
                return

            if info.min_dimension < self.config.min_video_height:
                self.reporter.debug(
                    "RES_SKIP %s res=%dx%d below %dp",
                    metadata.video_id,
                    info.width,
                    info.height,
                    self.config.min_video_height,
                )
                return

            iso_time = metadata.datetime_obj.strftime(ISO_DATETIME_FORMAT)
            if not info.is_shifted:
                self.reporter.debug(
                    "METADATA %s res=%dx%d start_time=%.3f",
                    metadata.target_filename,
                    info.width,
                    info.height,
                    info.start_time_s,
                )
                cmd = _build_ffmpeg_copy_cmd(temp_raw, temp_proc, iso_time)
                label = f"[magenta]META {metadata.video_id}"
            else:
                self.reporter.debug(
                    "ENCODE %s res=%dx%d start_time=%.3f -> HEVC",
                    metadata.target_filename,
                    info.width,
                    info.height,
                    info.start_time_s,
                )
                cmd = _build_ffmpeg_encode_cmd(temp_raw, temp_proc, iso_time)
                label = f"[magenta]ENC {metadata.video_id}"

            self._execute_ffmpeg_process(cmd, info.duration_us, label)
            shutil.move(str(temp_proc), str(metadata.output_path))
            _apply_file_timestamp(metadata.output_path, metadata.create_time)
            self.reporter.debug("DONE %s", metadata.target_filename)
        except subprocess.CalledProcessError:
            self.reporter.error("FFMPEG_FAIL %s", metadata.video_id)
        except Exception as e:
            self.reporter.error(
                "DL_ERR %s %s",
                metadata.video_id,
                str(e).split("\n")[0][:120],
            )
        finally:
            for p in (temp_raw, temp_proc):
                if p.exists():
                    try:
                        p.unlink()
                    except Exception:
                        pass

    def download_photo(self, photo: PhotoMetadata) -> bool:
        if photo.output_path.exists():
            return False

        self.reporter.debug("DL_PHOTO %s slide=%d", photo.post_id, photo.slide_index)
        ok = self._download_file(
            photo.download_url,
            photo.output_path,
            f"{photo.post_id}_s{photo.slide_index}",
        )

        if ok and photo.output_path.exists():
            _inject_exif_datetime(photo.output_path, photo.datetime_obj)
            _apply_file_timestamp(photo.output_path, photo.create_time)
            self.reporter.debug("DONE %s", photo.target_filename)
            return True

        return False

    def purge_low_res(self, output_dir: Path) -> None:
        self.reporter.debug(
            "PURGE_SCAN checking existing files for sub-%dp",
            self.config.min_video_height,
        )
        candidates: list[tuple[Path, int, int]] = []

        for mp4 in output_dir.glob("*.mp4"):
            try:
                probe_out = subprocess.check_output(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-select_streams",
                        "v:0",
                        "-show_entries",
                        "stream=width,height",
                        "-of",
                        "csv=p=0:s=x",
                        str(mp4),
                    ],
                    stderr=subprocess.DEVNULL,
                )

                dims_str = probe_out.decode("utf-8").strip()
                if dims_str and "x" in dims_str:
                    dims_parts = [p for p in dims_str.split("x") if p.strip()]
                    if len(dims_parts) >= 2:
                        w, h = int(dims_parts[0]), int(dims_parts[1])
                        if min(w, h) < self.config.min_video_height:
                            candidates.append((mp4, w, h))
            except Exception:
                pass

        if not candidates:
            return

        prompt = (
            f"Found {len(candidates)} sub-{self.config.min_video_height}p "
            "video(s) in output directory. Purge them?"
        )
        if not self.reporter.ask_confirm(prompt, default=False):
            self.reporter.info("PURGE_SKIPPED Sub-res files retained")
            return

        removed = 0
        for mp4, width, height in candidates:
            try:
                self.reporter.info("PURGE %s res=%dx%d", mp4.name, width, height)
                mp4.unlink()
                removed += 1
            except Exception as e:
                self.reporter.error("PURGE_FAIL %s: %s", mp4.name, e)

        self.reporter.info("PURGE_DONE removed=%d", removed)
