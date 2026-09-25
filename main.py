import argparse
import sys
from pathlib import Path

from ttdl.config import AppConfig
from ttdl.direct import DirectDownloader
from ttdl.live import LiveDownloader
from ttdl.logger import setup_logging
from ttdl.mass import TikTokDownloader
from ttdl.models import DateFilter
from ttdl.reporter import CliReporter


def main() -> None:
    workspace_dir = Path(__file__).resolve().parent
    config = AppConfig(workspace_dir)
    is_live = "-tl" in sys.argv or "--target-live" in sys.argv
    reporter = CliReporter(mode="live" if is_live else "mass")
    setup_logging(config.logs_dir)

    parser = argparse.ArgumentParser(
        description="TikTok Downloader",
        formatter_class=lambda prog: argparse.RawTextHelpFormatter(
            prog, max_help_position=40
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "-dl",
        "--direct-link",
        type=str,
        metavar="URL",
        help="Download directly from a TikTok URL. \nExample: -dl https://vt.tiktok.com/",
    )
    group.add_argument(
        "-tm",
        "--target-mass",
        type=str,
        metavar="USER",
        help="Download all from target username.\nExample: -tm username",
    )
    group.add_argument(
        "-tv",
        "--target-video",
        type=str,
        metavar="USER",
        help="Download videos only from target username.\nExample: -tv username",
    )
    group.add_argument(
        "-tp",
        "--target-photo",
        type=str,
        metavar="USER",
        help="Download photos only from target username.\nExample: -tp username",
    )
    group.add_argument(
        "-tl",
        "--target-live",
        type=str,
        metavar="USER",
        help="Download live stream from target username.\nExample: -tl username",
    )

    parser.add_argument(
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

    args = parser.parse_args()

    try:
        if args.direct_link:
            if args.date:
                reporter.warning("--date argument is ignored for direct link downloads")
            sys.exit(DirectDownloader(config, reporter, args.direct_link).execute())
        elif args.target_live:
            if args.date:
                reporter.warning("--date argument is ignored for live downloads")

            reporter.mode = "live"
            config.validate_dependencies(require_ffmpeg=True)
            sys.exit(LiveDownloader(args.target_live, config, reporter).execute())
        else:
            date_filter = DateFilter.build(args.date)

            if args.target_mass:
                username = args.target_mass
                mode = "all"
            elif args.target_video:
                username = args.target_video
                mode = "video"
            else:
                username = args.target_photo
                mode = "photo"

            config.validate_dependencies(require_ffmpeg=(mode != "photo"))
            sys.exit(
                TikTokDownloader(
                    target_username=username,
                    config=config,
                    reporter=reporter,
                    date_filter=date_filter,
                    mode=mode,
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
