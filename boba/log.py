"""One logger for the pipeline. Entry points call setup(); libraries just get_logger()."""

from __future__ import annotations

import logging
import os

_CONFIGURED = False


def setup(level: str | int | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    lvl = level or os.environ.get("BOBA_LOG_LEVEL", "INFO")
    logging.basicConfig(
        level=lvl,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    _CONFIGURED = True


def get_logger(name: str = "boba") -> logging.Logger:
    return logging.getLogger(name)
