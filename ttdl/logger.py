import logging

from rich.console import Console
from rich.logging import RichHandler

console = Console()


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(
                console=console,
                rich_tracebacks=True,
                show_path=False,
                show_level=True,
                markup=True,
            )
        ],
    )
