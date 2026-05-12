#!/usr/bin/env python3
"""Command-line interface for PureThermal/FLIR Lepton capture."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from glob import glob
from pathlib import Path
import platform
import sys

import cv2

from thermal_capture.config import CaptureConfig
from thermal_capture.display import (
    make_playback_image,
    make_preview_image,
    raw_to_celsius,
)
from thermal_capture.thermal_camera import ThermalCamera, ThermalCameraError
from thermal_capture.utils import (
    ensure_output_dir,
    session_stamp,
    unix_time_ms,
    unix_time_s,
)
from thermal_capture.writers import (
    ThermalCsvWriter,
    ThermalRawGzipWriter,
    default_csv_export_path,
    export_raw_to_csv,
    iter_raw_gzip_frames,
    read_raw_gzip_v1,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="thermal_capture",
        description="Capture Y16 frames from a PureThermal/FLIR Lepton camera.",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Optional YAML config path. Missing file uses defaults.",
    )
    parser.add_argument(
        "--thermal-device",
        default=None,
        help="Thermal device path or GStreamer pipeline. Default: config or /dev/video0.",
    )
    parser.add_argument(
        "--backend",
        choices=["gst", "v4l2"],
        default=None,
        help="Capture backend. Default: config or v4l2.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    preview = subparsers.add_parser(
        "preview",
        help="Display thermal preview without recording.",
    )
    preview.add_argument(
        "--no-temperature",
        action="store_true",
        help="Do not display the T-linear Celsius estimate.",
    )

    record = subparsers.add_parser("record", help="Record raw Y16 frames to gzip.")
    record.add_argument(
        "--output",
        default=None,
        help="Output directory. Default: current working directory.",
    )
    record.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Recording duration in seconds. Default: until Ctrl+C or q in preview.",
    )
    record.add_argument(
        "--save-csv",
        action="store_true",
        help="Also save derived Celsius matrices as CSV.",
    )
    record.add_argument(
        "--no-temperature",
        action="store_true",
        help="Do not display the T-linear Celsius estimate in preview.",
    )

    inspect = subparsers.add_parser(
        "inspect",
        help="Inspect the metadata header of a raw Y16 gzip v1 file.",
    )
    inspect.add_argument("filenames", nargs="+", help="Path(s) to .raw.y16.gz file(s).")

    export_csv = subparsers.add_parser(
        "export-csv",
        help="Export raw Y16 gzip v1 file(s) to derived Celsius CSV.",
    )
    export_csv.add_argument(
        "filenames",
        nargs="+",
        help="Path(s) to .raw.y16.gz file(s).",
    )
    export_csv.add_argument(
        "--output",
        default=None,
        help=(
            "Output CSV path for one input, or output directory for multiple "
            "inputs. Default: write beside each raw file."
        ),
    )

    play = subparsers.add_parser(
        "play",
        help="Play a raw Y16 gzip v1 capture as a normalized thermal preview.",
    )
    play.add_argument("filename", help="Path to a .raw.y16.gz file.")
    play.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Playback speed multiplier. Default: 1.0.",
    )
    play.add_argument(
        "--pause-ms",
        type=int,
        default=None,
        help="Override inter-frame delay in milliseconds. Default: header FPS.",
    )

    subparsers.add_parser(
        "diagnose",
        help="Print Python/OpenCV/backend diagnostics for this host.",
    )
    return parser


def load_runtime_config(args: argparse.Namespace) -> CaptureConfig:
    """Load config and apply CLI overrides."""
    config = CaptureConfig.from_yaml(args.config)
    return config.with_overrides(
        thermal_device=args.thermal_device,
        backend=args.backend,
        output_dir=getattr(args, "output", None),
    )


def run_capture(args: argparse.Namespace, *, record: bool) -> int:
    """Run the shared preview/record acquisition loop.

    Args:
        args: Parsed CLI arguments.
        record: Whether to write raw Y16 gzip frames.

    Returns:
        Process exit code.
    """
    config = load_runtime_config(args)
    save_csv = bool(getattr(args, "save_csv", False))
    if record and save_csv and not config.radiometric_tlinear:
        raise RuntimeError(
            "CSV Celsius output requires radiometric/T-linear Y16 data. "
            "Enable radiometric_tlinear only after verifying that raw values are "
            "Kelvin * 100 for this camera stream."
        )

    camera = ThermalCamera(
        config.thermal_device,
        backend=config.backend,
        width=config.width,
        height=config.height,
        fps=config.fps,
        crop_telemetry_rows=config.crop_telemetry_rows,
    )

    frame_count = 0
    start_s = unix_time_s()
    raw_path = None
    csv_path = None
    window_name = "Thermal recording" if record else "Thermal preview"

    try:
        with ExitStack() as stack:
            stack.enter_context(camera)
            raw_writer = None
            csv_writer = None
            if record:
                output_dir = ensure_output_dir(config.output_dir)
                stamp = session_stamp()
                raw_path = output_dir / f"thermal_{stamp}.raw.y16.gz"
                raw_writer = stack.enter_context(
                    ThermalRawGzipWriter(
                        raw_path,
                        width=config.width,
                        height=config.height,
                        fps=config.fps,
                    )
                )
                print(f"Recording raw Y16 data to: {raw_path}")
                if save_csv:
                    csv_path = output_dir / f"thermal_{stamp}.celsius.csv"
                    csv_writer = stack.enter_context(ThermalCsvWriter(csv_path))
                    print(f"Recording derived CSV data to: {csv_path}")
            print("Press q in the preview window or Ctrl+C to stop.")

            while True:
                now_s = unix_time_s()
                if (
                    getattr(args, "duration", None) is not None
                    and now_s - start_s >= args.duration
                ):
                    break

                frame = camera.read()

                if raw_writer is not None:
                    raw_writer.write_frame(
                        frame_index=frame_count,
                        raw=frame.raw,
                        timestamp_s=frame.timestamp_s,
                    )

                image = make_preview_image(
                    frame.raw,
                    timestamp_ms=unix_time_ms(frame.timestamp_s),
                    radiometric_tlinear=not args.no_temperature and config.radiometric_tlinear,
                )
                cv2.imshow(window_name, image)

                if csv_writer is not None:
                    celsius = raw_to_celsius(frame.raw)
                    csv_writer.write_frame(
                        frame_index=frame_count,
                        celsius=celsius,
                        timestamp_s=frame.timestamp_s,
                    )
                frame_count += 1

                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    except KeyboardInterrupt:
        print("\nCapture stopped by user.")
    finally:
        cv2.destroyAllWindows()

    if record:
        print(f"Frames written: {frame_count}")
    else:
        print(f"Frames displayed: {frame_count}")
    return 0


def run_preview(args: argparse.Namespace) -> int:
    """Run thermal preview mode."""
    return run_capture(args, record=False)


def run_record(args: argparse.Namespace) -> int:
    """Run thermal record mode with live preview."""
    return run_capture(args, record=True)


def run_inspect(args: argparse.Namespace) -> int:
    """Inspect one or more raw Y16 gzip v1 file headers."""
    for index, filename in enumerate(args.filenames):
        if index:
            print()
        header = read_raw_gzip_v1(filename)
        print(f"Raw Y16 gzip v1 metadata: {filename}")
        for key, value in header.items():
            print(f"{key}: {value}")
    return 0


def run_export_csv(args: argparse.Namespace) -> int:
    """Export one or more raw Y16 gzip v1 files to Celsius CSV."""
    input_paths = [Path(filename) for filename in args.filenames]
    output = Path(args.output) if args.output is not None else None

    if len(input_paths) == 1:
        export_raw_to_csv(input_paths[0], output)
        return 0

    if output is not None:
        if output.exists() and not output.is_dir():
            raise RuntimeError(
                "When exporting multiple raw files, --output must be a directory."
            )
        output.mkdir(parents=True, exist_ok=True)

    for input_path in input_paths:
        output_path = None
        if output is not None:
            output_path = output / default_csv_export_path(input_path).name
        print(f"Exporting {input_path}...")
        export_raw_to_csv(input_path, output_path)
    return 0


def playback_delay_ms(
    header: dict[str, object],
    *,
    speed: float,
    pause_ms: int | None,
) -> int:
    """Return playback delay in milliseconds from CLI options and file metadata."""
    if pause_ms is not None:
        return max(0, pause_ms)
    if speed <= 0:
        raise ValueError("--speed must be greater than zero.")
    fps = float(header.get("fps", 9.0) or 9.0)
    if fps <= 0:
        fps = 9.0
    return max(1, int(round(1000.0 / (fps * speed))))


def run_play(args: argparse.Namespace) -> int:
    """Play a raw Y16 gzip v1 capture as a normalized OpenCV preview."""
    header = read_raw_gzip_v1(args.filename)
    delay_ms = playback_delay_ms(
        header,
        speed=float(args.speed),
        pause_ms=args.pause_ms,
    )
    window_name = "Thermal raw playback"
    frames = 0

    try:
        for frame in iter_raw_gzip_frames(args.filename):
            image = make_playback_image(
                frame.raw_y16,
                frame_index=frame.frame_index,
                timestamp_ms=frame.timestamp_ms,
            )
            cv2.imshow(window_name, image)
            frames += 1
            if cv2.waitKey(delay_ms) & 0xFF == ord("q"):
                break
    except KeyboardInterrupt:
        print("\nPlayback stopped by user.")
    finally:
        cv2.destroyAllWindows()

    print(f"Frames displayed: {frames}")
    return 0


def opencv_has_gstreamer() -> bool:
    """Return whether the imported OpenCV build reports GStreamer support."""
    build_info = cv2.getBuildInformation()
    for line in build_info.splitlines():
        if "GStreamer" in line:
            return "YES" in line.upper()
    return False


def run_diagnose(args: argparse.Namespace) -> int:
    """Print host diagnostics relevant to PureThermal capture."""
    config = load_runtime_config(args)
    devices = sorted(glob("/dev/video*"))
    print(f"Python: {platform.python_version()}")
    print(f"OpenCV: {cv2.__version__}")
    print(f"OpenCV GStreamer: {'YES' if opencv_has_gstreamer() else 'NO'}")
    print(f"Video devices: {', '.join(devices) if devices else 'none found'}")
    print(f"Default backend: {config.backend}")
    print(f"Default thermal device: {config.thermal_device}")
    print("Recommended device check: v4l2-ctl --list-devices")
    print("Recommended format check: v4l2-ctl --device=/dev/video0 --list-formats-ext")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "preview":
            return run_preview(args)
        if args.command == "record":
            return run_record(args)
        if args.command == "inspect":
            return run_inspect(args)
        if args.command == "export-csv":
            return run_export_csv(args)
        if args.command == "play":
            return run_play(args)
        if args.command == "diagnose":
            return run_diagnose(args)
    except ThermalCameraError as exc:
        print(f"Thermal camera error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"Data error: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"Runtime error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
