import argparse
import csv
from contextlib import contextmanager
from datetime import timedelta
import os
import logging
import re
from queue import Queue

import numpy as np
from astropy.time import Time

from models.obs import ObsModel
from models.scan import ScanState, ScanType
try:
    from obs.obs import Observation
    from obs.obs_display import ObsDisplay
except ModuleNotFoundError:
    from obs import Observation
    from obs_display import ObsDisplay
from util.format import fmt_cell, fmt_float, fmt_hyperlink, fmt_target_coords, fmt_title

logger = logging.getLogger(__name__)
FILTERBANK_STORAGE_DTYPE = np.dtype("float32")

@contextmanager
def redirect_root_logging(log_path: str):
    """ Keep console output clean while sending log output to a file.
        Parameters:
            log_path: Absolute or relative path to the log file to write.

        Returns:
            A context manager that restores the original root logger handlers on exit. 
    """
    root_logger = logging.getLogger()
    old_handlers = root_logger.handlers[:]
    old_level = root_logger.level

    file_handler = logging.FileHandler(log_path, mode="w")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))

    root_logger.handlers = []
    root_logger.addHandler(file_handler)

    try:
        yield
    finally:
        file_handler.close()
        root_logger.handlers = old_handlers
        root_logger.setLevel(old_level)

def init_args():
    """ Define and parse the command-line interface for the observation
        processing tool.

        Returns:
            The parsed ``argparse.Namespace`` containing the CLI options. 
    """
    parser = argparse.ArgumentParser(description='Observation Processing Tool (OPT) for loading and processing Summed Power CSV files associated with an observation.')
    parser.add_argument('-d', '--dir', type=str, required=False, help='Directory containing observation and scan data files e.g. ~/samples', default='~/samples')
    parser.add_argument('-o', '--obs', type=str, required=True, help='Observation Id of the observation to process e.g. ODT-2026-03-28T1300Z-dish002', default='ODT-2026-03-28T1300Z-dish002')
    parser.add_argument('-p', '--profile', type=str, required=False, help="Configuration profile to use e.g. default, alston etc. See ./config directory for existing profiles", default='default')
    parser.add_argument('--make-fil', action='store_true', help='Stitch SKY filterbank *-fb.dat scan files into SIGPROC .fil files and exit.')
    parser.add_argument('--fil-output-dir', type=str, required=False, help='Directory for generated .fil files. Defaults to --dir.')

    return parser.parse_args()

def print_sky_scans(obs: ObsModel, sky_q: Queue | None = None, blacklist: list[str] | None = None, scan_filter=None):
    """ Print a table of SKY scans in the observation.
        Parameters:
            obs:         Observation model containing the scan metadata to print.
            sky_q:       Optional queue of processed SKY ``Scan`` objects used to look up
                         QA values such as SNR, FWHM, and dynamic range.
            blacklist:   Optional list of full scan IDs currently marked as excluded.
            scan_filter: Optional predicate receiving a ``ScanModel`` and returning
                         ``True`` when the row should be printed. 
    """
    blacklist = blacklist or []

    columns = [
        ("Excl", 4),
        ("Scan Index", 10),
        ("Scan Type", 10),
        ("Tgt ID", 14),
        ("Tgt Coordinates", 30),
        ("Tgt Pointing Type", 18),
        ("Feed", 10),
        ("Dig ID", 6),
        ("Center Freq", 11),
        ("Sample Rate", 11),
        ("Gain", 7),
        ("Spec Res", 8),
        ("Duration", 8),
        ("Image", 8),
    ]

    if sky_q is not None and len(sky_q.queue) > 0:
        columns.append(("SNR (dB)", 10))
        columns.append(("FWHM", 10))
        columns.append(("DR (dB)", 10))

    header = " ".join(fmt_cell(name, width) for name, width in columns)
    divider = " ".join("-" * width for _, width in columns)
    print(header)
    print(divider)

    for tgt_idx, target_scan_set in enumerate(obs.target_scans):
  
        target = obs.get_target_by_index(tgt_idx)
        target_config = obs.get_target_config_by_index(tgt_idx)
        target_scan_set = obs.get_target_scan_set_by_index(tgt_idx)

        for scan_idx, scan_model in enumerate(target_scan_set.scans):
            if scan_model.scan_type not in [ScanType.SKY]:
                continue
            if scan_filter is not None and not scan_filter(scan_model):
                continue

            coords = fmt_target_coords(target)

            row = [
                "[x]" if scan_model.scan_id in blacklist else "[ ]",
                "-".join(scan_model.scan_id.split("-")[-3:]) if len(scan_model.scan_id.split("-")) > 2 else scan_model.scan_id,
                scan_model.scan_type.name,
                target.id if target is not None and target.id is not None else "",
                coords,
                target.pointing.name if target is not None and target.pointing is not None else "",
                target_config.feed_type.name if target_config is not None and target_config.feed_type is not None else "",
                scan_model.dig_id,
                fmt_float(scan_model.center_freq, scale=1e6, precision=2, suffix=" MHz"),
                fmt_float(scan_model.sample_rate, scale=1e6, precision=2, suffix=" MHz"),
                fmt_float(scan_model.gain, precision=1, suffix=" dB"),
                str(scan_model.spectral_resolution),
                fmt_float(scan_model.duration, precision=0, suffix=" s"),
                _scan_image_link(scan_model, 8),
            ]

            if sky_q is not None and len(sky_q.queue) > 0:

                matching_scans = [
                    s for s in list(sky_q.queue)
                    if s.scan_model == scan_model
                    or (
                        s.scan_model.tgt_idx == scan_model.tgt_idx
                        and s.scan_model.freq_scan == scan_model.freq_scan
                        and s.get_scan_type() == ScanType.SKY
                    )
                ]

                if len(matching_scans) > 0:
                    latest_scan = max(matching_scans, key=lambda s: s.scan_model.created)

                    scan_qa = latest_scan.get_qa()
                    mpr_qa = scan_qa.getQA("mpr", 0) if scan_qa is not None else None
                    snr_db = getattr(mpr_qa, "snr_db", None)
                    fwhm = getattr(mpr_qa, "fwhm", None)
                    dr_db = getattr(mpr_qa, "dynamic_range_db", None)
                    row.append(fmt_float(snr_db, precision=2))
                    row.append(fmt_float(fwhm, precision=2))
                    row.append(fmt_float(dr_db, precision=2))

            print(" ".join(fmt_cell(value, width) for value, (_, width) in zip(row, columns)))

    print("")

