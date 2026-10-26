import argparse
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Final

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
    print(f"\n[!] Missing dependency '{e.name}'.")
    print("[*] Please install requirements first by running:")
    print("pip install .\n")
    sys.exit(1)

DEFAULT_TERM_WIDTH: Final[int] = 70
DEFAULT_TERM_HEIGHT: Final[int] = 20
MIN_HELP_WIDTH: Final[int] = 30
MAX_HELP_WIDTH: Final[int] = 80
MAX_HELP_POS: Final[int] = 22

EXIT_SUCCESS: Final[int] = 0
EXIT_FAILURE: Final[int] = 1
EXIT_INTERRUPT: Final[int] = 130

TERMUX_PREFIX_KEY: Final[str] = "com.termux"
CONFIG_ACTIONS: Final[list[str]] = ["show", "path", "edit", "set", "get", "reset"]
PROFILE_COMMANDS: Final[tuple[str, ...]] = ("batch", "video", "photo")
BROWSER_COMMANDS: Final[tuple[str, ...]] = ("batch", "video", "photo", "login")

CLI_EPILOG: Final[str] = (
    "profile options (for batch, video, photo):\n"
    "  --date DATE     Filter posts by date or range\n"
    "                  (e.g. 2023, 2023-10, 2022:2023)\n"
    "  --scrape        Scrape & cache only, no media\n"
    "  --cached        Download from JSON cache directly\n"
    "  --force         Force full profile scrape\n"
    "\n"
    "Run 'ttdl <command> --help' for command details."
)


def _create_formatter(prog: str) -> argparse.HelpFormatter:
    term_width = max(
        MIN_HELP_WIDTH,
        min(
            shutil.get_terminal_size((DEFAULT_TERM_WIDTH, DEFAULT_TERM_HEIGHT)).columns,
            MAX_HELP_WIDTH,
        ),
    )
    return argparse.RawTextHelpFormatter(
        prog,
        max_help_position=MAX_HELP_POS,
        width=term_width,
    )


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


def _build_batch_parser(
    subparsers: Any,
    base_parser: argparse.ArgumentParser,
) -> None:
    p_batch = subparsers.add_parser(
        "batch",
        parents=[base_parser],
        help="Download all media from target profile",
        description="Download all posts (videos, photos, stories)\nfrom target username.",
        formatter_class=_create_formatter,
    )
    p_batch.add_argument("user", type=str, help="Target TikTok username")
    _add_profile_options(p_batch)


def _build_video_parser(
    subparsers: Any,
    base_parser: argparse.ArgumentParser,
) -> None:
    p_video = subparsers.add_parser(
        "video",
        parents=[base_parser],
        help="Download videos only from profile",
        description="Download videos only from target username.",
        formatter_class=_create_formatter,
    )
    p_video.add_argument("user", type=str, help="Target TikTok username")
    _add_profile_options(p_video)


def _build_photo_parser(
    subparsers: Any,
    base_parser: argparse.ArgumentParser,
) -> None:
    p_photo = subparsers.add_parser(
        "photo",
        parents=[base_parser],
        help="Download photos only from profile",
        description="Download photos only from target username.",
        formatter_class=_create_formatter,
    )
    p_photo.add_argument("user", type=str, help="Target TikTok username")
    _add_profile_options(p_photo)


def _build_live_parser(
    subparsers: Any,
    base_parser: argparse.ArgumentParser,
) -> None:
    p_live = subparsers.add_parser(
        "live",
        parents=[base_parser],
        help="Record live stream from profile",
        description="Capture ongoing live stream from target username.",
        formatter_class=_create_formatter,
    )
    p_live.add_argument("user", type=str, help="Target TikTok username")
    p_live.add_argument(
        "--cookies",
        type=str,
        default=None,
        help="Path to cookies file\n(Netscape format / cookies.txt)",
    )


