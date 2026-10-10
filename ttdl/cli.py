import argparse
import logging
import os
import sys
from pathlib import Path

if "com.termux" in os.environ.get("PREFIX", ""):
    logging.basicConfig(level=logging.INFO, format="%(message)s")

try:
    from ttdl.config import AppConfig
    from ttdl.direct import DirectDownloader
    from ttdl.live import LiveDownloader
    from ttdl.logger import setup_logging
    from ttdl.models import DateFilter
    from ttdl.reporter import CliReporter
    from ttdl.utils import parse_url_input
except ImportError as e:
    print(f"\nMissing dependency '{e.name}'.")
    print("Please install requirements first by running:")
    print("pip install -r requirements.txt\n")
    sys.exit(1)


def _add_profile_options(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--date",
        type=str,
        help=(
            "Date filter (YYYY, YYYY-MM, YYYY-MM-DD) or range (start:end).\n"
            "Examples:\n"
            "  --date 2023                   (Whole year 2023)\n"
            "  --date 2023-10                (Whole month of Oct 2023)\n"
            "  --date 2023-12-25             (Specific single day)\n"
            "  --date 2021:2023              (Range: Jan 1, 2021 to Dec 31, 2023)\n"
            "  --date 2023-01:2023-06        (Range: Jan 1, 2023 to Jun 30, 2023)\n"
            "  --date 2024-12-22:2025-01-01  (Range: Dec 22, 2024 to Jan 1, 2025)\n"
            "  --date 2023-03-15:2024        (Mixed range: Mar 15, 2023 to Dec 31, 2024)"
        ),
    )
    p.add_argument(
        "--scrape",
        action="store_true",
        help="Scrape and update JSON cache only, do not download media",
    )
    p.add_argument(
        "--cached",
        action="store_true",
        help="Skip browser scraping, load from JSON cache and download directly",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Force full profile scrape, ignoring smart stop",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ttdl",
        description="TikTok Downloader - Videos, Photos, and Live Streams",
        formatter_class=lambda prog: argparse.RawTextHelpFormatter(
            prog, max_help_position=32
        ),
    )

    subparsers = parser.add_subparsers(
        dest="command",
        title="commands",
        metavar="{batch,video,photo,live,url}",
        required=True,
    )

    # Subcommand: batch
    p_batch = subparsers.add_parser(
        "batch",
        help="Download everything (videos and photos) from target profile",
        description="Download all posts (videos, photos, stories) from target username.",
    )
    p_batch.add_argument("user", type=str, help="Target TikTok username")
    _add_profile_options(p_batch)

    # Subcommand: video
    p_video = subparsers.add_parser(
        "video",
        help="Download videos only from target profile",
        description="Download videos only from target username.",
    )
    p_video.add_argument("user", type=str, help="Target TikTok username")
    _add_profile_options(p_video)

    # Subcommand: photo
    p_photo = subparsers.add_parser(
        "photo",
        help="Download photos only from target profile",
        description="Download photos only from target username.",
    )
    p_photo.add_argument("user", type=str, help="Target TikTok username")
    _add_profile_options(p_photo)

    # Subcommand: live
    p_live = subparsers.add_parser(
        "live",
        help="Record live stream from target profile",
        description="Capture ongoing live stream from target username.",
    )
    p_live.add_argument("user", type=str, help="Target TikTok username")

    # Subcommand: url
    p_url = subparsers.add_parser(
        "url",
        help="Download directly from video/photo URL or txt file",
        description="Download directly from a single URL or a file containing URLs.",
    )
    p_url.add_argument(
        "target",
        type=str,
        metavar="URL_OR_FILE",
        help="TikTok video/photo URL or path to text file listing URLs",
    )

    return parser


def main() -> None:
    workspace_dir = Path.cwd()
    config = AppConfig(workspace_dir)

    is_live = "live" in sys.argv
    reporter = CliReporter(mode="live" if is_live else "mass")
    setup_logging(config.logs_dir)

    parser = _build_parser()
    args = parser.parse_args()

    cmd = args.command
    cached: bool = getattr(args, "cached", False)
    needs_browser = cmd in ("batch", "video", "photo") and not cached

    if needs_browser and "com.termux" in os.environ.get("PREFIX", ""):
        from ttdl.termux import setup_termux

        setup_termux()

    if cmd in ("batch", "video", "photo"):
        try:
            from ttdl.mass import TikTokDownloader
        except ImportError as e:
            print(f"\nMissing dependency '{e.name}'.")
            print("Please install requirements first by running:")
            print("pip install -r requirements.txt\n")
            sys.exit(1)

    try:
        if cmd == "url":
            url_target: str = args.target
            urls = parse_url_input(url_target)
            sys.exit(DirectDownloader(config, reporter, urls).execute())

        if cmd == "live":
            username: str = args.user
            reporter.mode = "live"
            config.validate_dependencies(require_ffmpeg=True)
            sys.exit(LiveDownloader(username, config, reporter).execute())

        if cmd in ("batch", "video", "photo"):
            username = args.user
            date_filter = DateFilter.build(args.date)
            mode = "all" if cmd == "batch" else cmd

            config.validate_dependencies(require_ffmpeg=(mode != "photo"))
            sys.exit(
                TikTokDownloader(
                    target_username=username,
                    config=config,
                    reporter=reporter,
                    date_filter=date_filter,
                    mode=mode,
                    jsononly=args.scrape,
                    usejson=args.cached,
                    force=args.force,
                ).execute()
            )
    except ValueError as ve:
        reporter.error(f"FATAL {ve}")
        sys.exit(1)
    except RuntimeError as re:
        reporter.error(f"FATAL {re}")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nINTERRUPT execution aborted by user")
        sys.exit(130)
