import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final

MAX_LOG_BYTES: Final[int] = 5 * 1024 * 1024
BACKUP_COUNT: Final[int] = 2
LOG_FILE_NAME: Final[str] = "ttdl.log"
LOG_FORMAT: Final[str] = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_DATE_FORMAT: Final[str] = "[%X]"


def setup_logging(logs_dir: Path, verbose: bool = False) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / LOG_FILE_NAME

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT,
        force=True,
        handlers=[
            RotatingFileHandler(
                log_file,
                maxBytes=MAX_LOG_BYTES,
                backupCount=BACKUP_COUNT,
                encoding="utf-8",
            )
        ],
    )
