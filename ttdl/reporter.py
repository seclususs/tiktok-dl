import logging
import re
import types
from datetime import UTC, datetime, timedelta
from typing import Any, Self

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


class TaskDetailColumn(ProgressColumn):
    def __init__(
        self, table_column: Column | None = None, is_narrow: bool = False
    ) -> None:
        super().__init__(table_column=table_column)
        self.is_narrow = is_narrow

    def render(self, task: Task) -> Text:
        elapsed = task.finished_time if task.finished else task.elapsed
        elapsed_str = (
            str(timedelta(seconds=max(0, int(elapsed))))
            if elapsed is not None
            else "--:--:--"
        )

        unit = task.fields.get("unit")
        if unit in ("posts", "items"):
            total = int(task.total) if task.total is not None else "?"
            if self.is_narrow:
                return Text.from_markup(
                    f"[cyan]{int(task.completed)}/{total} {unit}[/cyan]"
                )
            return Text.from_markup(
                f"[dim]•[/dim] [cyan]{int(task.completed)}/{total} {unit}[/cyan] "
                f"[dim]•[/dim] [dim white]{elapsed_str}[/dim white]"
            )

        elif task.fields.get("is_ffmpeg") or any(
            tag in task.description for tag in ("META", "METADATA", "ENC", "ENCODE")
        ):
            if self.is_narrow:
                return Text.from_markup(f"[dim white]{elapsed_str}[/dim white]")
            return Text.from_markup(
                f"[dim]•[/dim] [dim white]{elapsed_str}[/dim white]"
            )

        else:
            completed = int(task.completed)
            base = int(task.total) if task.total is not None else completed

            if self.is_narrow:
                u, s = filesize.pick_unit_and_suffix(base, ["B", "K", "M", "G"], 1000)

                def _fmt_val(v: float, unit_factor: int) -> str:
                    val = v / unit_factor
                    if unit_factor == 1:
                        return f"{int(val)}"
                    return f"{val:.0f}" if val >= 100 else f"{val:.1f}"

                comp_str = _fmt_val(completed, u)
                tot_str = (
                    f"{_fmt_val(task.total, u)}{s}"
                    if task.total is not None
                    else f"?{s}"
                )

                speed = task.finished_speed or task.speed
                if speed is not None and speed > 0:
                    su, ss = filesize.pick_unit_and_suffix(
                        int(speed), ["B", "K", "M", "G"], 1000
                    )
                    sp_str = f"{_fmt_val(speed, su)}{ss}/s"
                else:
                    sp_str = "--/s"

                return Text.from_markup(
                    f"[cyan]{comp_str}/{tot_str}[/cyan] [dim]•[/dim] [yellow]{sp_str}[/yellow]"
                )

            u, s = filesize.pick_unit_and_suffix(
                base, ["bytes", "kB", "MB", "GB"], 1000
            )
            p = 0 if u == 1 else 1
            comp_str = f"{completed / u:,.{p}f}"
            tot_str = (
                f"{int(task.total) / u:,.{p}f} {s}"
                if task.total is not None
                else f"? {s}"
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
        self.is_narrow = self.console.width < 80
        use_dots = not getattr(self.console, "legacy_windows", False) and (
            getattr(self.console, "encoding", "").lower() in ("utf-8", "utf8")
        )
        spinner = "dots" if use_dots else "line"
        if mode == "live":
            self.progress = Progress(
                SpinnerColumn(spinner_name=spinner),
                TextColumn("[bold cyan]live stream"),
                TextColumn("[green]{task.fields[size]}"),
                TextColumn("[yellow]{task.fields[speed]}"),
                TimeElapsedColumn(),
                transient=True,
                console=self.console,
            )
        elif self.is_narrow:
            col_desc = Column(width=12, no_wrap=True, overflow="ellipsis")
            col_pct = Column(width=5, justify="right", no_wrap=True)
            col_detail = Column(width=19, no_wrap=True)
            self.progress = Progress(
                TextColumn("{task.description}", table_column=col_desc),
                BarColumn(bar_width=None),
                TextColumn(
                    "[progress.percentage]{task.percentage:>3.0f}%",
                    table_column=col_pct,
                ),
                TaskDetailColumn(table_column=col_detail, is_narrow=True),
                console=self.console,
                transient=True,
            )
        else:
            col_desc = Column(width=26, no_wrap=True)
            col_pct = Column(width=6, justify="right", no_wrap=True)
            col_detail = Column(width=36, no_wrap=True)
            self.progress = Progress(
                TextColumn("{task.description}", table_column=col_desc),
                BarColumn(bar_width=None),
                TextColumn(
                    "[progress.percentage]{task.percentage:>5.1f}%",
                    table_column=col_pct,
                ),
                TaskDetailColumn(table_column=col_detail, is_narrow=False),
                console=self.console,
                transient=True,
            )
        self.live_task: Any = None
        self._is_active = False

    def add_task(self, description: str, total: float | None = 0, **fields: Any) -> Any:
        return self.progress.add_task(description, total=total, **fields)

    def update_task(self, task_id: Any, **fields: Any) -> None:
        self.progress.update(task_id, **fields)

    def remove_task(self, task_id: Any) -> None:
        self.progress.remove_task(task_id)

    def _print(self, level: str, msg: str, level_color: str) -> None:
        time_str = datetime.now(UTC).astimezone().strftime("%X")

        if self.is_narrow and "/data/data/com.termux/files/home" in msg:
            msg = msg.replace("/data/data/com.termux/files/home", "~")

        match = re.match(r"^([A-Z_]+)(.*)", msg)
        if match:
            tag, rest = match.groups()
            msg = f"[bold {level_color}]{tag}[/bold {level_color}]{rest}"

        formatted = (
            f"[dim]{time_str}[/dim] [{level_color}]{level:<7}[/{level_color}] {msg}"
        )
        if self._is_active:
            self.progress.console.print(formatted)
        else:
            self.console.print(formatted)

    def debug(self, msg: str, *args: Any) -> None:
        msg = msg % args if args else msg
        if self.verbose:
            self._print("DEBUG", msg, "dim white")
        log.debug(msg)

    def info(self, msg: str, *args: Any) -> None:
        msg = msg % args if args else msg
        self._print("INFO", msg, "cyan")
        log.info(msg)

    def warning(self, msg: str, *args: Any) -> None:
        msg = msg % args if args else msg
        self._print("WARNING", msg, "yellow")
        log.warning(msg)

    def error(self, msg: str, *args: Any) -> None:
        msg = msg % args if args else msg
        self._print("ERROR", msg, "red")
        log.error(msg)

    def success(self, msg: str, *args: Any) -> None:
        msg = msg % args if args else msg
        self._print("SUCCESS", msg, "green")
        log.info(msg)

    def set_status(self, message: str) -> None:
        if self._is_active:
            return
        if self.is_narrow and len(message) > 35:
            message = message[:32] + "..."
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
        termux_home = "/data/data/com.termux/files/home"
        if val.startswith(termux_home):
            val = "~" + val[len(termux_home) :]
        else:
            try:
                from pathlib import Path

                home = str(Path.home())
                if home != "/" and val.startswith(home):
                    val = "~" + val[len(home) :]
            except Exception:
                pass

        max_len = max(20, self.console.width - 24)
        if len(val) > max_len:
            half = (max_len - 3) // 2
            val = f"{val[:half]}...{val[-half:]}"
        return val

    def print_summary(
        self, title: str, stats: list[tuple[str, str]], style: str = "cyan"
    ) -> None:
        self.clear_status()
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold white", justify="right")
        table.add_column(style="cyan")
        for k, v in stats:
            table.add_row(f"{k}:", self._format_stat_val(v))
        panel = Panel(
            table,
            title=f"[bold {style}]{title}[/bold {style}]",
            border_style=style,
            expand=False,
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
        return bool(
            Confirm.ask("Do you want to retry failed downloads?", console=self.console)
        )

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