def print_cal_scans(obs: ObsModel, cal_q: Queue | None = None, blacklist: list[str] | None = None, scan_filter=None):
    """ Print a table of calibration scans in the observation.
        Parameters:
            obs:         Observation model containing the scan metadata to print.
            cal_q:       Optional queue of processed calibration scans. Currently unused
                         in table rendering but kept for symmetry and future extension.
            blacklist:   Optional list of full scan IDs currently marked as excluded.
            scan_filter: Optional predicate receiving a ``ScanModel`` and returning
                         ``True`` when the row should be printed. 
    """
    blacklist = blacklist or []

    columns = [
        ("Excl", 4),
        ("Scan Index", 10),
        ("Scan Type", 10),
        ("Dig ID", 6),
        ("Center Freq", 11),
        ("Sample Rate", 11),
        ("Gain", 7),
        ("Spec Res", 8),
        ("Duration", 8),
        ("Image", 8),
    ]

    header = " ".join(fmt_cell(name, width) for name, width in columns)
    divider = " ".join("-" * width for _, width in columns)
    print(header)
    print(divider)

    for tgt_idx, target_scan_set in enumerate(obs.target_scans):
  
        target = obs.get_target_by_index(tgt_idx)
        target_config = obs.get_target_config_by_index(tgt_idx)
        target_scan_set = obs.get_target_scan_set_by_index(tgt_idx)

        for scan_idx, scan_model in enumerate(target_scan_set.scans):
            if scan_model.scan_type in [ScanType.SKY]:
                continue
            if scan_filter is not None and not scan_filter(scan_model):
                continue

            row = [
                "[x]" if scan_model.scan_id in blacklist else "[ ]",
                "-".join(scan_model.scan_id.split("-")[-3:]) if len(scan_model.scan_id.split("-")) > 2 else scan_model.scan_id,
                scan_model.scan_type.name,
                scan_model.dig_id,
                fmt_float(scan_model.center_freq, scale=1e6, precision=2, suffix=" MHz"),
                fmt_float(scan_model.sample_rate, scale=1e6, precision=2, suffix=" MHz"),
                fmt_float(scan_model.gain, precision=1, suffix=" dB"),
                str(scan_model.spectral_resolution),
                fmt_float(scan_model.duration, precision=0, suffix=" s"),
                _scan_image_link(scan_model, 8),
            ]
            print(" ".join(fmt_cell(value, width) for value, (_, width) in zip(row, columns)))

    print("")

