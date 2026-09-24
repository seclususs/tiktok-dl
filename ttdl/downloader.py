import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

import requests
from playwright.sync_api import BrowserContext
from rich.progress import Progress

from ttdl.config import AppConfig
from ttdl.models import PhotoMetadata, PostItem

log = logging.getLogger(__name__)


class MediaDownloader:
    def __init__(self, config: AppConfig):
        self.config = config
        self.tmp_dir = config.tmp_dir

    def _download_file(
        self,
        context: BrowserContext,
        url: str,
        dest: Path,
        label: str,
        progress: Progress,
        max_size: int | None = None,
    ) -> bool:
        cookies = {c["name"]: c["value"] for c in context.cookies()}
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/127.0.0.0 "
                "Safari/537.36 Edg/127.0.0.0"
            ),
            "Referer": "https://musicaldown.com/",
        }

        temp_dest = dest.with_suffix(dest.suffix + ".download")

        for attempt in range(1, self.config.download_max_retries + 1):
            task_id = None
            try:
                resp = requests.get(
                    url,
                    stream=True,
                    headers=headers,
                    cookies=cookies,
                    timeout=self.config.download_timeout,
                )
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", "0"))
                if max_size and total > max_size:
                    resp.close()
                    log.info(
                        "[yellow]SIZE_SKIP[/] %s content_length=%dMB exceeds %dMB",
                        label,
                        total // 1_000_000,
                        max_size // 1_000_000,
                    )
                    return False

                task_id = progress.add_task(
                    f"[cyan]DL {label}",
                    total=total if total > 0 else None,
                )
                downloaded = 0
                aborted = False
                with open(temp_dest, "wb") as f:
                    for chunk in resp.iter_content(
                        chunk_size=self.config.download_chunk_size,
                    ):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            progress.update(task_id, advance=len(chunk))
                            if max_size and downloaded > max_size:
                                aborted = True
                                break

                progress.remove_task(task_id)
                if aborted:
                    log.info(
                        "[yellow]SIZE_SKIP[/] %s downloaded=%dMB exceeds %dMB",
                        label,
                        downloaded // 1_000_000,
                        (max_size or 0) // 1_000_000,
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
                    progress.remove_task(task_id)
                msg = str(e).split("\n")[0][:80]
                log.warning(
                    "[bright_yellow]DL_TIMEOUT[/] %s attempt=%d/%d err=%s",
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
                    log.error(
                        "[red]DL_FAIL[/] %s max retries exhausted",
                        label,
                    )
                    return False

        return False

    def download_and_process_video(
        self,
        context: BrowserContext,
        metadata: PostItem,
        download_url: str,
        progress: Progress,
    ) -> None:
        temp_raw = self.tmp_dir / f"temp_raw_{metadata.video_id}.mp4"
        temp_proc = self.tmp_dir / f"temp_proc_{metadata.video_id}.mp4"

        try:
            log.info("[magenta]DL_START[/] %s", metadata.video_id)
            ok = self._download_file(
                context,
                download_url,
                temp_raw,
                metadata.video_id,
                progress,
                max_size=self.config.max_file_size_bytes,
            )
            if not ok:
                return

            actual_size = temp_raw.stat().st_size
            if actual_size < self.config.min_file_size_bytes:
                try:
                    temp_raw.read_text(encoding="utf-8")[:250]
                    log.warning(
                        "[bright_yellow]DL_SKIP[/] %s not_video size=%d",
                        metadata.video_id,
                        actual_size,
                    )
                except Exception:
                    log.warning(
                        "[bright_yellow]DL_SKIP[/] %s too_small size=%d",
                        metadata.video_id,
                        actual_size,
                    )
                return

            try:
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
                        str(temp_raw),
                    ],
                    stderr=subprocess.DEVNULL,
                )

                probe_data = json.loads(probe_out.decode("utf-8"))
                if not probe_data.get("streams"):
                    raise ValueError("No video stream found")
                stream_info = probe_data["streams"][0]

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

            except subprocess.CalledProcessError:
                log.error("[red]PROBE_FAIL[/] %s corrupt file", metadata.video_id)
                return
            except (json.JSONDecodeError, IndexError, ValueError, TypeError) as e:
                log.warning(
                    "[bright_yellow]PROBE_SKIP[/] %s unparseable stream info: %s",
                    metadata.video_id,
                    str(e),
                )
                return

            if min(width, height) < self.config.min_video_height:
                log.info(
                    "[yellow]RES_SKIP[/] %s res=%dx%d below %dp",
                    metadata.video_id,
                    width,
                    height,
                    self.config.min_video_height,
                )
                return

            iso_time = metadata.datetime_obj.strftime("%Y-%m-%dT%H:%M:%SZ")
            duration_us = int(duration_s * 1_000_000) if duration_s > 0 else None

            is_shifted = (start_time_s < 0.0) or (start_time_s >= 0.110)

            if not is_shifted:
                log.info(
                    "[magenta]METADATA[/] %s res=%dx%d start_time=%.3f",
                    metadata.target_filename,
                    width,
                    height,
                    start_time_s,
                )
                task_id = progress.add_task(
                    f"[magenta]METADATA {metadata.video_id}", total=duration_us
                )
                proc = subprocess.Popen(
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        str(temp_raw),
                        "-c",
                        "copy",
                        "-metadata",
                        f"creation_time={iso_time}",
                        "-progress",
                        "pipe:1",
                        "-nostats",
                        str(temp_proc),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                )
            else:
                log.info(
                    "[magenta]ENCODE[/] %s res=%dx%d start_time=%.3f -> HEVC",
                    metadata.target_filename,
                    width,
                    height,
                    start_time_s,
                )
                task_id = progress.add_task(
                    f"[magenta]ENCODE {metadata.video_id}", total=duration_us
                )
                proc = subprocess.Popen(
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        str(temp_raw),
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
                        str(temp_proc),
                    ],
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
                                    progress.update(task_id, completed=us)
                        except ValueError:
                            pass

            proc.wait()
            progress.remove_task(task_id)
            if proc.returncode != 0:
                raise subprocess.CalledProcessError(proc.returncode, proc.args)

            shutil.move(str(temp_proc), str(metadata.output_path))

            os.utime(
                str(metadata.output_path),
                (metadata.create_time, metadata.create_time),
            )
            log.info("[green]DONE[/] %s", metadata.target_filename)
        except subprocess.CalledProcessError:
            log.error("[red]FFMPEG_FAIL[/] %s", metadata.video_id)
        except Exception as e:
            log.error(
                "[red]DL_ERR[/] %s %s",
                metadata.video_id,
                str(e).split("\n")[0][:120],
            )
        finally:
            if temp_raw.exists():
                temp_raw.unlink()
            if temp_proc.exists():
                temp_proc.unlink()

    def download_photo(
        self,
        context: BrowserContext,
        photo: PhotoMetadata,
        progress: Progress,
    ) -> bool:
        if photo.output_path.exists():
            return False

        log.info(
            "[magenta]DL_PHOTO[/] %s slide=%d",
            photo.post_id,
            photo.slide_index,
        )
        ok = self._download_file(
            context,
            photo.download_url,
            photo.output_path,
            f"{photo.post_id}_s{photo.slide_index}",
            progress,
        )

        if ok and photo.output_path.exists():
            os.utime(
                str(photo.output_path),
                (photo.create_time, photo.create_time),
            )
            log.info("[green]DONE[/] %s", photo.target_filename)
            return True
        return False

    def purge_low_res(self, output_dir: Path) -> None:
        log.info(
            "[magenta]PURGE_SCAN[/] checking existing files for sub-%dp",
            self.config.min_video_height,
        )
        removed = 0

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
                        try:
                            width, height = int(dims_parts[0]), int(dims_parts[1])
                        except ValueError:
                            continue
                        if min(width, height) < self.config.min_video_height:
                            log.info(
                                "[magenta]PURGE[/] %s res=%dx%d",
                                mp4.name,
                                width,
                                height,
                            )
                            mp4.unlink()
                            removed += 1
            except Exception:
                pass

        log.info("[green]PURGE_DONE[/] removed=%d", removed)
