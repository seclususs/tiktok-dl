import argparse
import logging
import os
import shutil
import sys
from pathlib import Path

if "com.termux" in os.environ.get("PREFIX", ""):
    logging.basicConfig(level=logging.INFO, format="%(message)s")

try:
    from ttdl import __version__
    from ttdl.config import AppConfig, clean_cache, format_size, open_config_editor
    from ttdl.direct import DirectDownloader
    from ttdl.live import LiveDownloader
    from ttdl.logger import setup_logging
    from ttdl.models import DateFilter
    from ttdl.reporter import CliReporter
    from ttdl.utils import parse_url_input
except ImportError as e:
    print(f"\nMissing dependency '{e.name}'.")
    print("Please install requirements first by running:")
    print("pip install .\n")
    sys.exit(1)


def _add_profile_options(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--date",
        type=str,
        help=(
            "Date filter (YYYY, YYYY-MM, YYYY-MM-DD)\n"
            "or range (start:end).\n"
            "Examples:\n"
            "  2023                   (Whole year 2023)\n"
            "  2023-10        (Whole month of Oct 2023)\n"
            "  2023-12-25         (Specific single day)\n"
            "  2021:2023   (Jan 1, 2021 - Dec 31, 2023)\n"
            "  2023-01:2023-06   (Jan 1 - Jun 30, 2023)\n"
            "  2024-12-22:2025-01-01        (Day range)\n"
            "  2023-03-15:2024            (Mixed range)"
        ),
    )
    p.add_argument(
        "--scrape",
        action="store_true",
        help="Scrape & cache metadata only,\ndo not download media",
    )
    p.add_argument(
        "--cached",
        action="store_true",
        help="Skip browser scraping, load from\nJSON cache and download directly",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Force full profile scrape, ignoring\nconsecutive duplicate stop",
    )


def _build_parser() -> argparse.ArgumentParser:
    base_parser = argparse.ArgumentParser(add_help=False)
    base_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Enable detailed debug logging",
    )

    def raw_formatter(prog: str) -> argparse.HelpFormatter:
        term_width = max(24, min(shutil.get_terminal_size((62, 20)).columns, 80))
        return argparse.RawTextHelpFormatter(
            prog,
            max_help_position=18,
            width=term_width,
        )

    epilog = (
        "profile options (for batch, video, photo):\n"
        "  --date DATE     Filter posts by date or range\n"
        "                  (e.g. 2023, 2023-10, 2022:2023)\n"
        "  --scrape        Scrape & cache only, no media\n"
        "  --cached        Download from JSON cache directly\n"
        "  --force         Force full profile scrape\n"
        "\n"
        "Run 'ttdl <command> --help' for command details."
    )

    parser = argparse.ArgumentParser(
        prog="ttdl",
        description="TikTok Downloader - Videos, Photos, and Live Streams",
        epilog=epilog,
        parents=[base_parser],
        formatter_class=raw_formatter,
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        title="commands",
        metavar="<command>",
        required=True,
    )

    # Subcommand: batch
    p_batch = subparsers.add_parser(
        "batch",
        parents=[base_parser],
        help="Download all media from target profile",
        description="Download all posts (videos, photos, stories)\nfrom target username.",
        formatter_class=raw_formatter,
    )
    p_batch.add_argument("user", type=str, help="Target TikTok username")
    _add_profile_options(p_batch)

    # Subcommand: video
    p_video = subparsers.add_parser(
        "video",
        parents=[base_parser],
        help="Download videos only from profile",
        description="Download videos only from target username.",
        formatter_class=raw_formatter,
    )
    p_video.add_argument("user", type=str, help="Target TikTok username")
    _add_profile_options(p_video)

    # Subcommand: photo
    p_photo = subparsers.add_parser(
        "photo",
        parents=[base_parser],
        help="Download photos only from profile",
        description="Download photos only from target username.",
        formatter_class=raw_formatter,
    )
    p_photo.add_argument("user", type=str, help="Target TikTok username")
    _add_profile_options(p_photo)

    # Subcommand: live
    p_live = subparsers.add_parser(
        "live",
        parents=[base_parser],
        help="Record live stream from profile",
        description="Capture ongoing live stream from target username.",
        formatter_class=raw_formatter,
    )
    p_live.add_argument("user", type=str, help="Target TikTok username")
    p_live.add_argument(
        "--cookies",
        type=str,
        default=None,
        help="Path to cookies file\n(Netscape format / cookies.txt)",
    )

    # Subcommand: url
    p_url = subparsers.add_parser(
        "url",
        parents=[base_parser],
        help="Download from single URL or list file",
        description="Download directly from a single URL or\na file containing URLs.",
        formatter_class=raw_formatter,
    )
    p_url.add_argument(
        "target",
        type=str,
        metavar="URL_OR_FILE",
        help="TikTok video/photo URL or path to\ntext file listing URLs",
    )

    # Subcommand: config
    p_config = subparsers.add_parser(
        "config",
        parents=[base_parser],
        help="View, edit, or set configuration values",
        description="Manage ttdl settings in config file.",
        formatter_class=raw_formatter,
    )
    p_config.add_argument(
        "action",
        nargs="?",
        choices=["show", "path", "edit", "set", "get", "reset"],
        metavar="ACTION",
        default="show",
        help="Action: show, path, edit, set, get, reset\n(default: show)",
    )
    p_config.add_argument("key", nargs="?", help="Configuration key (for set/get)")
    p_config.add_argument("value", nargs="?", help="Value to set (for set)")
    p_config.add_argument(
        "--edit",
        action="store_true",
        help="Open config file in system text editor",
    )
    p_config.add_argument(
        "--path",
        action="store_true",
        help="Print path to active config file",
    )
    p_config.add_argument(
        "--show",
        action="store_true",
        help="Print active configuration content",
    )
    p_config.add_argument(
        "--reset",
        action="store_true",
        help="Reset configuration to default template",
    )

    # Subcommand: clean
    p_clean = subparsers.add_parser(
        "clean",
        parents=[base_parser],
        help="Purge cache, logs, and temp files",
        description="Clean temporary media chunks, browser sessions,\nlogs, and JSON cache.",
        formatter_class=raw_formatter,
    )
    p_clean.add_argument(
        "--all",
        action="store_true",
        help="Purge everything including profile\nJSON cache",
    )
    p_clean.add_argument(
        "--json",
        action="store_true",
        help="Purge only profile JSON metadata cache",
    )
    p_clean.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip confirmation prompt and proceed",
    )

    # Subcommand: login
    subparsers.add_parser(
        "login",
        parents=[base_parser],
        help="Log in to TikTok and save cookies",
        description="Opens browser to authenticate and export\nsession cookies.",
        formatter_class=raw_formatter,
    )

    return parser