def print_aggregated_scans(obs: ObsModel, sky_q: Queue | None = None, int_arrays: dict | None = None, blacklist: list[str] | None = None, scan_filter=None):
    """ Print a table of processed aggregated SKY scans held in ``sky_q``.

        Parameters:
            obs:         Observation model used to look up target metadata for
                         each aggregated scan.
            sky_q:       Queue containing aggregated SKY ``Scan`` objects.
            int_arrays:  Optional integrated array dictionary used to source the
                         aggregated duration in seconds for each scan.
            blacklist:   Optional list of full scan IDs currently marked as
                         excluded.
            scan_filter: Optional predicate receiving a ``Scan`` and returning
                         ``True`` when the row should be printed.
    """
    fmt_title("Aggregated SKY Scans")

    blacklist = blacklist or []

    columns = [
        ("Excl", 4),
        ("Scan Index", 10),
        ("Scan Type", 10),
        ("Tgt ID", 14),
        ("Tgt Coordinates", 30),
        ("Tgt Pointing Type", 18),
        ("Feed", 10),
        ("Dig ID", 6),
        ("Center Freq", 11),
        ("Sample Rate", 11),
        ("Gain", 7),
        ("Spec Res", 8),
        ("Duration", 8),
        ("Image", 8),
        ("SNR (dB)", 10),
        ("FWHM", 10),
        ("DR (dB)", 10),
    ]

    header = " ".join(fmt_cell(name, width) for name, width in columns)
    divider = " ".join("-" * width for _, width in columns)
    print(header)
    print(divider)

    if sky_q is None or len(sky_q.queue) == 0:
        print("")
        return

    for scan in list(sky_q.queue):
        if scan is None or scan.get_scan_type() != ScanType.SKY:
            continue
        if scan_filter is not None and not scan_filter(scan):
            continue

        scan_model = scan.scan_model
        tgt_idx = scan_model.tgt_idx
        target = obs.get_target_by_index(tgt_idx) if tgt_idx is not None and tgt_idx >= 0 else None
        target_config = obs.get_target_config_by_index(tgt_idx) if tgt_idx is not None and tgt_idx >= 0 else None

        coords = fmt_target_coords(target)

        scan_id_parts = str(scan_model.scan_id).split("-")
        scan_index = "-".join(scan_id_parts[-2:]) if int(getattr(scan_model, "scan_iter", -1)) < 0 else _get_scan_index(scan_model.scan_id)
        integrated_secs = None
        if int_arrays is not None:
            integrated_entry = int_arrays.get((scan_model.tgt_idx, scan_model.freq_scan))
            integrated_secs = integrated_entry.get("secs") if integrated_entry is not None else None

        scan_qa = scan.get_qa()
        mpr_qa = scan_qa.getQA("mpr", 0) if scan_qa is not None else None
        snr_db = getattr(mpr_qa, "snr_db", None)
        fwhm = getattr(mpr_qa, "fwhm", None)
        dr_db = getattr(mpr_qa, "dynamic_range_db", None)

        row = [
            "[x]" if scan_model.scan_id in blacklist else "[ ]",
            scan_index,
            scan_model.scan_type.name,
            target.id if target is not None and target.id is not None else "",
            coords,
            target.pointing.name if target is not None and target.pointing is not None else "",
            target_config.feed_type.name if target_config is not None and target_config.feed_type is not None else "",
            scan_model.dig_id,
            fmt_float(scan_model.center_freq, scale=1e6, precision=2, suffix=" MHz"),
            fmt_float(scan_model.sample_rate, scale=1e6, precision=2, suffix=" MHz"),
            fmt_float(scan_model.gain, precision=1, suffix=" dB"),
            str(scan_model.spectral_resolution),
            fmt_float(integrated_secs if integrated_secs is not None else scan_model.duration, precision=0, suffix=" s"),
            _scan_image_link(scan_model, 8),
            fmt_float(snr_db, precision=2),
            fmt_float(fwhm, precision=2),
            fmt_float(dr_db, precision=2),
        ]

        print(" ".join(fmt_cell(value, width) for value, (_, width) in zip(row, columns)))

    print("")

def _scan_image_link(scan_model, width: int = 8) -> str:
    """Build the image hyperlink cell for a scan row. """
    if not getattr(scan_model, "files_prefix", None) or not getattr(scan_model, "files_directory", None):
        return fmt_cell("", width)

    image_path = os.path.abspath(os.path.join(scan_model.files_directory, f"{scan_model.files_prefix}-sigfig.png"))
    label = fmt_cell("open", width)
    return fmt_hyperlink(image_path, label)

def _get_scan_index(scan_id: str) -> str:
    """ Convert a full scan ID into its short ``tgt-freq-iter`` suffix for
        user-facing selection prompts. """
    return "-".join(scan_id.split("-")[-3:]) if scan_id else ""

def _angle_to_sigproc_ra(coord) -> float:
    """Convert an astropy RA angle to SIGPROC HHMMSS.SS numeric format."""
    hms = coord.ra.hms
    return float(f"{int(hms.h):02d}{int(abs(hms.m)):02d}{abs(hms.s):05.2f}")

def _angle_to_sigproc_dec(coord) -> float:
    """Convert an astropy Dec angle to SIGPROC DDMMSS.SS numeric format."""
    dms = coord.dec.signed_dms
    sign = -1.0 if dms.sign < 0 else 1.0
    value = float(f"{int(abs(dms.d)):02d}{int(abs(dms.m)):02d}{abs(dms.s):05.2f}")
    return sign * value

