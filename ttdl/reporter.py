import logging
import re
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Self

from rich import filesize
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    Task,
    TextColumn,
    TimeElapsedColumn,
)
from rich.prompt import Confirm
from rich.table import Column, Table
from rich.text import Text

from ttdl.events import Reporter

log = logging.getLogger("ttdl.reporter")

DEFAULT_TERMUX_PREFIX: Final[str] = "/data/data/com.termux/files/home"
NARROW_THRESHOLD_WIDTH: Final[int] = 80
NARROW_DESC_WIDTH: Final[int] = 16
NARROW_PCT_WIDTH: Final[int] = 5
NARROW_DETAIL_WIDTH: Final[int] = 22
WIDE_DESC_WIDTH: Final[int] = 26
WIDE_PCT_WIDTH: Final[int] = 6
WIDE_DETAIL_WIDTH: Final[int] = 36
MIN_STATUS_WIDTH: Final[int] = 52
MIN_STAT_VAL_WIDTH: Final[int] = 34
STATUS_MARGIN: Final[int] = 6
STAT_MARGIN: Final[int] = 12

SYMBOL_MAP: Final[dict[str, str]] = {
    "info": "[bold cyan][*][/bold cyan]",
    "debug": "[dim white][*][/dim white]",
    "success": "[bold green][+][/bold green]",
    "warning": "[bold yellow][-][/bold yellow]",
    "skip": "[bold yellow][-][/bold yellow]",
    "error": "[bold red][!][/bold red]",
    "prompt": "[bold yellow][?][/bold yellow]",
}
TAG_EXTRACTOR_REGEX: Final[re.Pattern[str]] = re.compile(r"^([A-Z_]+)(.*)")


def _resolve_elapsed_str(task: Task) -> str:
    elapsed = task.finished_time if task.finished else task.elapsed
    if elapsed is None:
        return "--:--:--"

    return str(timedelta(seconds=max(0, int(elapsed))))


def _render_counter_task(task: Task, elapsed_str: str, is_narrow: bool) -> Text:
    unit = str(task.fields.get("unit", "posts"))
    total = int(task.total) if task.total is not None else "?"

    if is_narrow:
        return Text.from_markup(f"[cyan]{int(task.completed)}/{total} {unit}[/cyan]")

    return Text.from_markup(
        f"[dim]•[/dim] [cyan]{int(task.completed)}/{total} {unit}[/cyan] "
        f"[dim]•[/dim] [dim white]{elapsed_str}[/dim white]"
    )


def _render_ffmpeg_task(elapsed_str: str, is_narrow: bool) -> Text:
    if is_narrow:
        return Text.from_markup(f"[dim white]{elapsed_str}[/dim white]")

    return Text.from_markup(f"[dim]•[/dim] [dim white]{elapsed_str}[/dim white]")


def _format_narrow_val(v: float, unit_factor: int) -> str:
    val = v / unit_factor
    if unit_factor == 1:
        return f"{int(val)}"

    return f"{val:.0f}" if val >= 100 else f"{val:.1f}"


def _render_narrow_download_task(task: Task) -> Text:
    completed = int(task.completed)
    base = int(task.total) if task.total is not None else completed
    u, s = filesize.pick_unit_and_suffix(base, ["B", "K", "M", "G"], 1000)

    comp_str = _format_narrow_val(completed, u)
    tot_str = (
        f"{_format_narrow_val(task.total, u)}{s}" if task.total is not None else f"?{s}"
    )

    speed = task.finished_speed or task.speed
    if speed is not None and speed > 0:
        su, ss = filesize.pick_unit_and_suffix(int(speed), ["B", "K", "M", "G"], 1000)
        sp_str = f"{_format_narrow_val(speed, su)}{ss}/s"
    else:
        sp_str = "--/s"

    return Text.from_markup(
        f"[cyan]{comp_str}/{tot_str}[/cyan] [dim]•[/dim] [yellow]{sp_str}[/yellow]"
    )


def _render_wide_download_task(task: Task, elapsed_str: str) -> Text:
    completed = int(task.completed)
    base = int(task.total) if task.total is not None else completed
    u, s = filesize.pick_unit_and_suffix(base, ["bytes", "kB", "MB", "GB"], 1000)
    p = 0 if u == 1 else 1

    comp_str = f"{completed / u:,.{p}f}"
    tot_str = (
        f"{int(task.total) / u:,.{p}f} {s}" if task.total is not None else f"? {s}"
    )

    speed = task.finished_speed or task.speed
    if speed is not None and speed > 0:
        sp_str = f"{filesize.decimal(int(speed))}/s"
    else:
        sp_str = "-- B/s"

    return Text.from_markup(
        f"[dim]•[/dim] [cyan]{comp_str}/{tot_str}[/cyan] "
        f"[dim]•[/dim] [yellow]{sp_str:>8}[/yellow] "
        f"[dim]•[/dim] [dim white]{elapsed_str}[/dim white]"
    )


