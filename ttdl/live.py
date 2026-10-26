import http.cookiejar
import json
import logging
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Final, cast

from ttdl.config import AppConfig
from ttdl.events import Reporter

log = logging.getLogger(__name__)

QUALITY_TIERS: Final[dict[str, int]] = {
    "origion": 1000,
    "origin": 1000,
    "uhd_60": 900,
    "uhd": 800,
    "full_hd1": 700,
    "full_hd": 700,
    "hd_60": 600,
    "hd1": 500,
    "hd": 500,
    "sd2": 400,
    "sd": 400,
    "sd1": 300,
    "ld": 200,
}

DEFAULT_LIVE_UA: Final[str] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
TIKTOK_ROOM_API: Final[str] = "https://www.tiktok.com/api-live/user/room"
WEBCAST_INFO_API: Final[str] = "https://webcast.tiktok.com/webcast/room/info/?aid=1988"
WAF_RESTRICTED_CODE: Final[int] = 4003110
BYTES_IN_MB: Final[int] = 1048576
BYTES_IN_KB: Final[int] = 1024


def _calc_quality_tier(q_name: str, res: str) -> int:
    qn = q_name.lower()
    tier = QUALITY_TIERS.get(qn, 50)
    if tier != 50:
        return tier

    if "orig" in qn:
        return 1000

    if "uhd" in qn or "1080" in qn or "1080" in res:
        return 800

    if "full_hd" in qn or "720" in qn or "720" in res:
        return 700

    if "hd" in qn:
        return 500

    if "sd" in qn or "480" in qn or "480" in res:
        return 400

    if "ld" in qn or "360" in qn or "360" in res:
        return 200

    return 50


def _calc_resolution_pixels(res: str) -> int:
    if "x" in res:
        try:
            w, h = res.split("x", 1)
            return int(w) * int(h)
        except (ValueError, TypeError):
            pass

    return 0


def _format_stream_details(resolution: str, vbitrate: int) -> str:
    details: list[str] = []

    if resolution:
        details.append(resolution)

    if vbitrate:
        if vbitrate >= 100_000:
            details.append(f"{vbitrate / 1_000_000:.1f}Mbps")
        else:
            details.append(f"{vbitrate}kbps")

    return f"({', '.join(details)})" if details else ""


