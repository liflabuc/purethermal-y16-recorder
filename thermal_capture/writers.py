"""Writers and readers for thermal recordings."""

from __future__ import annotations

import csv
import gzip
import json
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import numpy as np

from .utils import unix_time_ms


RAW_GZIP_MAGIC = b"PTY16V1\n"
RAW_GZIP_VERSION = 1
RAW_FRAME_PREFIX = struct.Struct("<Qdq")


@dataclass(slots=True)
class RawGzipFrame:
    """One raw frame record read from a raw Y16 gzip v1 file.

    Attributes:
        frame_index: Frame index stored in the file.
        timestamp_s: Unix timestamp in seconds.
        timestamp_ms: Unix timestamp in milliseconds.
        raw_y16: Raw Y16 frame with shape ``(height, width)``.
    """

    frame_index: int
    timestamp_s: float
    timestamp_ms: int
    raw_y16: np.ndarray


def raw_gzip_header(
    *,
    width: int,
    height: int,
    fps: float,
) -> dict[str, object]:
    """Build the official raw Y16 gzip v1 metadata header.

    Args:
        width: Frame width in pixels.
        height: Frame height in pixels.
        fps: Nominal acquisition FPS.

    Returns:
        JSON-serializable metadata dictionary.
    """
    return {
        "format": "PureThermal Y16 Raw Gzip",
        "version": RAW_GZIP_VERSION,
        "width": width,
        "height": height,
        "fps": fps,
        "dtype": "uint16",
        "endianness": "little",
        "raw_unit": "kelvin_x100_if_radiometric_tlinear",
        "celsius_formula": "raw / 100.0 - 273.15",
        "timestamp_s": "unix_seconds_float64",
        "timestamp_ms": "unix_milliseconds_int64",
        "frame_record": {
            "frame_index": "uint64",
            "timestamp_s": "float64",
            "timestamp_ms": "int64",
            "raw_y16": "uint16[height,width]",
        },
    }


