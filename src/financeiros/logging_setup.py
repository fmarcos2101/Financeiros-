from __future__ import annotations

import logging
from pathlib import Path


def setup_logging(log_dir: str | Path, level: int = logging.INFO) -> logging.Logger:
    """Configura log em arquivo + stdout para o robô."""
    path = Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)
    log_file = path / "financeiros.log"

    logger = logging.getLogger("financeiros")
    logger.setLevel(level)
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(fmt)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger
