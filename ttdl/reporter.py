import logging
import re
import types
from datetime import datetime, timezone
from typing import Any, Self

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TransferSpeedColumn,
)
from rich.prompt import Confirm

from ttdl.events import Reporter

log = logging.getLogger("ttdl.reporter")


class CliReporter(Reporter):
    def __init__(self, mode: str = "mass") -> None:
        self.console = Console()
        self.mode = mode
        if mode == "live":
            self.progress = Progress(
                SpinnerColumn(),
                TextColumn("[bold cyan]live stream"),
                TextColumn("[green]{task.fields[size]}"),
                TextColumn("[yellow]{task.fields[speed]}"),
                TimeElapsedColumn(),
                transient=True,
                console=self.console,
            )
        else:
            self.progress = Progress(
                TextColumn("[bold blue]{task.description}", justify="right"),
                BarColumn(bar_width=None),
                "[progress.percentage]{task.percentage:>3.1f}%",
                "•",
                DownloadColumn(),
                "•",
                TransferSpeedColumn(),
                "•",
                TimeElapsedColumn(),
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
        time_str = datetime.now(timezone.utc).astimezone().strftime("%X")

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

    def on_live_update(self, size: str, speed: str) -> None:
        if self.live_task is None:
            self.live_task = self.progress.add_task(
                "dl", size="0.00MB", speed="0.00KB/s"
            )
        self.progress.update(self.live_task, size=size, speed=speed)

    def ask_retry(self) -> bool:
        return Confirm.ask(
            "Do you want to retry failed downloads?", console=self.console
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
        self.progress.stop()
        self._is_active = False