class ThermalRawGzipWriter:
    """Write official raw Y16 gzip v1 recordings.

    File layout:
        ``b"PTY16V1\n"``, then a uint32 little-endian JSON header length,
        then UTF-8 JSON metadata. Each frame record is a uint64 frame index,
        float64 timestamp in Unix seconds, int64 timestamp in Unix milliseconds,
        and the raw ``uint16[height,width]`` Y16 matrix in little-endian order.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        width: int,
        height: int,
        fps: float,
        flush_every: int = 1,
    ) -> None:
        """Open a raw Y16 gzip writer and write the file header.

        Args:
            path: Output ``.raw.y16.gz`` path.
            width: Frame width in pixels.
            height: Frame height in pixels.
            fps: Nominal acquisition FPS.
            flush_every: Flush interval in frames. ``1`` flushes every frame.
        """
        self.path = Path(path)
        self.width = width
        self.height = height
        self.fps = fps
        self.flush_every = max(1, int(flush_every))
        self.handle = gzip.open(self.path, "wb")
        self.closed = False

        header_json = json.dumps(
            raw_gzip_header(width=width, height=height, fps=fps),
            separators=(",", ":"),
        ).encode("utf-8")
        self.handle.write(RAW_GZIP_MAGIC)
        self.handle.write(struct.pack("<I", len(header_json)))
        self.handle.write(header_json)

    def write_frame(
        self,
        *,
        frame_index: int,
        raw: np.ndarray,
        timestamp_s: float,
    ) -> None:
        """Write one raw Y16 frame.

        Args:
            frame_index: Zero-based frame index.
            raw: Raw Y16 matrix as ``uint16`` with shape ``(height, width)``.
            timestamp_s: Unix timestamp in seconds.
        """
        if self.closed:
            raise RuntimeError("Raw gzip writer is already closed.")
        if raw.ndim != 2:
            raise ValueError(f"Expected 2-D raw Y16 matrix, got shape {raw.shape}.")
        if raw.shape != (self.height, self.width):
            raise ValueError(
                f"Expected raw Y16 shape {(self.height, self.width)}, got {raw.shape}."
            )
        if raw.dtype != np.uint16:
            raise TypeError(f"Expected uint16 raw Y16 frame, got {raw.dtype}.")

        timestamp_ms = unix_time_ms(timestamp_s)
        self.handle.write(struct.pack("<Qdq", frame_index, timestamp_s, timestamp_ms))
        self.handle.write(np.ascontiguousarray(raw).astype("<u2", copy=False).tobytes())
        if (frame_index + 1) % self.flush_every == 0:
            self.handle.flush()

    def close(self) -> None:
        """Close the gzip file."""
        if not self.closed:
            self.handle.flush()
            self.handle.close()
            self.closed = True

    def __enter__(self) -> "ThermalRawGzipWriter":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # type: ignore[no-untyped-def]
        self.close()


def read_raw_gzip_v1(path: str | Path) -> dict[str, object]:
    """Read and validate the raw Y16 gzip v1 header without reading frames.

    Args:
        path: Input ``.raw.y16.gz`` file.

    Returns:
        Parsed metadata header.

    Raises:
        ValueError: If the file magic, header length or JSON metadata is invalid.
    """
    with gzip.open(path, "rb") as handle:
        return _read_raw_gzip_header(handle)


def _read_raw_gzip_header(handle) -> dict[str, object]:  # type: ignore[no-untyped-def]
    """Read and validate a raw Y16 gzip v1 header from an open binary handle."""
    magic = handle.read(len(RAW_GZIP_MAGIC))
    if magic != RAW_GZIP_MAGIC:
        raise ValueError("Not a PureThermal Y16 raw gzip v1 file.")

    header_len_raw = handle.read(4)
    if len(header_len_raw) != 4:
        raise ValueError("Truncated raw gzip header length.")
    header_len = struct.unpack("<I", header_len_raw)[0]

    header_raw = handle.read(header_len)
    if len(header_raw) != header_len:
        raise ValueError("Truncated raw gzip JSON header.")

    header = json.loads(header_raw.decode("utf-8"))
    if header.get("version") != RAW_GZIP_VERSION:
        raise ValueError(f"Unsupported raw gzip version: {header.get('version')}")
    return header


def iter_raw_gzip_frames(path: str | Path) -> Iterator[RawGzipFrame]:
    """Iterate over raw Y16 gzip v1 frames without loading the full file.

    Args:
        path: Input ``.raw.y16.gz`` file.

    Yields:
        Raw frame records in file order.

    Raises:
        ValueError: If the file is malformed or a frame is truncated.
    """
    with gzip.open(path, "rb") as handle:
        header = _read_raw_gzip_header(handle)
        width = int(header["width"])
        height = int(header["height"])
        frame_bytes_len = width * height * np.dtype("<u2").itemsize

        while True:
            prefix = handle.read(RAW_FRAME_PREFIX.size)
            if not prefix:
                break
            if len(prefix) != RAW_FRAME_PREFIX.size:
                raise ValueError("Truncated raw gzip frame prefix.")

            frame_index, timestamp_s, timestamp_ms = RAW_FRAME_PREFIX.unpack(prefix)
            raw_bytes = handle.read(frame_bytes_len)
            if len(raw_bytes) != frame_bytes_len:
                raise ValueError(
                    f"Truncated raw Y16 frame at frame_index {frame_index}."
                )

            raw_y16 = np.frombuffer(raw_bytes, dtype="<u2").reshape((height, width))
            yield RawGzipFrame(
                frame_index=int(frame_index),
                timestamp_s=float(timestamp_s),
                timestamp_ms=int(timestamp_ms),
                raw_y16=np.ascontiguousarray(raw_y16),
            )


class ThermalCsvWriter:
    """Write radiometric thermal frames as Celsius matrices in CSV.

    The CSV contains one row per frame. The scientific data are Celsius values
    computed from raw Y16 T-linear samples; normalized preview images are never
    written because they are display-only contrast mappings.
    """

    HEADER = [
        "frame_index",
        "timestamp_s",
        "timestamp_ms",
        "width",
        "height",
        "unit",
        "frame_celsius",
    ]

    def __init__(self, path: str | Path, *, flush_every: int = 1) -> None:
        """Open a CSV writer.

        Args:
            path: Output CSV path.
            flush_every: Flush interval in frames. ``1`` flushes every frame.
        """
        self.path = Path(path)
        self.flush_every = max(1, int(flush_every))
        self.handle: TextIO = self.path.open("w", newline="", encoding="utf-8")
        self.writer = csv.writer(self.handle)
        self.writer.writerow(self.HEADER)
        self.closed = False

    def write_frame(
        self,
        *,
        frame_index: int,
        celsius: np.ndarray,
        timestamp_s: float,
        timestamp_ms: int | None = None,
    ) -> None:
        """Write one Celsius frame.

        Args:
            frame_index: Zero-based frame index.
            celsius: 2-D Celsius matrix with shape ``(height, width)``.
            timestamp_s: Unix timestamp in seconds.
            timestamp_ms: Optional Unix timestamp in milliseconds.
        """
        if self.closed:
            raise RuntimeError("CSV writer is already closed.")
        if celsius.ndim != 2:
            raise ValueError(f"Expected 2-D Celsius matrix, got shape {celsius.shape}.")

        height, width = celsius.shape
        frame_json = json.dumps(celsius.tolist(), separators=(",", ":"))
        self.writer.writerow(
            [
                frame_index,
                f"{timestamp_s:.6f}",
                unix_time_ms(timestamp_s) if timestamp_ms is None else timestamp_ms,
                width,
                height,
                "celsius",
                frame_json,
            ]
        )
        if (frame_index + 1) % self.flush_every == 0:
            self.handle.flush()

    def close(self) -> None:
        """Close the CSV file."""
        if not self.closed:
            self.handle.flush()
            self.handle.close()
            self.closed = True

    def __enter__(self) -> "ThermalCsvWriter":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # type: ignore[no-untyped-def]
        self.close()


def raw_to_celsius(raw: np.ndarray) -> np.ndarray:
    """Convert T-linear raw Y16 values to Celsius."""
    return raw.astype(np.float32) / 100.0 - 273.15


def default_csv_export_path(input_path: str | Path) -> Path:
    """Return the default CSV export path for a raw Y16 gzip file."""
    path = Path(input_path)
    suffix = ".raw.y16.gz"
    if path.name.endswith(suffix):
        return path.with_name(path.name[: -len(suffix)] + ".celsius.csv")
    return path.with_suffix(path.suffix + ".celsius.csv")


def export_raw_to_csv(
    input_path: str | Path,
    output_path: str | Path | None = None,
    *,
    progress_every: int = 100,
) -> Path:
    """Export raw Y16 gzip v1 frames to derived Celsius CSV.

    Args:
        input_path: Input ``.raw.y16.gz`` file.
        output_path: Optional output CSV path. Defaults beside the input file.
        progress_every: Print progress every N frames. ``0`` disables progress.

    Returns:
        Path to the generated CSV file.
    """
    csv_path = (
        default_csv_export_path(input_path)
        if output_path is None
        else Path(output_path)
    )
    frame_count = 0

    with ThermalCsvWriter(csv_path) as writer:
        for frame in iter_raw_gzip_frames(input_path):
            writer.write_frame(
                frame_index=frame.frame_index,
                celsius=raw_to_celsius(frame.raw_y16),
                timestamp_s=frame.timestamp_s,
                timestamp_ms=frame.timestamp_ms,
            )
            frame_count += 1
            if progress_every > 0 and frame_count % progress_every == 0:
                print(f"Exported {frame_count} frames...")

    print(f"Exported {frame_count} frames to: {csv_path}")
    return csv_path