def _telescope_id_from_dsh_id(dsh_id: str | None) -> int:
    """Use the digit component of a dish id such as dish001 as SIGPROC telescope_id."""
    match = re.search(r"\d+", dsh_id or "")
    return int(match.group(0)) if match else 0

def _filterbank_dtype(scan_model) -> np.dtype:
    fb_config = getattr(scan_model, "filter_bank", None)
    return np.dtype(getattr(fb_config, "dtype", None) or "uint8")

def _filterbank_storage_dtype(scan_model) -> np.dtype:
    """Return the on-disk dtype used for intermediate scan filterbank files."""
    return FILTERBANK_STORAGE_DTYPE

def _filterbank_tsamp(scan_model) -> float:
    fb_config = getattr(scan_model, "filter_bank", None)
    temporal_ms = float(getattr(fb_config, "temporal_resolution", 1.0) or 1.0)
    return temporal_ms / 1000.0

def _filterbank_sub_bandwidth(scan_model) -> float:
    """Return the filterbank output sub-bandwidth in Hz."""
    fb_config = getattr(scan_model, "filter_bank", None)
    if fb_config is None:
        return float(scan_model.sample_rate)
    _, sub_bandwidth = fb_config.resolve_subband(
        scan_center_freq=float(scan_model.center_freq),
        scan_bandwidth=float(scan_model.sample_rate),
    )
    return sub_bandwidth

def _filterbank_sub_center_freq(scan_model) -> float:
    """Return the filterbank output sub-band center frequency in Hz."""
    fb_config = getattr(scan_model, "filter_bank", None)
    if fb_config is None:
        return float(scan_model.center_freq)
    sub_center_freq, _ = fb_config.resolve_subband(
        scan_center_freq=float(scan_model.center_freq),
        scan_bandwidth=float(scan_model.sample_rate),
    )
    return sub_center_freq

def _filterbank_gap_mean_duration(scan_model) -> float:
    """Return the gap-fill averaging duration on each side of a gap, in seconds."""
    fb_config = getattr(scan_model, "filter_bank", None)
    duration_ms = float(getattr(fb_config, "gap_mean_duration", 1.0) or 0.0)
    return duration_ms / 1000.0

def _sigproc_nbits(dtype: np.dtype) -> int:
    return int(np.dtype(dtype).itemsize * 8)

def _sample_time_value(scan_model, sec_idx: int, key: str):
    sample_times = getattr(scan_model, "sample_times", None)
    if not sample_times or sec_idx < 0 or sec_idx >= len(sample_times):
        return None

    sample_time = sample_times[sec_idx] or {}
    return sample_time.get(key)

def _scan_second_start_end(scan_model, sec_idx: int, rows_per_sec: int, tsamp: float):
    """Return the start/end timestamps for one second-sized filterbank chunk."""
    start = _sample_time_value(scan_model, sec_idx, "read_start")
    end = _sample_time_value(scan_model, sec_idx, "read_end")

    if start is None and getattr(scan_model, "read_start", None) is not None:
        start = scan_model.read_start + timedelta(seconds=sec_idx * rows_per_sec * tsamp)
    if end is None and start is not None:
        end = start + timedelta(seconds=rows_per_sec * tsamp)

    return start, end

def _coerce_filterbank_spectra(spectra: np.ndarray, dtype: np.dtype) -> np.ndarray:
    """Round and cast filterbank spectra to the final output dtype."""
    dtype = np.dtype(dtype)
    if dtype.kind in {"f", "c"}:
        return spectra.astype(dtype, copy=False)

    info = np.iinfo(dtype)
    return np.rint(np.clip(spectra, info.min, info.max)).astype(dtype, copy=False)

def _filterbank_trimmed_std(values: np.ndarray, prop: float = 0.2) -> float:
    """Compute the Julia script's robust scale estimate from a trimmed vector."""
    flat = np.sort(np.ravel(values))
    trim_n = int(np.floor(flat.size * prop))
    trimmed = flat[trim_n:-trim_n] if trim_n > 0 and trim_n * 2 < flat.size else flat
    return float(np.std(trimmed, ddof=1 if trimmed.size > 1 else 0))