def _build_url_parser(
    subparsers: Any,
    base_parser: argparse.ArgumentParser,
) -> None:
    p_url = subparsers.add_parser(
        "url",
        parents=[base_parser],
        help="Download from single URL or list file",
        description="Download directly from a single URL or\na file containing URLs.",
        formatter_class=_create_formatter,
    )
    p_url.add_argument(
        "target",
        type=str,
        metavar="URL_OR_FILE",
        help="TikTok video/photo URL or path to\ntext file listing URLs",
    )


def _build_config_parser(
    subparsers: Any,
    base_parser: argparse.ArgumentParser,
) -> None:
    p_config = subparsers.add_parser(
        "config",
        parents=[base_parser],
        help="View, edit, or set configuration values",
        description="Manage ttdl settings in config file.",
        formatter_class=_create_formatter,
    )
    p_config.add_argument(
        "action",
        nargs="?",
        choices=CONFIG_ACTIONS,
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


def _build_clean_parser(
    subparsers: Any,
    base_parser: argparse.ArgumentParser,
) -> None:
    p_clean = subparsers.add_parser(
        "clean",
        parents=[base_parser],
        help="Purge cache, logs, and temp files",
        description="Clean temporary media chunks, browser sessions,\nlogs, and JSON cache.",
        formatter_class=_create_formatter,
    )
    p_clean.add_argument(
        "--all",
        action="store_true",
        help="Purge everything including profile\nJSON cache and configs",
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


def _build_login_parser(
    subparsers: Any,
    base_parser: argparse.ArgumentParser,
) -> None:
    subparsers.add_parser(
        "login",
        parents=[base_parser],
        help="Log in to TikTok and save cookies",
        description="Opens browser to authenticate and export\nsession cookies.",
        formatter_class=_create_formatter,
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

    parser = argparse.ArgumentParser(
        prog="ttdl",
        description="TikTok Downloader - Videos, Photos, and Live Streams",
        epilog=CLI_EPILOG,
        parents=[base_parser],
        formatter_class=_create_formatter,
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

    _build_batch_parser(subparsers, base_parser)
    _build_video_parser(subparsers, base_parser)
    _build_photo_parser(subparsers, base_parser)
    _build_live_parser(subparsers, base_parser)
    _build_url_parser(subparsers, base_parser)
    _build_config_parser(subparsers, base_parser)
    _build_clean_parser(subparsers, base_parser)
    _build_login_parser(subparsers, base_parser)

    return parser


def _ensure_playwright_installed() -> None:
    try:
        import playwright  # noqa: F401
    except ImportError as e:
        print(f"\n[!] Missing dependency '{e.name}'.")
        print("[*] Please install requirements first by running:")
        print("pip install .\n")
        sys.exit(EXIT_FAILURE)


def _handle_config(
    args: argparse.Namespace, config: AppConfig, reporter: CliReporter
) -> int:
    action = args.action

    if args.edit or action == "edit":
        reporter.info("OPEN_CONFIG %s", config.cfg_path)
        open_config_editor(config.cfg_path)
        return EXIT_SUCCESS

    if args.path or action == "path":
        print(str(config.cfg_path))
        return EXIT_SUCCESS

    if args.reset or action == "reset":
        config.reset_config()
        reporter.info("CONFIG_RESET %s", config.cfg_path)
        return EXIT_SUCCESS

    if action == "set":
        if not args.key or args.value is None:
            reporter.error("USAGE: ttdl config set <key> <value>")
            return EXIT_FAILURE

        if config.set_setting(args.key, args.value):
            reporter.info("CONFIG_SET %s = %s", args.key, args.value)
            return EXIT_SUCCESS

        reporter.error("UNKNOWN_KEY or INVALID_VALUE: %s", args.key)
        return EXIT_FAILURE

    if action == "get":
        if not args.key:
            reporter.error("USAGE: ttdl config get <key>")
            return EXIT_FAILURE

        val = config.get_setting(args.key)
        if val is not None:
            print(val)
            return EXIT_SUCCESS

        reporter.error("UNKNOWN_KEY: %s", args.key)
        return EXIT_FAILURE

    if config.cfg_path.is_file():
        print(f"# Active config: {config.cfg_path}\n")
        print(config.cfg_path.read_text(encoding="utf-8"))
    else:
        reporter.warning("Config file not found at %s", config.cfg_path)

    return EXIT_SUCCESS


def _handle_clean(
    args: argparse.Namespace, config: AppConfig, reporter: CliReporter
) -> int:
    mode_str = "ALL" if args.all else ("JSON" if args.json else "TEMP")

    if not getattr(args, "yes", False):
        prompt = f"Confirm purging {mode_str.lower()} cache, logs, and temporary files?"
        if not reporter.ask_confirm(prompt, default=False):
            reporter.info("CLEAN_CANCEL Purge cancelled by user")
            return EXIT_SUCCESS

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
    return EXIT_SUCCESS


def _handle_url(
    args: argparse.Namespace, config: AppConfig, reporter: CliReporter
) -> int:
    url_target: str = args.target
    urls = parse_url_input(url_target)
    return DirectDownloader(config, reporter, urls).execute()


def _handle_login(config: AppConfig, reporter: CliReporter) -> int:
    from ttdl.auth import AuthManager

    return AuthManager(config, reporter).login()


def _handle_live(
    args: argparse.Namespace, config: AppConfig, reporter: CliReporter
) -> int:
    username: str = args.user
    cookies_arg = getattr(args, "cookies", None)
    reporter.mode = "live"
    config.validate_dependencies(require_ffmpeg=True)
    return LiveDownloader(
        username, config, reporter, cookies_path=cookies_arg
    ).execute()


def _handle_profile_download(
    args: argparse.Namespace, config: AppConfig, reporter: CliReporter
) -> int:
    from ttdl.mass import TikTokDownloader

    username = args.user
    date_filter = DateFilter.build(args.date)
    cmd = args.command
    mode = "all" if cmd == "batch" else cmd

    config.validate_dependencies(require_ffmpeg=(mode != "photo"))
    return TikTokDownloader(
        target_username=username,
        config=config,
        reporter=reporter,
        date_filter=date_filter,
        mode=mode,
        jsononly=args.scrape,
        usejson=args.cached,
        force=args.force,
    ).execute()


def main() -> None:
    reporter: CliReporter | None = None
    try:
        workspace_dir = Path.cwd()
        config = AppConfig(workspace_dir)

        parser = _build_parser()
        args = parser.parse_args()

        verbose: bool = getattr(args, "verbose", False)
        cmd: str = args.command
        reporter = CliReporter(
            mode="live" if cmd == "live" else "mass", verbose=verbose
        )
        if cmd != "clean":
            setup_logging(config.logs_dir, verbose=verbose)

        cached: bool = getattr(args, "cached", False)
        needs_browser = (cmd in BROWSER_COMMANDS) and not cached

        if needs_browser and TERMUX_PREFIX_KEY in os.environ.get("PREFIX", ""):
            from ttdl.termux import setup_termux

            setup_termux(reporter)

        if cmd in BROWSER_COMMANDS:
            _ensure_playwright_installed()

        exit_code = EXIT_SUCCESS

        if cmd == "config":
            exit_code = _handle_config(args, config, reporter)

        elif cmd == "clean":
            exit_code = _handle_clean(args, config, reporter)

        elif cmd == "url":
            exit_code = _handle_url(args, config, reporter)

        elif cmd == "login":
            exit_code = _handle_login(config, reporter)

        elif cmd == "live":
            exit_code = _handle_live(args, config, reporter)

        elif cmd in PROFILE_COMMANDS:
            exit_code = _handle_profile_download(args, config, reporter)

        sys.exit(exit_code)

    except KeyboardInterrupt:
        if reporter is not None:
            reporter.clear_status()
            reporter.warning("INTERRUPT execution aborted by user")
        else:
            print("\n[-] INTERRUPT execution aborted by user")

        sys.exit(EXIT_INTERRUPT)
    except (ValueError, RuntimeError) as err:
        if reporter is not None:
            reporter.error("FATAL %s", err)
        else:
            print(f"[!] FATAL {err}")

        sys.exit(EXIT_FAILURE)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(EXIT_INTERRUPT)
