"""Configuration loading for thermal_capture."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


@dataclass(slots=True)
class CaptureConfig:
    """Runtime configuration for a thermal capture session.

    Attributes:
        thermal_device: V4L2 device path or full GStreamer pipeline.
        backend: Capture backend. Supported values are ``gst`` and ``v4l2``.
        width: Expected thermal image width in pixels.
        height: Expected thermal image height in pixels.
        fps: Expected frame rate.
        output_dir: Output directory. ``None`` means current working directory.
        crop_telemetry_rows: Whether to crop frames taller than ``height``.
        radiometric_tlinear: Whether raw Y16 values are interpreted as Kelvin * 100.
    """

    thermal_device: str = "/dev/video0"
    backend: str = "v4l2"
    width: int = 160
    height: int = 120
    fps: float = 9.0
    output_dir: Path | None = None
    crop_telemetry_rows: bool = True
    radiometric_tlinear: bool = True

    @classmethod
    def from_yaml(cls, path: str | Path | None) -> "CaptureConfig":
        """Load configuration from a YAML file.

        Missing files or ``None`` return defaults.

        Args:
            path: YAML file path.

        Returns:
            Parsed capture configuration.
        """
        if path is None:
            return cls()

        yaml_path = Path(path)
        if not yaml_path.exists():
            return cls()
        if yaml is None:
            raise RuntimeError("PyYAML is required to load YAML configuration files.")

        with yaml_path.open("r", encoding="utf-8") as handle:
            raw: dict[str, Any] = yaml.safe_load(handle) or {}

        output = raw.get("output_dir")
        return cls(
            thermal_device=str(raw.get("thermal_device", cls.thermal_device)),
            backend=str(raw.get("backend", cls.backend)),
            width=int(raw.get("width", cls.width)),
            height=int(raw.get("height", cls.height)),
            fps=float(raw.get("fps", cls.fps)),
            output_dir=Path(output) if output else None,
            crop_telemetry_rows=bool(
                raw.get("crop_telemetry_rows", cls.crop_telemetry_rows)
            ),
            radiometric_tlinear=bool(
                raw.get("radiometric_tlinear", cls.radiometric_tlinear)
            ),
        )

    def with_overrides(
        self,
        *,
        thermal_device: str | None = None,
        backend: str | None = None,
        output_dir: str | Path | None = None,
        duration: float | None = None,  # kept for CLI symmetry; not stored
    ) -> "CaptureConfig":
        """Return a copy with selected runtime overrides."""
        del duration
        return CaptureConfig(
            thermal_device=thermal_device or self.thermal_device,
            backend=backend or self.backend,
            width=self.width,
            height=self.height,
            fps=self.fps,
            output_dir=Path(output_dir) if output_dir else self.output_dir,
            crop_telemetry_rows=self.crop_telemetry_rows,
            radiometric_tlinear=self.radiometric_tlinear,
        )
