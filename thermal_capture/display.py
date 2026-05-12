"""Display utilities for thermal images."""

from __future__ import annotations

import cv2
import numpy as np


def raw_to_celsius(raw: np.ndarray) -> np.ndarray:
    """Convert T-linear raw values to Celsius.

    This conversion is only valid when the camera/firmware outputs radiometric
    T-linear values encoded as Kelvin * 100.

    Args:
        raw: Raw Y16 frame.

    Returns:
        Temperature matrix in Celsius.
    """
    return raw.astype(np.float32) / 100.0 - 273.15


def normalize_for_display(raw: np.ndarray) -> np.ndarray:
    """Normalize a raw frame to 8-bit grayscale for display only.

    Args:
        raw: Raw Y16 frame.

    Returns:
        8-bit image for visualization. This must not be saved as raw data.
    """
    values = raw.astype(np.float32)
    lo, hi = np.percentile(values, [1, 99])
    if hi <= lo:
        hi = float(values.max())
        lo = float(values.min())
    if hi <= lo:
        return np.zeros(raw.shape, dtype=np.uint8)
    img = (values - lo) * (255.0 / (hi - lo))
    return np.clip(img, 0, 255).astype(np.uint8)


def make_preview_image(
    raw: np.ndarray,
    *,
    timestamp_ms: int | None = None,
    radiometric_tlinear: bool = True,
    scale: int = 4,
) -> np.ndarray:
    """Build a BGR preview image with optional timestamp overlay.

    Args:
        raw: Raw Y16 frame.
        timestamp_ms: Optional Unix timestamp in milliseconds.
        radiometric_tlinear: Whether to display center temperature estimate.
        scale: Nearest-neighbor display scale.

    Returns:
        BGR image ready for ``cv2.imshow``.
    """
    gray = normalize_for_display(raw)
    bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    h, w = bgr.shape[:2]
    bgr = cv2.resize(bgr, (w * scale, h * scale), interpolation=cv2.INTER_NEAREST)

    lines: list[str] = []
    if timestamp_ms is not None:
        lines.append(f"t = {timestamp_ms} ms")
    if radiometric_tlinear:
        center = raw[raw.shape[0] // 2 - 5 : raw.shape[0] // 2 + 5,
                     raw.shape[1] // 2 - 5 : raw.shape[1] // 2 + 5]
        temp_c = float(raw_to_celsius(center).mean())
        lines.append(f"center = {temp_c:.2f} C")

    y = 24
    for line in lines:
        cv2.putText(bgr, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(bgr, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (255, 255, 255), 1, cv2.LINE_AA)
        y += 22
    return bgr


def make_playback_image(
    raw: np.ndarray,
    *,
    frame_index: int,
    timestamp_ms: int,
    scale: int = 4,
) -> np.ndarray:
    """Build a BGR playback image from raw Y16 data.

    Args:
        raw: Raw Y16 frame.
        frame_index: Frame index stored in the raw gzip file.
        timestamp_ms: Unix timestamp in milliseconds stored in the frame record.
        scale: Nearest-neighbor display scale.

    Returns:
        BGR image ready for ``cv2.imshow``.
    """
    gray = normalize_for_display(raw)
    bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    h, w = bgr.shape[:2]
    bgr = cv2.resize(bgr, (w * scale, h * scale), interpolation=cv2.INTER_NEAREST)

    celsius = raw_to_celsius(raw)
    lines = [
        f"frame = {frame_index}",
        f"t = {timestamp_ms} ms",
        (
            f"C min/mean/max = {float(celsius.min()):.2f} / "
            f"{float(celsius.mean()):.2f} / {float(celsius.max()):.2f}"
        ),
    ]

    y = 24
    for line in lines:
        cv2.putText(
            bgr,
            line,
            (10, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 0, 0),
            3,
            cv2.LINE_AA,
        )
        cv2.putText(
            bgr,
            line,
            (10, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        y += 22
    return bgr