def _load_filterbank_scan_data(scan_model, input_dir: str, nchans: int) -> tuple[str, np.ndarray | None]:
    """Load one unnormalised intermediate scan filterbank file."""
    fb_path = os.path.join(input_dir, f"{scan_model.files_prefix}-fb.dat")
    if not os.path.exists(fb_path):
        logger.warning(f"OPT: Missing filterbank file for scan {scan_model.scan_id}: {fb_path}")
        return fb_path, None

    data = np.fromfile(fb_path, dtype=_filterbank_storage_dtype(scan_model))
    complete_values = (data.size // nchans) * nchans
    if complete_values != data.size:
        logger.warning(
            "OPT: Dropping %d trailing values from %s because they do not make a complete %d-channel row.",
            data.size - complete_values,
            fb_path,
            nchans,
        )
        data = data[:complete_values]
    if data.size == 0:
        return fb_path, None

    return fb_path, data.reshape(-1, nchans)

def _filterbank_group_trimmed_std(scans: list, input_dir: str, nchans: int) -> float | None:
    """Compute one Julia-style trimmed std across all scans in a .fil group."""
    arrays = []
    for scan_model in scans:
        _, data = _load_filterbank_scan_data(scan_model, input_dir, nchans)
        if data is not None:
            arrays.append(np.ravel(data))

    if not arrays:
        return None

    trim_std = _filterbank_trimmed_std(np.concatenate(arrays))
    if not np.isfinite(trim_std) or trim_std <= 0.0:
        return None
    return trim_std

def _normalise_filterbank_spectra(spectra: np.ndarray, trim_std: float) -> np.ndarray:
    """Apply Julia-style observation-level normalisation before output."""
    spectra = spectra.astype(np.float32, copy=False) / trim_std
    spectra[~np.isfinite(spectra)] = 0
    spectra[spectra > 255] = 0
    return spectra

def _gap_fill_spectra(
    prev_tail: np.ndarray | None,
    current_head: np.ndarray | None,
    gap_rows: int,
    dtype: np.dtype,
) -> np.ndarray:
    """Build synthetic spectra for a time gap from edge-row means."""
    if gap_rows <= 0:
        nchans = 0 if current_head is None or current_head.ndim != 2 else current_head.shape[1]
        return np.empty((0, nchans), dtype=dtype)

    edge_rows = [rows.astype(np.float64, copy=False) for rows in (prev_tail, current_head) if rows is not None and rows.size > 0]
    if not edge_rows:
        nchans = 0
        return np.empty((0, nchans), dtype=dtype)

    mean_row = np.mean(np.concatenate(edge_rows, axis=0), axis=0, keepdims=True)
    fill = np.repeat(mean_row, gap_rows, axis=0)
    return _coerce_filterbank_spectra(fill, dtype)

def _write_filterbank_scan_with_gap_fill(
    out_file,
    scan_model,
    spectra: np.ndarray,
    dtype: np.dtype,
    tsamp: float,
    prev_end,
    prev_tail: np.ndarray | None,
) -> tuple[int, int, object, np.ndarray | None]:
    """Write one scan's spectra, inserting synthetic rows for metadata gaps."""
    duration = max(1, int(scan_model.duration or 1))
    rows_per_sec = max(1, spectra.shape[0] // duration)
    mean_rows = max(1, int(round(_filterbank_gap_mean_duration(scan_model) / tsamp))) if tsamp > 0 else 1
    rows_written = 0
    rows_inserted = 0

    for sec_idx in range(duration):
        row_start = sec_idx * rows_per_sec
        row_end = (sec_idx + 1) * rows_per_sec if sec_idx < duration - 1 else spectra.shape[0]
        chunk = spectra[row_start:row_end, :]
        if chunk.size == 0:
            continue

        current_start, current_end = _scan_second_start_end(scan_model, sec_idx, chunk.shape[0], tsamp)
        if prev_end is not None and current_start is not None:
            gap_sec = (current_start - prev_end).total_seconds()
            gap_rows = int(round(gap_sec / tsamp)) if tsamp > 0 and gap_sec > 0 else 0
            if gap_rows > 0:
                fill = _gap_fill_spectra(prev_tail, chunk[:mean_rows, :], gap_rows, dtype)
                if fill.size > 0:
                    out_file.write(fill.tobytes())
                    rows_written += fill.shape[0]
                    rows_inserted += fill.shape[0]

        output_chunk = _coerce_filterbank_spectra(chunk, dtype)
        out_file.write(output_chunk.tobytes())
        rows_written += output_chunk.shape[0]
        prev_end = current_end
        prev_tail = chunk[-mean_rows:, :]

    return rows_written, rows_inserted, prev_end, prev_tail

def _fil_output_name(obs: ObsModel, target, scan_model) -> str:
    target_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(getattr(target, "id", None) or f"tgt{scan_model.tgt_idx}"))
    output_center_freq = _filterbank_sub_center_freq(scan_model)
    filename = (
        f"{obs.obs_id}-{target_id}-t{scan_model.tgt_idx}-f{scan_model.freq_scan}-"
        f"cf{round(output_center_freq / 1e6, 2)}-ch{scan_model.spectral_resolution}.fil"
    ).lower()
    return re.sub(r'[:\\/*?"<>|]+', "", filename)

def _iter_filterbank_scans(obs: ObsModel, blacklist: list[str] | None = None):
    """Yield completed SKY scans with enabled filterbank output, grouped by metadata order."""
    blacklist = set(blacklist or [])
    for target_scan_set in obs.target_scans:
        scans = [
            scan_model for scan_model in target_scan_set.scans
            if scan_model is not None
            and scan_model.scan_type == ScanType.SKY
            and scan_model.scan_id not in blacklist
            and scan_model.status == ScanState.COMPLETE
            and getattr(getattr(scan_model, "filter_bank", None), "enabled", False)
            and getattr(scan_model, "files_prefix", None)
        ]
        yield from sorted(scans, key=lambda s: (s.tgt_idx, s.freq_scan, s.scan_iter, s.read_start or s.created))

def export_filterbank_observation_to_fil(
    obs: ObsModel,
    input_dir: str,
    output_dir: str | None = None,
    blacklist: list[str] | None = None,
) -> list[str]:
    """Stitch completed SKY *-fb.dat scan files into SIGPROC .fil files.

    One .fil is written for each ``(target, frequency scan)`` group. Data are
    streamed scan-by-scan, flipped so the first SIGPROC channel is the highest
    frequency, and accompanied by metadata from the observation and scan models.
    """
    try:
        from your.formats.filwriter import make_sigproc_object
    except ImportError as exc:
        raise RuntimeError("Generating .fil files requires the 'your' package.") from exc

    input_dir = os.path.expanduser(input_dir)
    output_dir = os.path.expanduser(output_dir or input_dir)
    os.makedirs(output_dir, exist_ok=True)

    scan_groups = {}
    for scan_model in _iter_filterbank_scans(obs, blacklist=blacklist):
        scan_groups.setdefault((scan_model.tgt_idx, scan_model.freq_scan), []).append(scan_model)

    written_files = []
    for (tgt_idx, freq_scan), scans in sorted(scan_groups.items()):
        if len(scans) == 0:
            continue

        first_scan = scans[0]
        target = obs.get_target_by_index(tgt_idx)
        source_name = str(getattr(target, "id", None) or f"target_{tgt_idx}")
        sky_coord = getattr(target, "sky_coord", None)
        src_raj = _angle_to_sigproc_ra(sky_coord) if sky_coord is not None and hasattr(sky_coord, "ra") else 0.0
        src_dej = _angle_to_sigproc_dec(sky_coord) if sky_coord is not None and hasattr(sky_coord, "dec") else 0.0

        nchans = int(first_scan.spectral_resolution)
        filterbank_sub_bandwidth = _filterbank_sub_bandwidth(first_scan)
        filterbank_sub_center_freq = _filterbank_sub_center_freq(first_scan)
        chan_width_mhz = (filterbank_sub_bandwidth / nchans) / 1e6
        fch1_mhz = (filterbank_sub_center_freq + filterbank_sub_bandwidth / 2.0) / 1e6 - chan_width_mhz / 2.0
        dtype = _filterbank_dtype(first_scan)
        tsamp = _filterbank_tsamp(first_scan)
        tstart_dt = first_scan.read_start or first_scan.created or obs.start_dt
        tstart_mjd = Time(tstart_dt).mjd
        telescope_id = 0
        trim_std = _filterbank_group_trimmed_std(scans, input_dir, nchans)
        if trim_std is None:
            logger.warning(f"OPT: Skipping filterbank export for target {tgt_idx}, frequency scan {freq_scan}; invalid global trimmed std.")
            continue

        output_path = os.path.join(output_dir, _fil_output_name(obs, target, first_scan))
        sigproc_object = make_sigproc_object(
            rawdatafile=os.path.basename(output_path),
            source_name=source_name,
            nchans=nchans,
            foff=-chan_width_mhz,
            fch1=fch1_mhz,
            tsamp=tsamp,
            tstart=tstart_mjd,
            src_raj=src_raj,
            src_dej=src_dej,
            machine_id=0,
            nbeams=0,
            ibeam=0,
            nbits=_sigproc_nbits(dtype),
            nifs=1,
            barycentric=0,
            pulsarcentric=0,
            telescope_id=telescope_id,
            data_type=1,
            az_start=-1,
            za_start=-1,
        )

        sigproc_object.write_header(output_path)
        rows_written = 0
        rows_inserted = 0
        prev_end = None
        prev_tail = None
        with open(output_path, "ab") as out_file:
            for scan_model in scans:
                _, data = _load_filterbank_scan_data(scan_model, input_dir, nchans)
                if data is None:
                    continue

                spectra = _normalise_filterbank_spectra(data, trim_std)[:, ::-1]
                scan_rows_written, scan_rows_inserted, prev_end, prev_tail = _write_filterbank_scan_with_gap_fill(
                    out_file=out_file,
                    scan_model=scan_model,
                    spectra=spectra,
                    dtype=dtype,
                    tsamp=tsamp,
                    prev_end=prev_end,
                    prev_tail=prev_tail,
                )
                rows_written += scan_rows_written
                rows_inserted += scan_rows_inserted

        written_files.append(output_path)
        logger.info(f"OPT: Wrote {rows_written} spectra from {len(scans)} scans to {output_path} ({rows_inserted} gap-fill spectra)")
        print(f"Wrote {output_path} ({rows_written:,} spectra from {len(scans)} scans, {rows_inserted:,} gap-fill spectra)")

    if not written_files:
        print("No completed SKY filterbank scan files were found to stitch.")
    return written_files

def blacklist_scans(obs: ObsModel, blacklist: list[str] | None = None) -> tuple[list[str], str]:
    """ Let the user incrementally build or reduce the scan blacklist using short scan indexes from the console.
        Parameters:
            obs:        Observation model used to validate user-entered scan indexes.
            blacklist:  Existing list of full scan IDs already excluded.
        Returns:
            A tuple of ``(updated_blacklist, status_message)`` where
            ``updated_blacklist`` is a list of full scan IDs and
            ``status_message`` summarises the latest user action.
    """
    blacklist = list(blacklist or [])
    valid_scan_ids = set()
    scan_index_to_id = {}

    obs_model = obs.obs_model if isinstance(obs, Observation) else obs

    for target_scan_set in obs_model.target_scans:
        for scan_model in target_scan_set.scans:
            scan_index = _get_scan_index(scan_model.scan_id)
            valid_scan_ids.add(scan_model.scan_id)
            scan_index_to_id[scan_index] = f"{obs_model.obs_id}-{scan_index}"

    prompt = (
        "Enter a comma-delimited list of scan indexes to toggle exclusion "
        "(for example 0-0-0,1-0-0), or press Enter when finished: "
    )
    raw_input = input(prompt).strip()

    if raw_input == "":
        return blacklist, ""

    blacklist_set = set(blacklist)
    invalid_indexes = []

    for token in raw_input.split(","):
        scan_index = token.strip()
        if not scan_index:
            continue

        full_scan_id = scan_index_to_id.get(scan_index, f"{obs_model.obs_id}-{scan_index}")
        if full_scan_id in valid_scan_ids:
            if full_scan_id in blacklist_set:
                blacklist_set.remove(full_scan_id)
            else:
                blacklist_set.add(full_scan_id)
        else:
            invalid_indexes.append(scan_index)

    blacklist = sorted(blacklist_set)

    messages = []
    if invalid_indexes:
        messages.append(f"Ignoring unknown scan indexes: {', '.join(invalid_indexes)}")
    if blacklist:
        messages.append(f"Blacklisted scans:\n{', '.join(blacklist)}")
    else:
        messages.append("No scans blacklisted.")

    return blacklist, "\n".join(messages)


def _collapse_integrated_arrays(int_data_arrays: dict, scan_type: ScanType | None = None) -> dict:
    """Collapse per-iteration integrated data into per-(target, freq_scan) entries.

    Parameters:
        int_data_arrays: Observation-integrated data keyed by
            ``(tgt_idx, freq_scan, scan_iter)``.
        scan_type: Optional scan type filter to include only one class of scans.

    Returns:
        A dictionary keyed by ``(tgt_idx, freq_scan)`` with accumulated
        ``secs``, ``scans``, ``tpw_sum``, ``spr_sum`` and ``mpr_sum``.
    """
    collapsed = {}

    for (tgt_idx, freq_scan, _scan_iter), entry in sorted((int_data_arrays or {}).items()):
        if scan_type is not None and entry.get("scan_type") != scan_type:
            continue

        key = (tgt_idx, freq_scan)
        collapsed.setdefault(
            key,
            {
                "scan_type": entry.get("scan_type"),
                "spr_sum": None,
                "mpr_sum": None,
                "tpw_sum": [],
                "secs": 0.0,
                "scans": 0,
            },
        )

        target_entry = collapsed[key]
        spr = entry.get("int_spr")
        mpr = entry.get("int_mpr")
        tpw = entry.get("int_tpw") or []

        if spr is not None:
            target_entry["spr_sum"] = spr.copy() if target_entry["spr_sum"] is None else target_entry["spr_sum"] + spr
        if mpr is not None:
            target_entry["mpr_sum"] = mpr.copy() if target_entry["mpr_sum"] is None else target_entry["mpr_sum"] + mpr

        target_entry["tpw_sum"].extend(tpw)
        target_entry["secs"] += float(entry.get("secs", 0.0) or 0.0)
        target_entry["scans"] += int(entry.get("scans", 0) or 0)

    return collapsed


def save_aggregated_power_csv(obs_id: str, scans, output_dir: str) -> str:
    """Save the data plotted in the Integrated Total Power panel.

    Each row identifies a synthesized SKY scan and contains the total calibrated
    power for one integrated-scan position.

    Returns:
        The path of the written ``<obs_id>-sky-apr.csv`` file.
    """
    output_dir = os.path.expanduser(output_dir)
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{obs_id}-sky-apr.csv")

    sorted_scans = sorted(
        [scan for scan in scans if getattr(scan, "scan_model", None) is not None],
        key=lambda scan: (scan.scan_model.center_freq, scan.scan_model.freq_scan),
    )

    with open(output_path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["scan_id", "tgt_idx", "freq_scan", "integrated_scan", "aggregated_power"])

        for scan in sorted_scans:
            if scan.cal is None:
                continue

            loaded_seconds = min(int(scan.get_loaded_seconds()), scan.cal.shape[0])
            if loaded_seconds <= 0:
                continue

            aggregated_power = np.sum(scan.cal[:loaded_seconds, :], axis=1)
            for integrated_scan, power in enumerate(aggregated_power, start=1):
                writer.writerow(
                    [
                        scan.scan_model.scan_id,
                        scan.scan_model.tgt_idx,
                        scan.scan_model.freq_scan,
                        integrated_scan,
                        float(power),
                    ]
                )

    logger.info(f"OPT: Saved aggregated SKY power data to {output_path}")
    return output_path

def main():
    """ Parse CLI arguments, load observation metadata, let the user manage the
        blacklist, initialise processing state, and replay scans through the
        current OPT workflow.    
    """
    global sky_q, cal_q

    sky_q = Queue()
    cal_q = Queue()

    args = init_args() # Initialize command line arguments
    args.dir = os.path.expanduser(args.dir) if args.dir is not None else args.dir

    preview_obs = Observation.from_disk(dir=args.dir, obs_id=args.obs)
    if preview_obs is None:
        logger.error(f"OPT: Failed to load observation metadata for Observation {args.obs} in {args.dir}. Exiting.")
        return

    if args.make_fil:
        export_filterbank_observation_to_fil(
            obs=preview_obs.obs_model,
            input_dir=args.dir,
            output_dir=args.fil_output_dir,
        )
        return

    log_path = os.path.expanduser(os.path.join(args.dir, f"{args.obs}-opt.log"))

    with redirect_root_logging(log_path):
        
        blacklist = []
        blacklist_msgs = ""
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            print(f"OPT logs written to {log_path}")
            preview_obs.blacklist = set(blacklist)
            print(preview_obs)
            print(preview_obs.describe_cal_scans())
            print(preview_obs.describe_sky_scans())
            if blacklist_msgs:
                print(blacklist_msgs)
                print("")

            updated_blacklist, blacklist_msgs = blacklist_scans(preview_obs, blacklist)
            if updated_blacklist == blacklist:
                break
            blacklist = updated_blacklist

        pipeline_factory = Observation.init_pipeline_factory(input_dir='config/' + args.profile)
        obs = Observation.from_disk(dir=args.dir, obs_id=args.obs, blacklist=blacklist, pipeline_factory=pipeline_factory)
        if obs is None:
            logger.error(f"OPT: Failed to reload observation metadata for Observation {args.obs} in {args.dir}. Exiting.")
            return

        signal_displays = obs.init_signal_displays()

        print(obs.describe_processing_pipeline_factory())

        obs.integrate_cal_scans(dir=args.dir, sky_q=sky_q, cal_q=cal_q)
        obs.synthesise_integrated_scans(sky_q=sky_q, cal_q=cal_q, signal_displays=signal_displays)
        obs.integrate_sky_scans(dir=args.dir, sky_q=sky_q, cal_q=cal_q)
        obs.synthesise_integrated_scans(sky_q=sky_q, cal_q=cal_q, signal_displays=signal_displays)

        collapsed_sky_arrays = _collapse_integrated_arrays(obs.int_data_arrays, scan_type=ScanType.SKY)

        sky_scans = [scan for scan in list(sky_q.queue) if scan.get_scan_type() == ScanType.SKY]
        apr_path = save_aggregated_power_csv(
            obs_id=obs.obs_model.obs_id,
            scans=sky_scans,
            output_dir=args.dir,
        )
        print(f"Aggregated SKY power data written to {apr_path}")

        obs_display = ObsDisplay(obs_id=obs.obs_model.obs_id)
        obs_display.set_scan(
            sky_scans,
            obs=obs.obs_model,
        )
        apr_png_path = obs_display.save_integrated_total_power(os.path.splitext(apr_path)[0] + ".png")
        print(f"Aggregated SKY power plot written to {apr_png_path}")
        obs_display.display()

        print_sky_scans(obs=obs.obs_model, sky_q=sky_q, blacklist=blacklist)
        print_aggregated_scans(obs=obs.obs_model, sky_q=sky_q, int_arrays=collapsed_sky_arrays, blacklist=blacklist)

    input("Press Enter to continue...")

if __name__ == "__main__":
    main()
