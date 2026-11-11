import argparse
import os
import re
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

try:
    from ttdl import __version__
    from ttdl.config import AppConfig, clean_cache, format_size, open_config_editor
    from ttdl.direct import DirectDownloader
    from ttdl.live import LiveDownloader
    from ttdl.models import DateFilter
    from ttdl.reporter import CliReporter
    from ttdl.utils import parse_url_input
except ImportError as e:
    print(f"[!] Missing dependency '{e.name}'")
    print("[*] Run: pip install .")
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
UTILITY_COMMANDS: Final[tuple[str, ...]] = ("config", "clean", "login")

CLI_EPILOG: Final[str] = (
    "examples:\n"
    "  ttdl @username [--video|--photo]\n"
    "  ttdl username [--video|--photo]\n"
    "  ttdl https://tiktok.com/...\n"
    "\n"
    "commands: login, config, clean (use '<cmd> --help')"
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
        "--cache",
        "--cached",
        dest="cached",
        action="store_true",
        help="Skip browser scraping, load from\nJSON cache and download directly",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Force full profile scrape, ignoring\nconsecutive duplicate stop",
    )


def _build_config_parser(
    subparsers: Any,
) -> None:
    p_config = subparsers.add_parser(
        "config",
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
) -> None:
    p_clean = subparsers.add_parser(
        "clean",
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
) -> None:
    subparsers.add_parser(
        "login",
        help="Log in to TikTok and save cookies",
        description="Opens browser to authenticate and export\nsession cookies.",
        formatter_class=_create_formatter,
    )


def _build_top_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ttdl",
        description="TikTok Downloader - Videos, Photos, and Live Streams",
        epilog=CLI_EPILOG,
        formatter_class=_create_formatter,
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--video",
        action="store_true",
        help="Download videos only from profile",
    )
    parser.add_argument(
        "--photo",
        action="store_true",
        help="Download photos only from profile",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Capture ongoing live stream from target profile",
    )
    parser.add_argument(
        "--cookies",
        type=str,
        default=None,
        help="Path to cookies file (Netscape fmt / cookies.txt)",
    )
    _add_profile_options(parser)
    parser.add_argument(
        "targets",
        nargs="*",
        metavar="TARGET",
        help="Target @username (or username), URL(s), or file path",
    )
    return parser


def _build_subcommand_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ttdl",
        description="TikTok Downloader - Videos, Photos, and Live Streams",
        epilog=CLI_EPILOG,
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

    _build_config_parser(subparsers)
    _build_clean_parser(subparsers)
    _build_login_parser(subparsers)

    return parser


USERNAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^@?[a-zA-Z0-9_.]+$")


def _is_username_target(target: str) -> bool:
    t = target.strip().strip("'\"")
    return bool(t and t != "@" and USERNAME_PATTERN.match(t))


def _is_url_or_file_target(target: str) -> bool:
    t = target.strip().strip("'\"")
    if not t:
        return False

    lower = t.lower()
    if lower.startswith(("http://", "https://", "www.")) or "://" in lower:
        return True

    if os.path.isfile(t):
        return True

    if lower.endswith(".txt"):
        return True

    return False


def _detect_subcommand(args_list: Sequence[str]) -> str | None:
    for arg in args_list:
        if not arg.startswith("-"):
            if arg in UTILITY_COMMANDS:
                return arg
            return None

    return None


def _ensure_playwright_installed(reporter: CliReporter) -> None:
    try:
        import playwright  # noqa: F401
    except ImportError as e:
        reporter.error("Missing dependency '%s'", e.name)
        reporter.info("Run: pip install .")
        sys.exit(EXIT_FAILURE)


def _handle_config(
    args: argparse.Namespace, config: AppConfig, reporter: CliReporter
) -> int:
    action = args.action

    if args.edit or action == "edit":
        reporter.info("Opening config: %s", config.cfg_path)
        open_config_editor(config.cfg_path)
        return EXIT_SUCCESS

    if args.path or action == "path":
        print(str(config.cfg_path))
        return EXIT_SUCCESS

    if args.reset or action == "reset":
        config.reset_config()
        reporter.success("Reset configuration to default")
        return EXIT_SUCCESS

    if action == "set":
        if not args.key or args.value is None:
            reporter.error("Usage: ttdl config set <key> <value>")
            return EXIT_FAILURE

        if config.set_setting(args.key, args.value):
            reporter.success("Set %s = %s", args.key, args.value)
            return EXIT_SUCCESS

        reporter.error("Invalid key or value: %s", args.key)
        return EXIT_FAILURE

    if action == "get":
        if not args.key:
            reporter.error("Usage: ttdl config get <key>")
            return EXIT_FAILURE

        val = config.get_setting(args.key)
        if val is not None:
            print(val)
            return EXIT_SUCCESS

        reporter.error("Unknown key: %s", args.key)
        return EXIT_FAILURE

    if config.cfg_path.is_file():
        print(f"# Active config: {config.cfg_path}\n")
        print(config.cfg_path.read_text(encoding="utf-8"))
    else:
        reporter.warning("Config file not found")

    return EXIT_SUCCESS


def _handle_clean(
    args: argparse.Namespace, config: AppConfig, reporter: CliReporter
) -> int:
    mode_str = "all" if args.all else ("JSON" if args.json else "temp")

    if not getattr(args, "yes", False):
        prompt = f"Purge {mode_str} cache, logs, and temp files?"
        if not reporter.ask_confirm(prompt, default=False):
            reporter.warning("Purge cancelled")
            return EXIT_SUCCESS

    files_removed, bytes_freed = clean_cache(
        config, clean_all=args.all, clean_json=args.json
    )
    reporter.success(
        "Cleaned %d files (%s freed)",
        files_removed,
        format_size(bytes_freed),
    )
    return EXIT_SUCCESS