def _render_download_task(task: Task, elapsed_str: str, is_narrow: bool) -> Text:
    if is_narrow:
        return _render_narrow_download_task(task)

    return _render_wide_download_task(task, elapsed_str)


class TaskDetailColumn(ProgressColumn):
    def __init__(
        self, table_column: Column | None = None, is_narrow: bool = False
    ) -> None:
        super().__init__(table_column=table_column)
        self.is_narrow = is_narrow

    def render(self, task: Task) -> Text:
        elapsed_str = _resolve_elapsed_str(task)

        unit = task.fields.get("unit")
        if unit in ("posts", "items"):
            return _render_counter_task(task, elapsed_str, self.is_narrow)

        if task.fields.get("is_ffmpeg") or any(
            tag in task.description for tag in ("META", "METADATA", "ENC", "ENCODE")
        ):
            return _render_ffmpeg_task(elapsed_str, self.is_narrow)

        return _render_download_task(task, elapsed_str, self.is_narrow)


def _shorten_path(val: str, max_len: int) -> str:
    if val.startswith(DEFAULT_TERMUX_PREFIX):
        val = "~" + val[len(DEFAULT_TERMUX_PREFIX) :]
    else:
        try:
            home = str(Path.home())
            if home != "/" and val.startswith(home):
                val = "~" + val[len(home) :]
        except Exception:
            pass

    if len(val) > max_len:
        half = (max_len - 3) // 2
        val = f"{val[:half]}...{val[-half:]}"

    return val


def _format_log_entry(
    level: str,
    msg: str,
    level_color: str,
    verbose: bool,
    is_narrow: bool,
) -> str:
    if is_narrow and DEFAULT_TERMUX_PREFIX in msg:
        msg = msg.replace(DEFAULT_TERMUX_PREFIX, "~")

    match = TAG_EXTRACTOR_REGEX.match(msg)
    tag = match.group(1) if match else ""
    rest = match.group(2) if match else msg

    effective_level = level.lower()
    effective_color = level_color
    if tag and ("SKIP" in tag or "SKIPPED" in tag):
        effective_level = "skip"
        effective_color = "yellow"

    prefix_tag = SYMBOL_MAP.get(
        effective_level, f"[{effective_color}][*][/{effective_color}]"
    )

    if match:
        msg = f"[bold {effective_color}]{tag}[/bold {effective_color}]{rest}"

    prefix = ""
    if verbose:
        time_str = datetime.now(UTC).astimezone().strftime("%X")
        prefix = f"[dim]{time_str}[/dim] "

    return f"{prefix}{prefix_tag} {msg}"


def _build_progress(mode: str, console: Console, is_narrow: bool) -> Progress:
    use_dots = not getattr(console, "legacy_windows", False) and (
        getattr(console, "encoding", "").lower() in ("utf-8", "utf8")
    )
    spinner = "dots" if use_dots else "line"
    if mode == "live":
        return Progress(
            SpinnerColumn(spinner_name=spinner),
            TextColumn("[bold cyan]live stream"),
            TextColumn("[green]{task.fields[size]}"),
            TextColumn("[yellow]{task.fields[speed]}"),
            TimeElapsedColumn(),
            transient=True,
            console=console,
        )

    if is_narrow:
        col_desc = Column(width=NARROW_DESC_WIDTH, no_wrap=True, overflow="ellipsis")
        col_pct = Column(width=NARROW_PCT_WIDTH, justify="right", no_wrap=True)
        col_detail = Column(width=NARROW_DETAIL_WIDTH, no_wrap=True)
        return Progress(
            TextColumn("{task.description}", table_column=col_desc),
            BarColumn(bar_width=None),
            TextColumn(
                "[progress.percentage]{task.percentage:>3.0f}%",
                table_column=col_pct,
            ),
            TaskDetailColumn(table_column=col_detail, is_narrow=True),
            console=console,
            transient=True,
        )

    col_desc = Column(width=WIDE_DESC_WIDTH, no_wrap=True)
    col_pct = Column(width=WIDE_PCT_WIDTH, justify="right", no_wrap=True)
    col_detail = Column(width=WIDE_DETAIL_WIDTH, no_wrap=True)
    return Progress(
        TextColumn("{task.description}", table_column=col_desc),
        BarColumn(bar_width=None),
        TextColumn(
            "[progress.percentage]{task.percentage:>5.1f}%",
            table_column=col_pct,
        ),
        TaskDetailColumn(table_column=col_detail, is_narrow=False),
        console=console,
        transient=True,
    )


