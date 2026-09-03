#!/usr/bin/env python3
"""Minimal Airspy IQ receiver for macOS.

This module wraps the ``airspy_rx`` program supplied by Homebrew's ``airspy``
package, avoiding a dependency on Python SoapySDR bindings.

Setup:
    brew install airspy
    python3 -m pip install numpy
    airspy_info

Example:
    from airspy import Airspy

    radio = Airspy()
    radio.set_center_frequency(100.1e6)
    radio.set_sample_rate(3e6)
    samples = radio.read_samples(262_144)
    print(samples[:10])

The Airspy settings are applied when ``read_samples`` starts the finite
capture. Frequencies are specified in Hz and samples are returned as
``numpy.complex64`` IQ values.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np


class AirspyError(RuntimeError):
    """Raised when an Airspy device cannot be configured or read."""


class Airspy:
    """Small wrapper for finite Airspy IQ captures."""

    MIN_FREQUENCY_HZ = 24e6
    MAX_FREQUENCY_HZ = 1.9e9

    def __init__(
        self,
        center_frequency_hz: float = 100.1e6,
        sample_rate_hz: float = 3e6,
        serial: str | None = None,
        executable: str = "airspy_rx",
    ):
        resolved_executable = shutil.which(executable)
        if resolved_executable is None:
            raise AirspyError(
                f"{executable!r} was not found. On macOS, install it with: brew install airspy"
            )

        self.executable = resolved_executable
        self.serial = serial
        self.center_frequency_hz = 0.0
        self.sample_rate_hz = 0
        self.set_center_frequency(center_frequency_hz)
        self.set_sample_rate(sample_rate_hz)

    def set_center_frequency(self, frequency_hz: float) -> None:
        """Set the centre frequency, in Hz, for the next capture."""
        frequency_hz = float(frequency_hz)
        if not self.MIN_FREQUENCY_HZ <= frequency_hz <= self.MAX_FREQUENCY_HZ:
            raise ValueError(
                f"Airspy centre frequency must be between "
                f"{self.MIN_FREQUENCY_HZ:g} and {self.MAX_FREQUENCY_HZ:g} Hz"
            )
        self.center_frequency_hz = frequency_hz

    def set_sample_rate(self, sample_rate_hz: float) -> None:
        """Set the device sample rate, in samples per second, for the next capture."""
        sample_rate_hz = int(float(sample_rate_hz))
        if sample_rate_hz <= 0:
            raise ValueError("Airspy sample rate must be greater than zero")
        self.sample_rate_hz = sample_rate_hz

    def read_samples(self, sample_count: int, timeout: float | None = None) -> np.ndarray:
        """Capture and return a finite block of complex IQ samples."""
        sample_count = int(sample_count)
        if sample_count <= 0:
            raise ValueError("sample_count must be greater than zero")

        if timeout is None:
            capture_seconds = sample_count / self.sample_rate_hz
            timeout = max(10.0, capture_seconds * 5.0 + 5.0)

        with tempfile.TemporaryDirectory(prefix="airspy-capture-") as temp_dir:
            capture_path = Path(temp_dir) / "samples.f32"
            command = [
                self.executable,
                "-r",
                str(capture_path),
                "-f",
                f"{self.center_frequency_hz / 1e6:.9f}",
                "-a",
                str(self.sample_rate_hz),
                "-t",
                "0",  # FLOAT32_IQ: interleaved I, Q, I, Q, ...
                "-n",
                str(sample_count),
            ]
            if self.serial:
                command.extend(["-s", self.serial])

            try:
                result = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired as exc:
                raise AirspyError(
                    f"Timed out after {timeout:.1f}s while waiting for {sample_count} samples"
                ) from exc

            if result.returncode != 0:
                details = (result.stderr or result.stdout).strip()
                raise AirspyError(
                    f"airspy_rx failed with exit status {result.returncode}: "
                    f"{details or 'no diagnostic output'}"
                )

            if not capture_path.exists():
                raise AirspyError("airspy_rx completed without creating a sample file")

            interleaved = np.fromfile(capture_path, dtype=np.float32)
            if interleaved.size % 2:
                raise AirspyError(
                    f"Airspy returned an odd number of FLOAT32 IQ components: {interleaved.size}"
                )

            samples = (
                interleaved[0::2] + 1j * interleaved[1::2]
            ).astype(np.complex64, copy=False)
            if samples.size < sample_count:
                raise AirspyError(
                    f"Airspy returned only {samples.size} of {sample_count} requested samples"
                )

            return samples[:sample_count]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Tune an Airspy and capture a finite block of complex IQ samples."
    )
    parser.add_argument(
        "-f",
        "--frequency",
        type=float,
        default=100.1e6,
        help="Centre frequency in Hz (default: 100.1e6)",
    )
    parser.add_argument(
        "-r",
        "--sample-rate",
        type=float,
        default=3e6,
        help="Sample rate in samples/second (default: 3e6)",
    )
    parser.add_argument(
        "-n",
        "--samples",
        type=int,
        default=262_144,
        help="Number of complex IQ samples to capture (default: 262144)",
    )
    parser.add_argument("--serial", help="Optional 64-bit Airspy serial number")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Optionally save the captured complex64 samples as a NumPy .npy file",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        radio = Airspy(serial=args.serial)
        radio.set_center_frequency(args.frequency)
        radio.set_sample_rate(args.sample_rate)
        samples = radio.read_samples(args.samples)
    except (AirspyError, ValueError) as exc:
        print(f"Airspy capture failed: {exc}")
        return 1

    mean_power = float(np.mean(np.abs(samples) ** 2))
    print(f"Centre frequency : {radio.center_frequency_hz / 1e6:.6f} MHz")
    print(f"Sample rate      : {radio.sample_rate_hz / 1e6:.6f} Msps")
    print(f"Samples captured : {samples.size}")
    print(f"Mean IQ power    : {mean_power:.6g}")
    print(f"First 10 samples : {samples[:10]}")

    if args.output is not None:
        output_path = args.output.expanduser()
        saved_path = (
            output_path
            if output_path.suffix == ".npy"
            else Path(f"{output_path}.npy")
        )
        np.save(saved_path, samples)
        print(f"Saved samples    : {saved_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
