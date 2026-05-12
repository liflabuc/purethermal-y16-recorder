"""Utility functions for timestamping and filenames."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import time


def unix_time_s() -> float:
    """Return current Unix time in seconds as a float64-compatible value."""
    return time.time()


def unix_time_ms(ts_s: float | None = None) -> int:
    """Return Unix time in milliseconds.

    Args:
        ts_s: Optional Unix timestamp in seconds.

    Returns:
        Integer Unix timestamp in milliseconds.
    """
    if ts_s is None:
        ts_s = unix_time_s()
    return int(round(ts_s * 1000.0))


def session_stamp() -> str:
    """Return a filesystem-safe timestamp for output filenames."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def ensure_output_dir(path: str | Path | None) -> Path:
    """Create and return the output directory.

    Args:
        path: Desired output directory. ``None`` means current working directory.

    Returns:
        Existing output path.
    """
    output = Path.cwd() if path is None else Path(path)
    output.mkdir(parents=True, exist_ok=True)
    return output