def _extract_sdk_candidates(sdk_str: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    try:
        parsed = json.loads(sdk_str)
        data_map = parsed.get("data", {})

        for q_name, q_val in data_map.items():
            main = q_val.get("main", {})
            flv_url = main.get("flv")
            hls_url = main.get("hls")
            sdk_params_str = main.get("sdk_params") or "{}"
            try:
                sdk_params = json.loads(sdk_params_str)
            except Exception:
                sdk_params = {}

            vbitrate = int(sdk_params.get("vbitrate", 0))
            res = str(sdk_params.get("resolution", ""))
            tier = _calc_quality_tier(q_name, res)
            pixels = _calc_resolution_pixels(res)

            target_url = flv_url or hls_url
            if target_url:
                candidates.append(
                    {
                        "name": q_name,
                        "url": target_url,
                        "tier": tier,
                        "pixels": pixels,
                        "vbitrate": vbitrate,
                        "resolution": res,
                        "format": "flv" if target_url == flv_url else "m3u8",
                    }
                )
    except Exception:
        pass

    return candidates


def _extract_flv_candidates(flv_pull: Any) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    if isinstance(flv_pull, dict):
        for q_name, q_url in flv_pull.items():
            if q_url:
                tier = _calc_quality_tier(q_name, "")
                candidates.append(
                    {
                        "name": q_name,
                        "url": q_url,
                        "tier": tier,
                        "pixels": 0,
                        "vbitrate": 0,
                        "resolution": "",
                        "format": "flv",
                    }
                )

    return candidates


def _extract_direct_candidates(
    stream_url_data: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    for direct_key in ("hls_pull_url", "rtmp_pull_url"):
        d_url = stream_url_data.get(direct_key)
        if d_url:
            candidates.append(
                {
                    "name": direct_key.split("_")[0],
                    "url": d_url,
                    "tier": 500,
                    "pixels": 0,
                    "vbitrate": 0,
                    "resolution": "",
                    "format": "m3u8" if "hls" in direct_key else "rtmp",
                }
            )

    return candidates


def _select_highest_quality_stream(
    stream_url_data: dict[str, Any],
) -> tuple[str, str, str]:
    candidates: list[dict[str, Any]] = []

    sdk_str = (
        stream_url_data.get("live_core_sdk_data", {})
        .get("pull_data", {})
        .get("stream_data")
    )
    if sdk_str:
        candidates.extend(_extract_sdk_candidates(sdk_str))

    candidates.extend(_extract_flv_candidates(stream_url_data.get("flv_pull_url")))
    candidates.extend(_extract_direct_candidates(stream_url_data))
    if not candidates:
        raise ValueError("No stream URLs found in live metadata")

    candidates.sort(key=lambda c: (c["tier"], c["pixels"], c["vbitrate"]), reverse=True)
    best = candidates[0]
    details_str = _format_stream_details(best["resolution"], best["vbitrate"])
    return str(best["url"]), str(best["name"]), details_str


class LiveDownloader:
    def __init__(
        self,
        target: str,
        config: AppConfig,
        reporter: Reporter,
        cookies_path: str | Path | None = None,
    ) -> None:
        self.target = target.lstrip("@")
        self.config = config
        self.reporter = reporter
        self.cookies_path = Path(cookies_path) if cookies_path else None
        self._user_agent = DEFAULT_LIVE_UA

    def _fetch_json(self, url: str, headers: dict[str, str]) -> dict[str, Any]:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return cast(dict[str, Any], data) if isinstance(data, dict) else {}
        except Exception as e:
            curl_bin = shutil.which("curl")
            if curl_bin:
                cmd = [curl_bin, "-s", url]
                for k, v in headers.items():
                    cmd.extend(["-H", f"{k}: {v}"])

                res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
                if res.returncode == 0 and res.stdout.strip():
                    try:
                        data = json.loads(res.stdout)
                        if isinstance(data, dict):
                            return cast(dict[str, Any], data)
                    except Exception:
                        pass
            raise e

    def _check_live_status(self) -> tuple[bool, str | None]:
        url = f"{TIKTOK_ROOM_API}?aid=1988&sourceType=54&uniqueId={self.target}"
        headers = {
            "User-Agent": self._user_agent,
            "Referer": f"https://www.tiktok.com/@{self.target}/live",
            "Accept": "application/json, text/plain, */*",
        }
        data = self._fetch_json(url, headers).get("data", {})
        user_data = data.get("user", {})
        live_room = data.get("liveRoom", {})

        status = user_data.get("status") or live_room.get("status")
        room_id = user_data.get("roomId")
        return (status == 2), (str(room_id) if room_id else None)

    def _find_cookies_file(self) -> Path | None:
        if self.cookies_path:
            if not self.cookies_path.is_file():
                self.reporter.error("FILE_NOT_FOUND cookies=%s", self.cookies_path)
                return None
            return self.cookies_path

        default_cookie = self.config.config_dir / "cookies.txt"
        if default_cookie.is_file():
            return default_cookie

        return None

    def _load_cookie_header(self, cookie_file: Path | None = None) -> str | None:
        target_file = cookie_file or self._find_cookies_file()
        if not target_file:
            return None

        try:
            cj = http.cookiejar.MozillaCookieJar(target_file)
            cj.load(ignore_discard=True, ignore_expires=True)
            cookies_dict = {c.name: c.value for c in cj}
            if cookies_dict:
                return "; ".join(f"{k}={v}" for k, v in cookies_dict.items())
        except Exception:
            pass

        try:
            lines = target_file.read_text(encoding="utf-8").splitlines()
            pairs = []

            for line in lines:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue

                if "\t" in stripped:
                    parts = stripped.split("\t")
                    if len(parts) >= 7:
                        pairs.append(f"{parts[5]}={parts[6]}")

                elif "=" in stripped:
                    pairs.append(stripped)

            if pairs:
                return "; ".join(pairs)
        except Exception:
            pass

        return None

    def _resolve_stream_url(
        self, room_id: str, cookie_header: str | None
    ) -> tuple[str, str, str]:
        url = f"{WEBCAST_INFO_API}&room_id={room_id}"
        headers = {
            "User-Agent": self._user_agent,
            "Referer": "https://www.tiktok.com/",
            "Accept": "application/json, text/plain, */*",
        }
        if cookie_header:
            headers["Cookie"] = cookie_header

        data = self._fetch_json(url, headers)
        status_code = data.get("status_code", 0)
        if status_code == WAF_RESTRICTED_CODE:
            raise RuntimeError(
                f"session restricted by WAF ({WAF_RESTRICTED_CODE}). "
                "Please run 'ttdl login' to authenticate."
            )

        room_data = data.get("data", {})
        stream_url = room_data.get("stream_url")
        if not stream_url:
            raise RuntimeError("Unable to extract stream_url from live metadata")

        return _select_highest_quality_stream(stream_url)

    def _finalize_recording(self, raw_file: Path, final_file: Path) -> None:
        if not raw_file.exists() or raw_file.stat().st_size == 0:
            return

        self.reporter.info("FINALIZE Finalizing recording...")
        remux_cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(raw_file),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(final_file),
        ]

        try:
            res = subprocess.run(
                remux_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if (
                res.returncode == 0
                and final_file.exists()
                and final_file.stat().st_size > 0
            ):
                raw_file.unlink(missing_ok=True)
                return
        except Exception:
            pass

        if raw_file.exists():
            raw_file.replace(final_file)

    def execute(self) -> int:
        self.reporter.info("EXEC_START target=%s mode=live", self.target)
        live_out_dir = self.config.downloads_dir / "live" / self.target
        self.reporter.info("OUT %s", live_out_dir)

        try:
            is_live, room_id = self._check_live_status()
        except Exception as e:
            self.reporter.error("STATUS_FAIL %s", str(e))
            return 1

        if not is_live or not room_id:
            self.reporter.warning(
                "OFFLINE target=%s is not currently live", self.target
            )
            return 0

        self.reporter.info("LIVE_FOUND room_id=%s", room_id)
        cookie_file = self._find_cookies_file()
        if not cookie_file:
            self.reporter.error(
                "AUTH_FAIL cookies not found. Please run 'ttdl login' first to authenticate."
            )
            return 1

        self.reporter.debug("COOKIE_LOAD %s", cookie_file)
        cookie_header = self._load_cookie_header(cookie_file)

        try:
            stream_url, q_name, q_details = self._resolve_stream_url(
                room_id, cookie_header
            )
        except Exception as e:
            self.reporter.error("RESOLVE_FAIL %s", str(e))
            return 1

        info_label = f"{q_name} {q_details}".strip()
        self.reporter.info("QUALITY %s", info_label)

        live_out_dir.mkdir(parents=True, exist_ok=True)
        timestamp = int(time.time())
        out_file = live_out_dir / f"{self.target}_live_{room_id}_{timestamp}.mp4"
        raw_file = live_out_dir / f"{self.target}_live_{room_id}_{timestamp}.part.mp4"

        tracker = _SizeTracker(self.target, self.config.downloads_dir, self.reporter)
        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-headers",
            f"User-Agent: {self._user_agent}\r\nReferer: https://www.tiktok.com/\r\n",
            "-i",
            stream_url,
            "-c",
            "copy",
            "-movflags",
            "+frag_keyframe+empty_moov",
            str(raw_file),
        ]

        self.reporter.info("REC_START target=%s file=%s", self.target, out_file.name)
        proc: subprocess.Popen[bytes] | None = None

        err_log = self.config.tmp_dir / f"ffmpeg_live_{timestamp}.log"
        err_file = None

        try:
            with self.reporter:
                tracker.start()
                err_file = open(err_log, "w+", encoding="utf-8", errors="replace")
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=err_file,
                )
                proc.wait()

            tracker.stop()

            if proc.returncode != 0 and (
                not raw_file.exists() or raw_file.stat().st_size == 0
            ):
                if raw_file.exists():
                    raw_file.unlink(missing_ok=True)

                err_detail = ""
                if err_log.exists():
                    try:
                        lines = [
                            ln.strip()
                            for ln in err_log.read_text(
                                encoding="utf-8", errors="replace"
                            ).splitlines()
                            if ln.strip()
                        ]
                        if lines:
                            err_detail = f": {lines[-1]}"
                    except Exception:
                        pass

                self.reporter.error("REC_FAIL FFmpeg recording failed%s", err_detail)
                return 1

            self.reporter.info("STREAM_END file=%s", out_file.name)
            self._finalize_recording(raw_file, out_file)
            self.reporter.info("EXEC_DONE Live stream recording complete")
            self.reporter.print_summary(
                "Live Stream Complete",
                [
                    ("Target", f"@{self.target}"),
                    ("Room ID", str(room_id)),
                    ("Quality", info_label),
                    ("Output", str(out_file)),
                ],
                style="green",
            )
            return 0
        except KeyboardInterrupt:
            tracker.stop()
            if proc and proc.poll() is None:
                try:
                    if proc.stdin:
                        proc.stdin.write(b"q")
                        proc.stdin.flush()
                    proc.wait(timeout=3)
                except Exception:
                    proc.terminate()
            self.reporter.info(
                "REC_INTERRUPT saving recorded stream to %s", out_file.name
            )
            self._finalize_recording(raw_file, out_file)
            self.reporter.info("EXEC_DONE Live stream recording stopped by user")
            self.reporter.print_summary(
                "Live Stream Interrupted",
                [
                    ("Target", f"@{self.target}"),
                    ("Room ID", str(room_id)),
                    ("Quality", info_label),
                    ("Output", str(out_file)),
                ],
                style="yellow",
            )
            return 0
        except Exception as e:
            tracker.stop()
            if raw_file.exists() and not out_file.exists():
                self._finalize_recording(raw_file, out_file)
            self.reporter.error("REC_FAIL %s", str(e))
            return 1
        finally:
            tracker.stop()
            if err_file and not err_file.closed:
                try:
                    err_file.close()
                except Exception:
                    pass
            if err_log.exists():
                try:
                    err_log.unlink()
                except Exception:
                    pass


class _SizeTracker:
    def __init__(self, target: str, downloads_dir: Path, reporter: Reporter) -> None:
        self.out_dir = downloads_dir / "live" / target
        self.reporter = reporter
        self.active = False
        self.worker = threading.Thread(target=self._scan, daemon=True)

    def start(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.reporter.on_live_update("0.00MB", "0.00KB/s")
        self.active = True
        self.worker.start()

    def stop(self) -> None:
        self.active = False

    def _scan(self) -> None:
        last_b = 0
        last_t = time.time()

        while self.active:
            time.sleep(0.5)
            try:
                items: list[str] = [
                    os.path.join(self.out_dir, f) for f in os.listdir(self.out_dir)
                ]
                if not items:
                    continue

                latest = max(items, key=os.path.getmtime)
                b = os.path.getsize(latest)
            except Exception:
                continue

            now = time.time()
            dt = now - last_t
            if dt >= 1.0:
                speed = (b - last_b) / dt if b > last_b else 0.0
                self.reporter.on_live_update(
                    size=f"{b / BYTES_IN_MB:.2f}MB",
                    speed=f"{speed / BYTES_IN_KB:.2f}KB/s",
                )
                last_b = b
                last_t = now