class CliReporter(Reporter):
    def __init__(
        self,
        mode: str = "mass",
        verbose: bool = False,
        console: Console | None = None,
    ) -> None:
        self.console = console if console is not None else Console()
        self.mode = mode
        self.verbose = verbose
        self._status: Any = None
        self.is_narrow = self.console.width < NARROW_THRESHOLD_WIDTH
        self.progress = _build_progress(mode, self.console, self.is_narrow)
        self.live_task: Any = None
        self._is_active = False

    def add_task(self, description: str, total: float | None = 0, **fields: Any) -> Any:
        return self.progress.add_task(description, total=total, **fields)

    def update_task(self, task_id: Any, **fields: Any) -> None:
        self.progress.update(task_id, **fields)

    def remove_task(self, task_id: Any) -> None:
        self.progress.remove_task(task_id)

    def _print(self, level: str, msg: str, level_color: str) -> None:
        formatted = _format_log_entry(
            level, msg, level_color, self.verbose, self.is_narrow
        )

        if self._is_active:
            self.progress.console.print(formatted)
        else:
            self.console.print(formatted)

    def debug(self, msg: str, *args: Any) -> None:
        msg = msg % args if args else msg
        if self.verbose:
            self._print("debug", msg, "dim white")
        log.debug(msg)

    def info(self, msg: str, *args: Any) -> None:
        msg = msg % args if args else msg
        self._print("info", msg, "cyan")
        log.info(msg)

    def warning(self, msg: str, *args: Any) -> None:
        msg = msg % args if args else msg
        self._print("warning", msg, "yellow")
        log.warning(msg)

    def error(self, msg: str, *args: Any) -> None:
        msg = msg % args if args else msg
        self._print("error", msg, "red")
        log.error(msg)

    def success(self, msg: str, *args: Any) -> None:
        msg = msg % args if args else msg
        self._print("success", msg, "green")
        log.info(msg)

    def skip(self, msg: str, *args: Any) -> None:
        msg = msg % args if args else msg
        self._print("skip", msg, "yellow")
        log.info(msg)

    def set_status(self, message: str) -> None:
        if self._is_active:
            return

        max_len = max(MIN_STATUS_WIDTH, self.console.width - STATUS_MARGIN)
        if self.is_narrow and len(message) > max_len:
            message = message[: max_len - 3] + "..."

        if self._status is None:
            use_dots = not getattr(self.console, "legacy_windows", False) and (
                getattr(self.console, "encoding", "").lower() in ("utf-8", "utf8")
            )
            spinner = "dots" if use_dots else "line"
            self._status = self.console.status(
                f"[cyan]{message}[/cyan]", spinner=spinner
            )
            self._status.start()
        else:
            self._status.update(f"[cyan]{message}[/cyan]")

    def clear_status(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None

    def _format_stat_val(self, val: str) -> str:
        if not self.is_narrow:
            return val

        max_len = max(MIN_STAT_VAL_WIDTH, self.console.width - STAT_MARGIN)
        return _shorten_path(val, max_len)

    def print_summary(
        self, title: str, stats: list[tuple[str, str]], style: str = "cyan"
    ) -> None:
        self.clear_status()
        table = Table.grid(padding=(0, 2), expand=True)
        table.add_column(style="bold white", justify="right")
        table.add_column(style="cyan", overflow="fold")
        for k, v in stats:
            table.add_row(f"{k}:", self._format_stat_val(v))
        panel = Panel(
            table,
            title=f"[bold {style}]{title}[/bold {style}]",
            border_style=style,
            expand=True,
        )
        if self._is_active:
            self.progress.console.print(panel)
        else:
            self.console.print(panel)

    def on_live_update(self, size: str, speed: str) -> None:
        if self.live_task is None:
            self.live_task = self.progress.add_task(
                "dl", size="0.00MB", speed="0.00KB/s"
            )
        self.progress.update(self.live_task, size=size, speed=speed)

    def ask_retry(self) -> bool:
        prompt_text = (
            "[bold yellow][?][/bold yellow] Do you want to retry failed downloads?"
        )
        return bool(Confirm.ask(prompt_text, console=self.console))

    def ask_confirm(self, prompt: str, default: bool = False) -> bool:
        target_console = self.progress.console if self._is_active else self.console
        clean_prompt = prompt.strip()
        if not clean_prompt.startswith("[?]"):
            clean_prompt = f"[bold yellow][?][/bold yellow] {clean_prompt}"

        return bool(Confirm.ask(clean_prompt, console=target_console, default=default))

    def __enter__(self) -> Self:
        self.progress.start()
        self._is_active = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        self.clear_status()
        self.progress.stop()
        self._is_active = False