def _handle_url(
    args: argparse.Namespace, config: AppConfig, reporter: CliReporter
) -> int:
    url_target: str | Sequence[str] = getattr(
        args, "target", getattr(args, "targets", [])
    )
    urls = parse_url_input(url_target)
    if not urls:
        reporter.error("No valid URLs provided")
        return EXIT_FAILURE

    return DirectDownloader(config, reporter, urls).execute()


def _handle_login(config: AppConfig, reporter: CliReporter) -> int:
    from ttdl.auth import AuthManager

    return AuthManager(config, reporter).login()


def _handle_live(
    args: argparse.Namespace, config: AppConfig, reporter: CliReporter
) -> int:
    username: str = getattr(args, "user", getattr(args, "username", ""))
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

    username: str = getattr(args, "user", "")
    date_filter = DateFilter.build(getattr(args, "date", None))

    if getattr(args, "video", False):
        mode = "video"
    elif getattr(args, "photo", False):
        mode = "photo"
    else:
        mode = "all"

    jsononly: bool = getattr(args, "scrape", False)
    usejson: bool = getattr(args, "cached", False) or getattr(args, "cache", False)
    force: bool = getattr(args, "force", False)

    config.validate_dependencies(require_ffmpeg=(mode != "photo"))
    return TikTokDownloader(
        target_username=username,
        config=config,
        reporter=reporter,
        date_filter=date_filter,
        mode=mode,
        jsononly=jsononly,
        usejson=usejson,
        force=force,
    ).execute()


def run_cli(argv: Sequence[str] | None = None) -> int:
    reporter = CliReporter(mode="mass")
    try:
        args_list = list(sys.argv[1:] if argv is None else argv)
        if not args_list:
            top_parser = _build_top_parser()
            top_parser.print_help()
            sys.exit(EXIT_SUCCESS)

        workspace_dir = Path.cwd()
        config = AppConfig(workspace_dir)

        subcmd = _detect_subcommand(args_list)

        if subcmd is not None:
            parser = _build_subcommand_parser()
            args = parser.parse_args(args_list)

            cmd: str = args.command

            if cmd == "config":
                return _handle_config(args, config, reporter)

            if cmd == "clean":
                return _handle_clean(args, config, reporter)

            if cmd == "login":
                if TERMUX_PREFIX_KEY in os.environ.get("PREFIX", ""):
                    from ttdl.termux import setup_termux

                    setup_termux(reporter)
                _ensure_playwright_installed(reporter)
                return _handle_login(config, reporter)

            return EXIT_SUCCESS

        parser = _build_top_parser()
        args = parser.parse_args(args_list)
        targets: list[str] = getattr(args, "targets", [])

        if not targets:
            reporter.error("No target specified")
            reporter.info("Run 'ttdl --help' for usage details")
            return EXIT_FAILURE

        first_target = targets[0]

        if first_target == "url":
            if len(targets) > 1:
                setattr(args, "target", targets[1:])
                return _handle_url(args, config, reporter)

            reporter.error("Direct download requires URL or file (e.g. ttdl <url>)")
            reporter.info("Run 'ttdl --help' for usage details")
            return EXIT_FAILURE

        if first_target in ("batch", "video", "photo", "live"):
            if len(targets) > 1:
                if first_target == "video":
                    setattr(args, "video", True)
                elif first_target == "photo":
                    setattr(args, "photo", True)
                elif first_target == "live":
                    setattr(args, "live", True)

                first_target = targets[1]
            else:
                reporter.error("No target username specified for '%s'", first_target)
                reporter.info("Run 'ttdl --help' for usage details")
                return EXIT_FAILURE

        if _is_url_or_file_target(first_target):
            setattr(args, "target", targets)
            return _handle_url(args, config, reporter)

        if _is_username_target(first_target):
            username = first_target.lstrip("@").strip()
            if not username:
                reporter.error("Target username empty")
                return EXIT_FAILURE

            setattr(args, "user", username)

            if getattr(args, "live", False):
                reporter.mode = "live"
                return _handle_live(args, config, reporter)

            cached = getattr(args, "cached", False) or getattr(args, "cache", False)
            if not cached and TERMUX_PREFIX_KEY in os.environ.get("PREFIX", ""):
                from ttdl.termux import setup_termux

                setup_termux(reporter)

            if not cached:
                _ensure_playwright_installed(reporter)

            return _handle_profile_download(args, config, reporter)

        reporter.error("Target '%s' is unrecognized", first_target)
        reporter.info("Target must be username (e.g. @user or user) or URL")
        reporter.info("Direct download requires URL or file (e.g. ttdl <url>)")
        reporter.info("Run 'ttdl --help' for usage details")
        return EXIT_FAILURE

    except KeyboardInterrupt:
        reporter.clear_status()
        reporter.warning("Aborted by user")
        sys.exit(EXIT_INTERRUPT)
    except (ValueError, RuntimeError) as err:
        reporter.error("Error: %s", err)
        sys.exit(EXIT_FAILURE)


def main() -> None:
    try:
        sys.exit(run_cli())
    except KeyboardInterrupt:
        sys.exit(EXIT_INTERRUPT)
