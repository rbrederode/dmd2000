"""Read GQRX raw IQ recordings and process them through OBS scan logic.

GQRX raw IQ recordings are stored as interleaved 32-bit floating point
complex samples. This module keeps the Julia ``open_rawdata`` behaviour
available via :func:`read_raw`, and adds a sidecar CLI for turning one raw
recording into DMD2000 scan products without changing the live applications.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from models.fil import FilterBank
from models.obs import ObsModel, ObsState
from models.scan import ScanModel, ScanState, ScanType
from models.target import TargetModel, TargetScanSet
from obs.scan import Scan

logger = logging.getLogger(__name__)


@dataclass
class GQRXProcessingResult:
    raw_path: Path
    output_dir: Path
    obs_path: Path
    scan_models: list[ScanModel]
    fil_paths: list[str]


def read_raw(filename: str, path: str | os.PathLike[str] = ".") -> tuple[int, np.memmap]:
    """Memory-map a raw GQRX IQ file as ``np.complex64`` samples.

    This mirrors the Julia ``open_rawdata(fname)`` routine:

    - open the file
    - map the entire file as ``ComplexF32`` / ``np.complex64``
    - return ``sizeof(raw) // 64`` and the mapped sample vector

    Parameters
    ----------
    filename:
        Name of the raw IQ file. A full path is accepted; in that case ``path``
        is ignored.
    path:
        Directory containing ``filename``.
    """

    raw_path = Path(filename).expanduser()
    if not raw_path.is_absolute():
        raw_path = Path(path).expanduser() / raw_path
    raw_path = raw_path.resolve()

    if not raw_path.exists():
        raise FileNotFoundError(f"GQRX raw IQ file not found: {raw_path}")
    if not raw_path.is_file():
        raise ValueError(f"GQRX raw IQ path is not a file: {raw_path}")

    file_size = raw_path.stat().st_size
    sample_size = np.dtype(np.complex64).itemsize
    if file_size % sample_size != 0:
        raise ValueError(
            f"GQRX raw IQ file size ({file_size} bytes) is not divisible by "
            f"{sample_size}; expected complex64 samples."
        )

    raw = np.memmap(raw_path, dtype=np.complex64, mode="r")
    general_nmax = file_size // 64
    logger.info("FileOpenOK: %s", raw_path)
    return general_nmax, raw


def _parse_gqrx_filename(raw_path: Path) -> tuple[float | None, float | None]:
    """Infer center frequency and sample rate from common GQRX filenames."""

    parts = raw_path.name.split("_")
    if len(parts) >= 5:
        try:
            center_freq = float(parts[3])
            sample_rate = float(re.split(r"[^0-9.]+", parts[4])[0])
            return center_freq, sample_rate
        except (TypeError, ValueError):
            pass
    return None, None


def _parse_datetime(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)

    normalised = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalised)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _write_scan_sidecars(scan: Scan, output_dir: Path) -> None:
    prefix = scan.scan_model.files_prefix
    if not prefix:
        raise RuntimeError(f"Scan {scan.scan_model.scan_id} completed without a file prefix")

    with open(output_dir / f"{prefix}-meta.json", "w") as f:
        json.dump(scan.get_scan_meta(), f, indent=4)
    with open(output_dir / f"{prefix}-qa.json", "w") as f:
        json.dump(scan.get_qa_meta(), f, indent=4)
    with open(output_dir / f"{prefix}-spr.csv", "w") as f:
        np.savetxt(f, scan.spr, delimiter=",", fmt="%.6f")


class GQRXScan(Scan):
    """Scan variant that can select a frequency-offset subband from GQRX IQ."""

    def __init__(self, scan_model: ScanModel, raw_center_freq: float, retain_iq: bool = False):
        self.raw_center_freq = float(raw_center_freq)
        super().__init__(scan_model, retain_iq=retain_iq)

    def _fb_rows_from_iq(self, iq: np.ndarray) -> np.ndarray:
        """Create filterbank rows, optionally offset from the tuner center."""

        samples_per_row = self._fb_samples_per_row()
        channels = int(self.scan_model.spectral_resolution)
        usable = (len(iq) // samples_per_row) * samples_per_row

        if usable == 0:
            return np.empty((0, channels), dtype=np.float32)

        bin_start, bin_end = self._fb_bin_range(samples_per_row, channels, raw_center_freq=self.raw_center_freq)
        chunks = np.asarray(iq[:usable], dtype=np.complex64).reshape(-1, samples_per_row)
        pwr = np.abs(np.fft.fftshift(np.fft.fft(chunks, axis=1), axes=1)) ** 2
        pwr = pwr[:, bin_start:bin_end]
        nave = (bin_end - bin_start) // channels
        fb = pwr.reshape(pwr.shape[0], nave, channels, order="F").mean(axis=1).astype(np.float32, copy=False)
        bad_rows = ~np.isfinite(np.mean(fb, axis=1))
        if np.any(bad_rows):
            fb[bad_rows, :] = 0
        return fb


def process_raw_to_scans(
    filename: str,
    path: str | os.PathLike[str] = ".",
    output_dir: str | os.PathLike[str] | None = None,
    sample_rate: float | None = None,
    center_freq: float | None = None,
    sub_center_freq: float | None = None,
    spectral_resolution: int = 100,
    temporal_resolution: float = 1.0,
    sub_bandwidth: float | None = None,
    dtype: str = "uint8",
    gain: float = 0.0,
    scan_duration: int = 60,
    max_duration: int | None = 10800,
    start_time: datetime | None = None,
    obs_id: str | None = None,
    dig_id: str = "gqrx",
    target_id: str = "gqrx",
    make_fil: bool = False,
) -> GQRXProcessingResult:
    """Convert a raw GQRX file into scan products and optionally a ``.fil``."""

    _, raw = read_raw(filename, path)
    raw_path = Path(filename).expanduser()
    if not raw_path.is_absolute():
        raw_path = Path(path).expanduser() / raw_path
    raw_path = raw_path.resolve()

    inferred_center_freq, inferred_sample_rate = _parse_gqrx_filename(raw_path)
    sample_rate = sample_rate if sample_rate is not None else inferred_sample_rate
    center_freq = center_freq if center_freq is not None else inferred_center_freq

    if sample_rate is None or float(sample_rate) <= 0.0:
        raise ValueError("sample_rate must be provided or inferable from the GQRX filename")
    if center_freq is None or float(center_freq) <= 0.0:
        raise ValueError("center_freq must be provided or inferable from the GQRX filename")
    sample_rate = float(sample_rate)
    center_freq = float(center_freq)
    sub_center_freq = float(sub_center_freq if sub_center_freq is not None else center_freq)
    if spectral_resolution <= 0:
        raise ValueError("spectral_resolution must be positive")
    if scan_duration <= 0:
        raise ValueError("scan_duration must be positive")

    total_seconds = int(len(raw) // int(sample_rate))
    if max_duration is not None:
        total_seconds = min(total_seconds, int(max_duration))
    if total_seconds <= 0:
        raise ValueError("GQRX raw IQ file does not contain one complete second of samples")

    output_path = Path(output_dir or raw_path.parent).expanduser().resolve()
    output_path.mkdir(parents=True, exist_ok=True)

    start = start_time or datetime.now(timezone.utc)
    obs_id = obs_id or f"gqrx-{start.strftime('%Y%m%dT%H%M%SZ')}"
    output_bandwidth = float(sub_bandwidth if sub_bandwidth is not None else sample_rate)
    filter_bank = FilterBank(
        enabled=True,
        sub_bandwidth=sub_bandwidth,
        sub_center_freq=sub_center_freq,
        temporal_resolution=temporal_resolution,
        dtype=dtype,
    )

    Scan.reset_scan_iter_counter(obs_id=obs_id, tgt_idx=0, freq_scan=0)
    scan_models: list[ScanModel] = []
    seconds_done = 0

    while seconds_done < total_seconds:
        duration = min(scan_duration, total_seconds - seconds_done)
        scan_start = start + timedelta(seconds=seconds_done)
        scan_model = ScanModel(
            obs_id=obs_id,
            tgt_idx=0,
            freq_scan=0,
            scan_iter=len(scan_models),
            scan_type=ScanType.SKY,
            dig_id=dig_id,
            created=scan_start,
            start_idx=seconds_done,
            duration=duration,
            sample_rate=sample_rate,
            spectral_resolution=spectral_resolution,
            start_freq=sub_center_freq - output_bandwidth / 2.0,
            center_freq=sub_center_freq,
            end_freq=sub_center_freq + output_bandwidth / 2.0,
            gain=gain,
            load=False,
            status=ScanState.EMPTY,
            filter_bank=filter_bank,
            files_directory=str(output_path),
            last_update=scan_start,
        )
        scan = GQRXScan(scan_model, raw_center_freq=center_freq)

        for sec in range(1, duration + 1):
            absolute_sec = seconds_done + sec - 1
            sample_start = int(absolute_sec * sample_rate)
            sample_end = sample_start + int(sample_rate)
            read_start = start + timedelta(seconds=absolute_sec)
            read_end = read_start + timedelta(seconds=1)
            if not scan.load_samples(sec, raw[sample_start:sample_end], read_start, read_end):
                raise RuntimeError(f"Failed to load second {sec} for scan {scan.scan_model.scan_id}")

        _write_scan_sidecars(scan, output_path)
        scan_models.append(scan.scan_model)
        seconds_done += duration

    target = TargetModel(obs_id=obs_id, tgt_idx=0, id=target_id)
    target_scans = TargetScanSet(
        obs_id=obs_id,
        tgt_idx=0,
        freq_min=sub_center_freq - output_bandwidth / 2.0,
        freq_max=sub_center_freq + output_bandwidth / 2.0,
        freq_scans=1,
        scan_iterations=len(scan_models),
        scan_duration=scan_duration,
        scans=scan_models,
    )
    obs = ObsModel(
        obs_id=obs_id,
        title=f"GQRX import {raw_path.name}",
        description=f"Sidecar import from raw GQRX IQ file {raw_path}",
        obs_state=ObsState.IDLE,
        targets=[target],
        target_scans=[target_scans],
        start_dt=start,
        end_dt=start + timedelta(seconds=total_seconds),
        last_update=datetime.now(timezone.utc),
    )
    obs.save_to_disk(str(output_path))
    obs_path = output_path / f"{obs.obs_id.replace(':', '')}-obs.json"

    fil_paths = []
    if make_fil:
        from obs.opt import export_filterbank_observation_to_fil

        fil_paths = export_filterbank_observation_to_fil(obs, str(output_path), str(output_path))
    return GQRXProcessingResult(raw_path, output_path, obs_path, scan_models, fil_paths)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read raw GQRX complex64 IQ samples and create DMD2000 scan products.")
    parser.add_argument("filename", help="Raw GQRX IQ filename, or a full path to the file.")
    parser.add_argument("--path", default=".", help="Directory containing filename when filename is relative.")
    parser.add_argument("--output-dir", default=None, help="Directory for generated scan products. Defaults to the raw file directory.")
    parser.add_argument("--sample-rate", type=float, default=None, help="Sample rate in Hz. Inferred from common GQRX filenames when omitted.")
    parser.add_argument("--center-freq", type=float, default=None, help="Center frequency in Hz. Inferred from common GQRX filenames when omitted.")
    parser.add_argument("--sub-center-freq", type=float, default=None, help="Center frequency in Hz for the output filterbank subband. Defaults to --center-freq.")
    parser.add_argument("--spectral-resolution", type=int, default=100, help="Number of output spectral channels.")
    parser.add_argument("--temporal-resolution", type=float, default=1.0, help="Filterbank time resolution in milliseconds.")
    parser.add_argument("--sub-bandwidth", type=float, default=None, help="Output sub-bandwidth in Hz. Defaults to the full sample rate.")
    parser.add_argument("--dtype", default="uint8", choices=["uint8", "uint16", "float32", "float64"], help="Filterbank output dtype.")
    parser.add_argument("--gain", type=float, default=0.0, help="Gain metadata value for generated scans.")
    parser.add_argument("--scan-duration", type=int, default=60, help="Seconds per generated ScanModel chunk.")
    parser.add_argument("--max-duration", type=int, default=10800, help="Maximum seconds to import. Use 0 for the whole file.")
    parser.add_argument("--start-time", default=None, help="UTC ISO timestamp for the first sample, e.g. 2026-06-06T12:00:00Z.")
    parser.add_argument("--obs-id", default=None, help="Observation id for generated metadata.")
    parser.add_argument("--dig-id", default="gqrx", help="Digitiser id metadata value.")
    parser.add_argument("--target-id", default="gqrx", help="Target/source name for generated observation metadata.")
    parser.add_argument("--make-fil", action="store_true", help="Also stitch generated filterbank scan files into a SIGPROC .fil file.")
    return parser


def main(argv: list[str] | None = None) -> GQRXProcessingResult:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    args = build_arg_parser().parse_args(argv)
    result = process_raw_to_scans(
        filename=args.filename,
        path=args.path,
        output_dir=args.output_dir,
        sample_rate=args.sample_rate,
        center_freq=args.center_freq,
        sub_center_freq=args.sub_center_freq,
        spectral_resolution=args.spectral_resolution,
        temporal_resolution=args.temporal_resolution,
        sub_bandwidth=args.sub_bandwidth,
        dtype=args.dtype,
        gain=args.gain,
        scan_duration=args.scan_duration,
        max_duration=None if args.max_duration == 0 else args.max_duration,
        start_time=_parse_datetime(args.start_time),
        obs_id=args.obs_id,
        dig_id=args.dig_id,
        target_id=args.target_id,
        make_fil=args.make_fil,
    )
    print(f"Read {result.raw_path}")
    print(f"Wrote {len(result.scan_models)} scan(s) to {result.output_dir}")
    print(f"Wrote observation metadata {result.obs_path}")
    for fil_path in result.fil_paths:
        print(f"Wrote filterbank {fil_path}")
    return result


if __name__ == "__main__":
    main()
