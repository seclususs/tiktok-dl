import types
from typing import Any, Final, Self

from rich._spinners import SPINNERS
from rich.console import Console
from rich.prompt import Confirm
from rich.status import Status
from rich.text import Text

from ttdl.events import Reporter

DEFAULT_TERMUX_PREFIX: Final[str] = "/data/data/com.termux/files/home"
SPINNERS["ttdl_dots"] = {
    "interval": 80,
    "frames": [f"[{f}]" for f in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"],
}


class CliReporter(Reporter):
    def __init__(
        self,
        mode: str = "mass",
        console: Console | None = None,
    ) -> None:
        self.console: Console = (
            console if console is not None else Console(highlight=False, soft_wrap=True)
        )
        self.mode = mode
        self._status: Status | None = None

    def _print_msg(self, msg: str, color: str, prefix_symbol: str) -> None:
        if DEFAULT_TERMUX_PREFIX in msg:
            msg = msg.replace(DEFAULT_TERMUX_PREFIX, "~")

        text = Text()
        text.append(prefix_symbol, style=f"bold {color}")
        text.append(f" {msg}", style="bright_white")
        self.console.print(text)

    def info(self, msg: str, *args: Any) -> None:
        formatted = msg % args if args else msg
        self._print_msg(formatted, "cyan", "[*]")

    def warning(self, msg: str, *args: Any) -> None:
        formatted = msg % args if args else msg
        self._print_msg(formatted, "yellow", "[-]")

    def error(self, msg: str, *args: Any) -> None:
        formatted = msg % args if args else msg
        self._print_msg(formatted, "red", "[!]")

    def success(self, msg: str, *args: Any) -> None:
        formatted = msg % args if args else msg
        self._print_msg(formatted, "green", "[+]")

    def skip(self, msg: str, *args: Any) -> None:
        formatted = msg % args if args else msg
        self._print_msg(formatted, "yellow", "[-]")

    def set_status(self, message: str) -> None:
        if DEFAULT_TERMUX_PREFIX in message:
            message = message.replace(DEFAULT_TERMUX_PREFIX, "~")
        text = Text(message, style="bright_white")
        if self._status is None:
            self._status = self.console.status(
                text,
                spinner="ttdl_dots",
                spinner_style="bold cyan",
            )
            self._status.start()
        else:
            self._status.update(text)

    def clear_status(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None

    def on_live_update(self, size: str, speed: str) -> None:
        self.set_status(f"Live: {size} ({speed})")

    def print_summary(
        self, title: str, stats: list[tuple[str, str]], style: str = "cyan"
    ) -> None:
        self.clear_status()
        t = Text()
        t.append("[*]", style=f"bold {style}")
        t.append(f" {title}:", style="bright_white")
        self.console.print(t)

        for k, v in stats:
            val = str(v)
            if DEFAULT_TERMUX_PREFIX in val:
                val = val.replace(DEFAULT_TERMUX_PREFIX, "~")

            line = Text()
            line.append("[*]", style=f"bold {style}")
            line.append(f" {k}: ", style="bright_white")
            line.append(val, style=f"bold {style}")
            self.console.print(line)

    def ask_retry(self) -> bool:
        self.clear_status()
        try:
            prompt = Text()
            prompt.append("[?]", style="bold yellow")
            prompt.append(" Retry failed downloads?", style="bright_white")
            return bool(Confirm.ask(prompt, console=self.console))
        except (EOFError, KeyboardInterrupt):
            return False

    def ask_confirm(self, prompt: str, default: bool = False) -> bool:
        self.clear_status()
        clean = prompt.strip()
        if clean.startswith("[?]"):
            clean = clean[3:].strip()

        p = Text()
        p.append("[?]", style="bold yellow")
        p.append(f" {clean}", style="bright_white")

        try:
            return bool(Confirm.ask(p, console=self.console, default=default))
        except (EOFError, KeyboardInterrupt):
            return False

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        self.clear_status()
