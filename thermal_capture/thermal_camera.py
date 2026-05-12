"""Thermal camera access through OpenCV, V4L2 and GStreamer.

This module keeps raw thermal data separate from display images. The expected
thermal stream is PureThermal/FLIR Lepton Y16, i.e. one uint16 value per pixel.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
import warnings

import cv2
import numpy as np


@dataclass(slots=True)
class ThermalFrame:
    """A raw thermal frame and its acquisition timestamp.

    Attributes:
        raw: Raw Y16 matrix as ``uint16`` with shape ``(height, width)``.
        timestamp_s: Unix timestamp in seconds at frame retrieval time.
    """

    raw: np.ndarray
    timestamp_s: float


class ThermalCameraError(RuntimeError):
    """Raised when the thermal camera cannot be opened or read."""


class ThermalCamera:
    """Open and read a PureThermal/FLIR Lepton Y16 stream.

    Supported backends:
        - ``gst``: OpenCV through a GStreamer pipeline using GRAY16_LE.
        - ``v4l2``: OpenCV through V4L2, requesting Y16 and disabling RGB conversion.

    Notes:
        Some OpenCV builds still return a 3-channel image with V4L2 even after
        requesting Y16. That output is rejected intentionally because it is no
        longer guaranteed to be raw thermal data.
    """

    def __init__(
        self,
        device: str,
        *,
        backend: str = "v4l2",
        width: int = 160,
        height: int = 120,
        fps: float = 9.0,
        crop_telemetry_rows: bool = True,
    ) -> None:
        """Initialize the camera descriptor.

        Args:
            device: V4L2 path, by-id path, or full GStreamer pipeline.
            backend: ``gst`` or ``v4l2``.
            width: Expected image width.
            height: Expected image height.
            fps: Expected frame rate.
            crop_telemetry_rows: Crop extra rows such as 160x122 to 160x120.
        """
        self.device = device
        self.backend = backend.lower().strip()
        self.width = width
        self.height = height
        self.fps = fps
        self.crop_telemetry_rows = crop_telemetry_rows
        self.cap: cv2.VideoCapture | None = None
        self._warned_telemetry_crop = False

    def open(self) -> None:
        """Open the camera stream."""
        if self.backend == "gst":
            source = self._gst_source()
            cap = cv2.VideoCapture(source, cv2.CAP_GSTREAMER)
        elif self.backend == "v4l2":
            cap = self._open_v4l2()
        else:
            raise ThermalCameraError(f"Unsupported backend: {self.backend}")

        if not cap.isOpened():
            raise ThermalCameraError(f"Could not open thermal camera: {self.device}")

        self.cap = cap

    def read(self) -> ThermalFrame:
        """Read a raw thermal frame.

        Returns:
            Raw thermal frame and Unix timestamp from immediately after retrieval.
        """
        if self.cap is None:
            raise ThermalCameraError("Thermal camera is not open.")

        ok, frame = self.cap.read()
        timestamp_s = time.time()
        if not ok or frame is None:
            raise ThermalCameraError("Could not read thermal frame.")

        raw = self._coerce_y16(frame)
        return ThermalFrame(raw=raw, timestamp_s=timestamp_s)

    def release(self) -> None:
        """Release the camera handle."""
        if self.cap is not None:
            self.cap.release()
            self.cap = None

    def _open_v4l2(self) -> cv2.VideoCapture:
        """Open the device with V4L2 and request non-converted Y16 frames."""
        cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)

        # Must be set before and after the requested format on some OpenCV builds.
        cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc("Y", "1", "6", " "))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)

        return cap

    def _gst_source(self) -> str:
        """Return a GStreamer pipeline for GRAY16_LE capture."""
        if "v4l2src" in self.device and "appsink" in self.device:
            return self.device

        fps_num = int(round(self.fps))
        return (
            f"v4l2src device={self.device} "
            f"! video/x-raw,format=GRAY16_LE,width={self.width},height={self.height},"
            f"framerate={fps_num}/1 "
            "! appsink drop=true max-buffers=1 sync=false"
        )

    def _coerce_y16(self, frame: np.ndarray) -> np.ndarray:
        """Coerce OpenCV output to a 2-D uint16 Y16 matrix.

        Y16 is the scientific input stream: one 16-bit unsigned sample per
        thermal pixel. BGR/RGB frames are display images produced by conversion
        somewhere below us, so they are rejected instead of being converted back
        to grayscale and treated as measurements.

        Args:
            frame: Frame returned by OpenCV.

        Returns:
            Contiguous ``uint16`` matrix with shape ``(height, width)``.

        Raises:
            ThermalCameraError: If OpenCV returned converted/non-raw data.
        """
        if frame.ndim == 3 and frame.shape[2] == 1:
            frame = frame[:, :, 0]

        elif frame.ndim == 3 and frame.shape[2] == 2 and frame.dtype == np.uint8:
            # Some backends expose 16-bit grayscale as two uint8 bytes per pixel.
            frame = np.ascontiguousarray(frame).view("<u2")[:, :, 0]

        elif frame.ndim == 3:
            raise ThermalCameraError(
                "Expected raw single-channel Y16 frame, but OpenCV returned a "
                f"multi-channel frame with shape {frame.shape} and dtype {frame.dtype}. "
                "This usually means the V4L2 backend converted the stream to BGR/RGB. "
                "Use --backend gst with an OpenCV build that supports GStreamer, or verify "
                "that CAP_PROP_CONVERT_RGB=0 is honored by your OpenCV build."
            )

        if frame.ndim != 2:
            raise ThermalCameraError(
                f"Expected 2-D Y16 frame after coercion; got shape {frame.shape}."
            )

        if frame.dtype == np.int16:
            frame = frame.view(np.uint16)
        elif frame.dtype != np.uint16:
            raise ThermalCameraError(
                f"Expected uint16 Y16 data; got dtype {frame.dtype}."
            )

        if frame.shape[1] != self.width:
            raise ThermalCameraError(
                f"Unexpected frame width: got {frame.shape[1]}, expected {self.width}."
            )

        if frame.shape[0] != self.height:
            if self.crop_telemetry_rows and frame.shape[0] > self.height:
                extra_rows = frame.shape[0] - self.height
                if not self._warned_telemetry_crop:
                    warnings.warn(
                        "Thermal frame has "
                        f"{frame.shape[0]} rows; cropping to {self.height}. "
                        f"The {extra_rows} extra row(s) may contain telemetry "
                        "that is not interpreted by this software.",
                        RuntimeWarning,
                        stacklevel=2,
                    )
                    self._warned_telemetry_crop = True
                frame = frame[: self.height, :]
            else:
                raise ThermalCameraError(
                    f"Unexpected frame height: got {frame.shape[0]}, "
                    f"expected {self.height}."
                )

        return np.ascontiguousarray(frame)

    def __enter__(self) -> "ThermalCamera":
        self.open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # type: ignore[no-untyped-def]
        self.release()
