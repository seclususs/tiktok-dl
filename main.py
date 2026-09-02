import argparse
import sys
from pathlib import Path

from ttdl.live import run_dl
from ttdl.logger import console, setup_logging
from ttdl.mass import TikTokDownloader
from ttdl.models import DateFilter


def main() -> None:
    setup_logging()

    parser = argparse.ArgumentParser(
        description="TikTok Downloader",
        formatter_class=lambda prog: argparse.RawTextHelpFormatter(
            prog, max_help_position=40
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
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

    workspace_dir = Path(__file__).resolve().parent

    try:
        if args.target_live:
            if args.date:
                console.print(
                    "[yellow]WARNING[/] --date argument is ignored for live downloads"
                )
            sys.exit(run_dl(args.target_live, workspace_dir))
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

            TikTokDownloader(
                username,
                workspace_dir=workspace_dir,
                mode=mode,
                date_filter=date_filter,
            ).execute()
    except ValueError as ve:
        if "Invalid date format" in str(ve):
            console.print(f"[bold red]FATAL[/] {ve}")
            sys.exit(1)
        raise
    except RuntimeError as re:
        if "NO BROWSER FOUND" in str(re):
            console.print(f"[FATAL] {re}", style="bold red")
            sys.exit(1)
        raise


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[bold bright_yellow]INTERRUPT[/] execution aborted by user")
        sys.exit(130)