def main() -> None:
    reporter: CliReporter | None = None
    try:
        workspace_dir = Path.cwd()
        config = AppConfig(workspace_dir)

        parser = _build_parser()
        args = parser.parse_args()

        verbose: bool = getattr(args, "verbose", False)
        cmd = args.command
        reporter = CliReporter(
            mode="live" if cmd == "live" else "mass", verbose=verbose
        )
        if cmd != "clean":
            setup_logging(config.logs_dir, verbose=verbose)

        cached: bool = getattr(args, "cached", False)
        needs_browser = cmd in ("batch", "video", "photo", "login") and not cached

        if needs_browser and "com.termux" in os.environ.get("PREFIX", ""):
            from ttdl.termux import setup_termux

            setup_termux(reporter)

        if cmd in ("batch", "video", "photo", "login"):
            try:
                import playwright  # noqa: F401

                if cmd in ("batch", "video", "photo"):
                    from ttdl.mass import TikTokDownloader
            except ImportError as e:
                print(f"\nMissing dependency '{e.name}'.")
                print("Please install requirements first by running:")
                print("pip install .\n")
                sys.exit(1)

        if cmd == "config":
            action = args.action

            if args.edit or action == "edit":
                reporter.info("OPEN_CONFIG %s", config.cfg_path)
                open_config_editor(config.cfg_path)
                sys.exit(0)

            if args.path or action == "path":
                print(str(config.cfg_path))
                sys.exit(0)

            if args.reset or action == "reset":
                config.reset_config()
                reporter.info("CONFIG_RESET %s", config.cfg_path)
                sys.exit(0)

            if action == "set":
                if not args.key or args.value is None:
                    reporter.error("USAGE: ttdl config set <key> <value>")
                    sys.exit(1)

                success = config.set_setting(args.key, args.value)
                if success:
                    reporter.info("CONFIG_SET %s = %s", args.key, args.value)
                    sys.exit(0)
                else:
                    reporter.error("UNKNOWN_KEY or INVALID_VALUE: %s", args.key)
                    sys.exit(1)

            if action == "get":
                if not args.key:
                    reporter.error("USAGE: ttdl config get <key>")
                    sys.exit(1)

                val = config.get_setting(args.key)
                if val is not None:
                    print(val)
                    sys.exit(0)
                else:
                    reporter.error("UNKNOWN_KEY: %s", args.key)
                    sys.exit(1)

            if config.cfg_path.is_file():
                print(f"# Active config: {config.cfg_path}\n")
                print(config.cfg_path.read_text(encoding="utf-8"))
            else:
                reporter.warning("Config file not found at %s", config.cfg_path)
            sys.exit(0)

        if cmd == "clean":
            mode_str = "ALL" if args.all else ("JSON" if args.json else "TEMP")
            if not getattr(args, "yes", False):
                prompt = f"Confirm purging {mode_str.lower()} cache, logs, and temporary files?"
                if not reporter.ask_confirm(prompt, default=False):
                    reporter.info("CLEAN_CANCEL Purge cancelled by user")
                    sys.exit(0)

            for h in logging.root.handlers[:]:
                logging.root.removeHandler(h)
                h.close()

            files_removed, bytes_freed = clean_cache(
                config, clean_all=args.all, clean_json=args.json
            )
            reporter.info(
                "CLEAN_DONE mode=%s removed=%d freed=%s",
                mode_str,
                files_removed,
                format_size(bytes_freed),
            )
            sys.exit(0)

        if cmd == "url":
            url_target: str = args.target
            urls = parse_url_input(url_target)
            sys.exit(DirectDownloader(config, reporter, urls).execute())

        if cmd == "login":
            from ttdl.auth import AuthManager

            sys.exit(AuthManager(config, reporter).login())

        if cmd == "live":
            username: str = args.user
            cookies_arg = getattr(args, "cookies", None)
            reporter.mode = "live"
            config.validate_dependencies(require_ffmpeg=True)
            sys.exit(
                LiveDownloader(
                    username, config, reporter, cookies_path=cookies_arg
                ).execute()
            )

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
    except KeyboardInterrupt:
        if reporter is not None:
            reporter.clear_status()
            reporter.warning("INTERRUPT execution aborted by user")
        else:
            print("\nINTERRUPT execution aborted by user")
        sys.exit(130)
    except ValueError as ve:
        if reporter is not None:
            reporter.error(f"FATAL {ve}")
        else:
            print(f"FATAL {ve}")
        sys.exit(1)
    except RuntimeError as re:
        if reporter is not None:
            reporter.error(f"FATAL {re}")
        else:
            print(f"FATAL {re}")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
