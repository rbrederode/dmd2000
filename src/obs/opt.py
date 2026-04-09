import argparse
from contextlib import contextmanager
import os
import json
import numpy as np
import logging
from queue import Queue

from models.obs import Observation
from models.pipeline import PipelineConfig, StepConfig, StepType
from models.scan import ScanDataSource, ScanModel, ScanType, ScanState
from obs.obs_display import ObsDisplay
from scan import Scan
from sdp.pipeline.pipeline_factory import ProcessingPipelineFactory
from sdp.signal_display import SignalDisplay

logger = logging.getLogger(__name__)

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

    return parser.parse_args()

def load_obs_metadata(dir: str, obs_id: str) -> Observation | None:
    """ Load observation metadata from disk.
        Parameters:
            dir:        Directory containing the observation metadata file.
            obs_id:     Observation identifier used to derive the metadata filename.

        Returns:
            An ``Observation`` instance when loading succeeds, otherwise ``None``. 
    """
    obs_path = os.path.join(dir, f"{obs_id}-obs.json")
    obs_path = os.path.expanduser(obs_path)
   
    if not os.path.isfile(obs_path):
        logger.error(f"OPT: Observation metadata file not found: {obs_path}")
        return None
   
    with open(obs_path, 'r') as f:
        obs_data = json.load(f)
    obs = Observation().from_dict(obs_data)
   
    return obs

def _print_title(title: str):
    """ Print a simple section title banner.
        Parameters:
            title:  Title text to print. 
    """
    print("")
    print("="*len(title))
    print(title)
    print("="*len(title)+"\n")

def print_obs_header(obs: Observation):
    """ Print a summary of observation-level metadata.
        Parameters:
            obs: Observation model to summarise. 
    """
    _print_title("Observation Summary")

    print(f"Observation Id: {obs.obs_id}")
    print(f"Dish Id:        {obs.dsh_id}")
    print(f"Dish Diameter:  {obs.diameter}m")
    print(f"User:           {obs.user_email}")
    print(f"Start Time:     {obs.start_dt.isoformat()}")
    print(f"End Time:       {obs.end_dt.isoformat()}")
    print(f"Description:    {obs.description}\n")

def print_sky_scans(obs: Observation, sky_q: Queue | None = None, blacklist: list[str] | None = None, scan_filter=None):
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
        ("Channels", 8),
        ("Duration", 8),
        ("Image", 8),
    ]

    if sky_q is not None and len(sky_q.queue) > 0:
        columns.append(("SNR (dB)", 10))
        columns.append(("FWHM", 10))
        columns.append(("DR (dB)", 10))

    header = " ".join(_fmt_cell(name, width) for name, width in columns)
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

            if target is not None and target.sky_coord is not None:
                coords = f"RA {target.sky_coord.ra.to_string(unit='hour', precision=0)} Dec {target.sky_coord.dec.to_string(unit='deg', precision=2)}"
            elif target is not None and target.altaz is not None:
                coords = f"Alt {target.altaz.alt.to_string(unit='deg', precision=0)} Az {target.altaz.az.to_string(unit='deg', precision=2)}"
            else:
                coords = ""

            row = [
                "[x]" if scan_model.scan_id in blacklist else "[ ]",
                "-".join(scan_model.scan_id.split("-")[-3:]) if len(scan_model.scan_id.split("-")) > 2 else scan_model.scan_id,
                scan_model.scan_type.name,
                target.id if target is not None and target.id is not None else "",
                coords,
                target.pointing.name if target is not None and target.pointing is not None else "",
                target_config.feed.name if target_config is not None and target_config.feed is not None else "",
                scan_model.dig_id,
                _fmt_float(scan_model.center_freq, scale=1e6, precision=2, suffix=" MHz"),
                _fmt_float(scan_model.sample_rate, scale=1e6, precision=2, suffix=" MHz"),
                _fmt_float(scan_model.gain, precision=1, suffix=" dB"),
                str(scan_model.channels),
                _fmt_float(scan_model.duration, precision=0, suffix=" s"),
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
                    row.append(_fmt_float(snr_db, precision=2))
                    row.append(_fmt_float(fwhm, precision=2))
                    row.append(_fmt_float(dr_db, precision=2))

            print(" ".join(_fmt_cell(value, width) for value, (_, width) in zip(row, columns)))

    print("")

def print_cal_scans(obs: Observation, cal_q: Queue | None = None, blacklist: list[str] | None = None, scan_filter=None):
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
        ("Channels", 8),
        ("Duration", 8),
        ("Image", 8),
    ]

    header = " ".join(_fmt_cell(name, width) for name, width in columns)
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
                _fmt_float(scan_model.center_freq, scale=1e6, precision=2, suffix=" MHz"),
                _fmt_float(scan_model.sample_rate, scale=1e6, precision=2, suffix=" MHz"),
                _fmt_float(scan_model.gain, precision=1, suffix=" dB"),
                str(scan_model.channels),
                _fmt_float(scan_model.duration, precision=0, suffix=" s"),
                _scan_image_link(scan_model, 8),
            ]
            print(" ".join(_fmt_cell(value, width) for value, (_, width) in zip(row, columns)))

    print("")

def _fmt_cell(value, width) -> str:
    """Format a single table cell to a fixed width. """
    return f"{str(value):<{width}}"

def _fmt_float(value, scale=1.0, precision=2, suffix="") -> str:
    """Format a numeric value with optional scaling and suffix."""
    if value is None:
        return ""
    return f"{float(value) / scale:.{precision}f}{suffix}"

def _make_hyperlink(path: str, label: str) -> str:
    """Create an OSC 8 terminal hyperlink string. """
    return f"\033]8;;file://{path}\033\\{label}\033]8;;\033\\"

def _scan_image_link(scan_model, width: int = 8) -> str:
    """Build the image hyperlink cell for a scan row. """
    if not getattr(scan_model, "files_prefix", None) or not getattr(scan_model, "files_directory", None):
        return _fmt_cell("", width)

    image_path = os.path.abspath(os.path.join(scan_model.files_directory, f"{scan_model.files_prefix}-sigfig.png"))
    label = _fmt_cell("open", width)
    return _make_hyperlink(image_path, label)

def _format_target_coordinates(target) -> str:
    """Format target coordinates for table output. """
    if target is not None and target.sky_coord is not None:
        return f"RA {target.sky_coord.ra.to_string(unit='hour', precision=0)} Dec {target.sky_coord.dec.to_string(unit='deg', precision=2)}"
    if target is not None and target.altaz is not None:
        return f"Alt {target.altaz.alt.to_string(unit='deg', precision=0)} Az {target.altaz.az.to_string(unit='deg', precision=2)}"
    return ""

def _get_scan_index(scan_id: str) -> str:
    """ Convert a full scan ID into its short ``tgt-freq-iter`` suffix for
        user-facing selection prompts. """
    return "-".join(scan_id.split("-")[-3:]) if scan_id else ""

def blacklist_scans(obs: Observation, blacklist: list[str] | None = None) -> tuple[list[str], str]:
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

    for target_scan_set in obs.target_scans:
        for scan_model in target_scan_set.scans:
            scan_index = _get_scan_index(scan_model.scan_id)
            valid_scan_ids.add(scan_model.scan_id)
            scan_index_to_id[scan_index] = f"{obs.obs_id}-{scan_index}"

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

        full_scan_id = scan_index_to_id.get(scan_index, f"{obs.obs_id}-{scan_index}")
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


def print_pipeline_config(pipeline_factory: ProcessingPipelineFactory):
    """ Show the configured processing steps that OPT will use.
        Parameters:
            pipeline_factory: Factory containing the pipeline configuration to display.
        Returns:
            ``None``.
    """
    print("="*40)
    print("Processing Pipeline Configuration")
    print("="*40)

    print("")
    print(pipeline_factory)
    print("")

def init_sky_arrays(obs: Observation, blacklist: list[str] | None = None) -> dict:
    """ Create per-target, per-frequency-scan accumulators for SKY scan power aggregation while excluding blacklisted scans.
        Parameters:
            obs:        Observation model providing SKY scan metadata.
            blacklist:  Optional list of full scan IDs to exclude.
        Returns:
            A dictionary keyed by target index, where each value is a list indexed
            by frequency scan and containing aggregation entries with ``spr_sum``
            and accumulated ``seconds``, or ``None`` when no SKY scan exists for
            that slot.
    """
    blacklist = set(blacklist or [])
    sky_arrays = {}
    
    for tgt_idx, _ in enumerate(obs.target_scans):

        target_scan_set = obs.get_target_scan_set_by_index(tgt_idx)
        freq_scan_arrays = []

        if target_scan_set is None or target_scan_set.freq_scans is None:
            sky_arrays[tgt_idx] = freq_scan_arrays
            continue

        freq_scans = target_scan_set.freq_scans
        scan_iterations = target_scan_set.scan_iterations or 0

        for freq_scan_idx in range(freq_scans):
            first_scan_model = None

            for scan_iter in range(scan_iterations):

                scan_model = target_scan_set.get_scan_by_index(freq_scan_idx, scan_iter)

                if scan_model is None or scan_model.scan_id in blacklist or scan_model.scan_type != ScanType.SKY:
                    continue

                if scan_model is not None:
                    first_scan_model = scan_model
                    break

            if first_scan_model is not None:
                freq_scan_arrays.append({
                    "spr_sum": np.zeros(first_scan_model.channels),
                    "seconds": 0,
                })
            else:
                freq_scan_arrays.append(None)

        sky_arrays[tgt_idx] = freq_scan_arrays

    return sky_arrays

def init_cal_arrays(obs: Observation, blacklist: list[str] | None = None) -> dict:
    """ Create calibration accumulation containers keyed by calibration scan
        type and frequency scan index, excluding blacklisted scans.

        Parameters:
            obs:        Observation model providing calibration scan metadata.
            blacklist:  Optional list of full scan IDs to exclude.

        Returns:
            A nested dictionary of the form
            ``cal_arrays[scan_type][freq_scan_idx]`` containing ``mpr_sum`` and
            accumulated ``count`` for each calibration slot.
    """
    blacklist = set(blacklist or [])
    cal_arrays = {}
    template_by_type = {}
    freq_scans_by_type = {}

    for target_scan_set in obs.target_scans:
        for scan_model in target_scan_set.scans:
            if scan_model is None or scan_model.scan_id in blacklist or scan_model.scan_type == ScanType.SKY:
                continue

            if scan_model.scan_type not in template_by_type:
                template_by_type[scan_model.scan_type] = scan_model.channels
            if scan_model.scan_type not in freq_scans_by_type:
                freq_scans_by_type[scan_model.scan_type] = set()
            freq_scans_by_type[scan_model.scan_type].add(scan_model.freq_scan)

    for scan_type, channels in template_by_type.items():
        cal_arrays[scan_type] = {}
        for freq_scan_idx in sorted(freq_scans_by_type.get(scan_type, set())):
            cal_arrays[scan_type][freq_scan_idx] = {
                "mpr_sum": np.zeros(channels),
                "count": 0,
            }

    return cal_arrays

def update_cal_arrays(cal_arrays: dict, scan: Scan):
    """ Add a processed calibration scan's mean power spectrum to the
        relevant calibration aggregation entry.

        Parameters:
            cal_arrays: Calibration aggregation dictionary created by
                        ``init_cal_arrays``.
            scan:       Processed calibration ``Scan`` whose ``mpr`` should be
                        accumulated.
    """
    if scan is None or scan.get_scan_type() == ScanType.SKY or scan.mpr is None:
        return

    scan_type = scan.get_scan_type()
    freq_scan_idx = scan.scan_model.freq_scan

    if scan_type not in cal_arrays or freq_scan_idx not in cal_arrays[scan_type]:
        return

    cal_arrays[scan_type][freq_scan_idx]["mpr_sum"] += scan.mpr
    cal_arrays[scan_type][freq_scan_idx]["count"] += 1

def update_sky_arrays(sky_arrays: dict, scan: Scan):
    """Accumulate a SKY scan's summed power spectrum into sky_arrays."""

    if scan is None or scan.get_scan_type() != ScanType.SKY or scan.spr is None:
        return

    tgt_idx = scan.scan_model.tgt_idx
    freq_scan_idx = scan.scan_model.freq_scan
    sky_entry = sky_arrays.get(tgt_idx, [None])[freq_scan_idx]

    if sky_entry is None:
        return

    sky_entry["spr_sum"] += np.sum(scan.spr, axis=0)
    sky_entry["seconds"] += scan.get_loaded_seconds()

def init_aggregated_cal_scans(obs: Observation, cal_arrays: dict, pipeline_factory: "ProcessingPipelineFactory" = None, blacklist: list[str] | None = None, sky_q: Queue | None = None, cal_q: Queue | None = None) -> dict:
    """ Build synthetic calibration ``Scan`` objects that will later be loaded
        from aggregated calibration spectra and reused by the normal pipeline
        and display code.

        Parameters:
            obs:        Observation model used to source template scan metadata.
            cal_arrays: Calibration aggregation dictionary describing which
                        aggregated scans are required.
            pipeline_factory: Optional factory used to attach a processing pipeline
                              to each manufactured scan.
            blacklist:  Optional list of full scan IDs to exclude from template
                        selection.
            sky_q:      Optional SKY queue passed into the pipeline factory.
            cal_q:      Optional calibration queue passed into the pipeline factory.

        Returns:
            A nested dictionary of manufactured calibration ``Scan`` objects keyed
            by scan type and frequency scan index.
    """
    blacklist = set(blacklist or [])
    aggregated_cal_scans = {}
    template_by_key = {}

    for target_scan_set in obs.target_scans:
        for scan_model in target_scan_set.scans:
            if scan_model is None or scan_model.scan_id in blacklist or scan_model.scan_type == ScanType.SKY:
                continue

            scan_type = scan_model.scan_type
            freq_scan_idx = scan_model.freq_scan
            if scan_type in cal_arrays and freq_scan_idx in cal_arrays[scan_type]:
                template_by_key.setdefault((scan_type, freq_scan_idx), scan_model)

    for scan_type, freq_scan_entries in cal_arrays.items():
        aggregated_cal_scans[scan_type] = {}

        for freq_scan_idx in freq_scan_entries.keys():
            template = template_by_key.get((scan_type, freq_scan_idx))
            if template is None:
                continue

            aggregate_model = ScanModel(
                obs_id=obs.obs_id,
                tgt_idx=template.tgt_idx,
                freq_scan=freq_scan_idx,
                scan_iter=-1,
                scan_id=f"{obs.obs_id}-agg-{scan_type.name.lower()}-{freq_scan_idx}",
                dig_id=template.dig_id,
                scan_type=scan_type,
                status=ScanState.COMPLETE,
                center_freq=template.center_freq,
                start_freq=template.start_freq,
                end_freq=template.end_freq,
                sample_rate=template.sample_rate,
                gain=template.gain,
                channels=template.channels,
                duration=template.duration,
                created=template.created,
                read_start=template.read_start,
                read_end=template.read_end,
                files_directory=template.files_directory,
            )

            aggregate_scan = Scan(scan_model=aggregate_model)
            aggregate_scan.set_status(ScanState.EMPTY)
            if pipeline_factory is not None and sky_q is not None and cal_q is not None:
                aggregate_scan.set_pipeline(
                    pipeline_factory.create_pipeline(scan=aggregate_scan, sky_q=sky_q, cal_q=cal_q)
                )
            aggregated_cal_scans[scan_type][freq_scan_idx] = aggregate_scan

    return aggregated_cal_scans

def init_aggregated_sky_scans(obs: Observation, sky_arrays: dict, pipeline_factory: "ProcessingPipelineFactory" = None, blacklist: list[str] | None = None, sky_q: Queue | None = None, cal_q: Queue | None = None) -> dict:
    """Build synthetic SKY Scan objects keyed by target and frequency scan index."""

    blacklist = set(blacklist or [])
    aggregated_sky_scans = {}
    template_by_key = {}

    for tgt_idx, target_scan_set in enumerate(obs.target_scans):
        for scan_model in target_scan_set.scans:
            if scan_model is None or scan_model.scan_id in blacklist or scan_model.scan_type != ScanType.SKY:
                continue
            if sky_arrays.get(tgt_idx) is None or sky_arrays[tgt_idx][scan_model.freq_scan] is None:
                continue
            template_by_key.setdefault((tgt_idx, scan_model.freq_scan), scan_model)

    for tgt_idx, freq_scan_entries in sky_arrays.items():
        aggregated_sky_scans[tgt_idx] = {}
        for freq_scan_idx, sky_entry in enumerate(freq_scan_entries):
            if sky_entry is None:
                continue

            template = template_by_key.get((tgt_idx, freq_scan_idx))
            if template is None:
                continue

            aggregate_model = ScanModel(
                obs_id=obs.obs_id,
                tgt_idx=tgt_idx,
                freq_scan=freq_scan_idx,
                scan_iter=-1,
                scan_id=f"{obs.obs_id}-{tgt_idx}-{freq_scan_idx}-agg",
                dig_id=template.dig_id,
                scan_type=ScanType.SKY,
                status=ScanState.COMPLETE,
                center_freq=template.center_freq,
                start_freq=template.start_freq,
                end_freq=template.end_freq,
                sample_rate=template.sample_rate,
                gain=template.gain,
                channels=template.channels,
                duration=max(1, int(sky_entry.get("seconds", 0))),
                created=template.created,
                read_start=template.read_start,
                read_end=template.read_end,
                files_directory=template.files_directory,
            )

            aggregate_scan = Scan(scan_model=aggregate_model)
            aggregate_scan.set_status(ScanState.EMPTY)
            if pipeline_factory is not None and sky_q is not None and cal_q is not None:
                aggregate_scan.set_pipeline(
                    pipeline_factory.create_pipeline(scan=aggregate_scan, sky_q=sky_q, cal_q=cal_q)
                )
            aggregated_sky_scans[tgt_idx][freq_scan_idx] = aggregate_scan

    return aggregated_sky_scans

def update_aggregated_cal_scan(aggregate_scan: Scan, cal_entry: dict):
    """ Rebuild a synthetic calibration ``Scan`` from the accumulated
        calibration power and total contributing seconds.

        Parameters:
            aggregate_scan: Manufactured calibration ``Scan`` to update.
            cal_entry:      Calibration aggregation entry containing ``mpr_sum`` and ``seconds``.
    """
    if aggregate_scan is None or cal_entry is None:
        return

    count = cal_entry.get("count", 0)
    if count <= 0:
        return

    avg_mpr = cal_entry["mpr_sum"] / count
    aggregate_scan.scan_model.duration = 1
    aggregate_scan.init_data_arrays()
    aggregate_scan.loaded_secs = aggregate_scan.scan_model.duration * [False]
    aggregate_scan.set_status(ScanState.EMPTY)
    for sec in range(1, aggregate_scan.scan_model.duration + 1):
        aggregate_scan.load_spr(sec=sec, spr=avg_mpr, read_start=aggregate_scan.scan_model.read_start, read_end=aggregate_scan.scan_model.read_end)

def update_aggregated_sky_scan(aggregate_scan: Scan, sky_entry: dict):
    """Refresh a manufactured SKY scan from a sky_arrays entry."""

    if aggregate_scan is None or sky_entry is None:
        return

    seconds = sky_entry.get("seconds", 0)
    if seconds <= 0:
        return

    avg_spr = sky_entry["spr_sum"] / seconds
    aggregate_scan.scan_model.duration = int(seconds)
    aggregate_scan.init_data_arrays()
    aggregate_scan.loaded_secs = aggregate_scan.scan_model.duration * [False]
    aggregate_scan.set_status(ScanState.EMPTY)
    for sec in range(1, aggregate_scan.scan_model.duration + 1):
        aggregate_scan.load_spr(sec=sec, spr=avg_spr, read_start=aggregate_scan.scan_model.read_start, read_end=aggregate_scan.scan_model.read_end)

def print_cal_arrays_shape(cal_arrays: dict):
    """Print a compact summary of ``cal_arrays``.

    Purpose:
        Show the current calibration aggregation structure and accumulation
        progress without dumping the full arrays.

    Parameters:
        cal_arrays: Calibration aggregation dictionary to summarise.

    Returns:
        ``None``.
    """

    _print_title("Calibration Array Shape")

    if not cal_arrays:
        print("No calibration arrays initialised.")
        print("")
        return

    for scan_type, freq_scan_entries in cal_arrays.items():
        print(f"{scan_type.name}: {len(freq_scan_entries)} freq_scans")
        for freq_scan_idx, entry in freq_scan_entries.items():
            mpr_sum = entry.get("mpr_sum")
            count = entry.get("count")
            shape = mpr_sum.shape if mpr_sum is not None else None
            print(f"  freq_scan[{freq_scan_idx}]: mpr_sum shape={shape}, count={count}")
    print("")

def init_signal_displays(obs: Observation, blacklist: list[str] | None = None) -> dict:
    """ Create one ``SignalDisplay`` per digitiser used by the observation.

        Parameters:
            obs: Observation model containing the scan metadata.
            blacklist: Optional blacklist parameter reserved for future filtering.

        Returns:
            A dictionary keyed by digitiser ID containing ``SignalDisplay``
            instances.
    """
    signal_displays = {}
    
    for tgt_idx, target_scans in enumerate(obs.target_scans):
    
        # Determine freq_scans and scan_iterations
        freq_scans = getattr(target_scans, 'freq_scans', 0) or 0
    
        for freq_scan_idx in range(freq_scans):

            scan_model = target_scans.get_scan_by_index(freq_scan_idx, 0)  # Just need to check the first scan iteration for each freq_scan to get the dig_id
            if scan_model is not None:
                dig_id = scan_model.dig_id
                if dig_id not in signal_displays:
                    signal_displays[dig_id] = SignalDisplay(dig_id=dig_id)

    return signal_displays

def init_pipeline_factory(input_dir: str) -> ProcessingPipelineFactory:
    """ Load ``PipelineConfig.json`` and construct the OPT processing pipeline
        factory.

        Parameters:
            input_dir: Directory containing the pipeline configuration file.

        Returns:
            A ``ProcessingPipelineFactory`` configured from disk or defaults.
    """
    # Load processing pipeline factory configuration from disk   
    filename = "PipelineConfig.json"

    try:
        pipeline_config = PipelineConfig().load_from_disk(input_dir=input_dir, filename=filename)
    except FileNotFoundError:
        pipeline_config = None
        logger.warning(f"OPT could not load processing pipeline factory configuration from directory {input_dir} file {filename}. File not found.")
    
    if pipeline_config is not None:
        logger.info(f"OPT loaded processing pipeline factory configuration from directory {input_dir} file {filename}:\n{pipeline_config}")
    else:
        pipeline_config = PipelineConfig()
        logger.info(f"OPT using default processing pipeline factory configuration as pipelineconfig file not found in directory {input_dir} file {filename}:\n{pipeline_config}")

    return ProcessingPipelineFactory(pipeline_config=pipeline_config)

def process_scan_data(obs: Observation, sky_arrays: dict, signal_displays: dict, pipeline_factory: "ProcessingPipelineFactory" = None, cal_arrays: dict | None = None, aggregated_cal_scans: dict | None = None, aggregated_sky_scans: dict | None = None, blacklist: list[str] | None = None):
    """ Replay the observation scans from disk, apply calibration and QA
        processing, update SKY and calibration aggregations, and refresh signal
        displays.

        Parameters:
            obs: Observation model defining the scans to replay.
            sky_arrays: SKY aggregation arrays keyed by target and frequency scan.
            signal_displays: Digitiser signal displays used for interactive
                visualisation.
            pipeline_factory: Optional factory used to build a processing pipeline
                for each replayed scan.
            cal_arrays: Optional calibration aggregation dictionary.
            aggregated_cal_scans: Optional manufactured aggregated calibration
                scans to keep updated during replay.
            blacklist: Optional list of full scan IDs to exclude from processing.
    """
    global sky_q, cal_q
    blacklist = set(blacklist or [])

    for tgt_idx, target_scan_set in enumerate(obs.target_scans):
  
        target = obs.get_target_by_index(tgt_idx)
        target_config = obs.get_target_config_by_index(tgt_idx)
        target_scan_set = obs.get_target_scan_set_by_index(tgt_idx)

        for scan_idx, scan_model in enumerate(target_scan_set.scans):
            if scan_model.scan_id in blacklist:
                logger.info(f"OPT skipping blacklisted scan {scan_model.scan_id}")
                continue

            freq_scan = scan_model.freq_scan
            scan_iter = scan_model.scan_iter

            dig_id = scan_model.dig_id
            files_prefix = scan_model.files_prefix
            files_directory = scan_model.files_directory

            if not files_prefix or not files_directory:
                logger.error(f"OPT skipping scan {scan_model.scan_id} with missing files_prefix or files_directory.")
                continue

            scan = Scan(scan_model=scan_model)

            # If the scan does not have an equivalent calibration scan in cal_q, then create an equivalent load calibration scan and insert it into cal_q
            cal_scan = None
            if scan_model.scan_type == ScanType.SKY:
                for cal in list(cal_q.queue):
                    if cal.scan_model.equivalent(scan_model) and cal.get_scan_type() == ScanType.LOAD and cal.get_status() == ScanState.COMPLETE and cal.scan_model.scan_id != scan.scan_model.scan_id:
                        cal_scan = cal
                        logger.info(f"OPT found equivalent calibration scan in cal_q for scan {scan_model.scan_id}:\n{cal_scan}")
                        break

                    logger.info(f"OPT no equivalent calibration scan in cal_q for scan {scan_model.scan_id} - checked cal_scan {cal.scan_model.scan_id} with status {cal.get_status().name} and type {cal.get_scan_type().name}")

                if cal_scan is None:

                    cal_scan_model = ScanModel(
                        dig_id=scan_model.dig_id,
                        scan_type=ScanType.LOAD,
                        read_start=scan_model.read_start,
                        center_freq=scan_model.center_freq,
                        start_freq=scan_model.start_freq,
                        end_freq=scan_model.end_freq,
                        sample_rate=scan_model.sample_rate,
                        gain=scan_model.gain,
                        channels=scan_model.channels,
                        duration=1,
                        files_prefix=files_prefix.replace("sky", "load"),
                        files_directory=files_directory,
                        status=ScanState.COMPLETE)
                    
                    cal_scan = Scan(scan_model=cal_scan_model)
                    cal_q.put(cal_scan)

            # Calibration scans are processed immediately. SKY scans are only aggregated here and processed later as synthetic scans.
            pipeline = pipeline_factory.create_pipeline(scan=scan, sky_q=sky_q, cal_q=cal_q) if pipeline_factory is not None and scan_model.scan_type != ScanType.SKY else None
            scan = scan.from_disk(file_prefix=files_prefix, input_dir=files_directory, include_iq=False, pipeline=pipeline)

            if scan is None or scan.spr is None:
                logger.error(f"OPT failed to load scan: {files_prefix} in {files_directory}")
                continue

            if scan.get_scan_type() == ScanType.SKY:
                if aggregated_sky_scans is None:
                    sky_q.put(scan)
            elif aggregated_cal_scans is None:
                cal_q.put(scan)

            if dig_id in signal_displays and scan.get_scan_type() != ScanType.SKY:
                signal_displays[dig_id].set_scan(scan=scan, load=cal_scan)
                signal_displays[dig_id].display()
                input("Press Enter to continue...")

            if scan.get_scan_type() == ScanType.SKY:
                update_sky_arrays(sky_arrays, scan)
            if cal_arrays is not None and scan.get_scan_type() != ScanType.SKY:
                update_cal_arrays(cal_arrays, scan)
                if aggregated_cal_scans is not None:
                    aggregate_scan = aggregated_cal_scans.get(scan.get_scan_type(), {}).get(freq_scan)
                    update_aggregated_cal_scan(aggregate_scan, cal_arrays[scan.get_scan_type()][freq_scan])
                    if aggregate_scan is not None and all(existing.scan_model.scan_id != aggregate_scan.scan_model.scan_id for existing in list(cal_q.queue)):
                        cal_q.put(aggregate_scan)

    if aggregated_sky_scans is not None:
        for tgt_idx, freq_scan_entries in aggregated_sky_scans.items():
            for freq_scan_idx, aggregate_scan in freq_scan_entries.items():
                sky_entry = sky_arrays[tgt_idx][freq_scan_idx]
                if aggregate_scan.pipeline is None and pipeline_factory is not None:
                    aggregate_scan.set_pipeline(
                        pipeline_factory.create_pipeline(scan=aggregate_scan, sky_q=sky_q, cal_q=cal_q)
                    )
                update_aggregated_sky_scan(aggregate_scan, sky_entry)
                if all(existing.scan_model.scan_id != aggregate_scan.scan_model.scan_id for existing in list(sky_q.queue)):
                    sky_q.put(aggregate_scan)

                if aggregate_scan.get_dig_id() in signal_displays:
                    matching_loads = [
                        cal for cal in list(cal_q.queue)
                        if cal.get_scan_type() == ScanType.LOAD and cal.scan_model.equivalent(aggregate_scan.scan_model)
                    ]
                    load_scan = max(matching_loads, key=lambda s: s.scan_model.created) if matching_loads else None
                    if load_scan is None:
                        fallback_load_model = ScanModel(
                            dig_id=aggregate_scan.scan_model.dig_id,
                            scan_type=ScanType.LOAD,
                            read_start=aggregate_scan.scan_model.read_start,
                            center_freq=aggregate_scan.scan_model.center_freq,
                            start_freq=aggregate_scan.scan_model.start_freq,
                            end_freq=aggregate_scan.scan_model.end_freq,
                            sample_rate=aggregate_scan.scan_model.sample_rate,
                            gain=aggregate_scan.scan_model.gain,
                            channels=aggregate_scan.scan_model.channels,
                            duration=1,
                            status=ScanState.COMPLETE,
                        )
                        load_scan = Scan(scan_model=fallback_load_model)
                    signal_displays[aggregate_scan.get_dig_id()].set_scan(scan=aggregate_scan, load=load_scan)
                    signal_displays[aggregate_scan.get_dig_id()].display()
                    input("Press Enter to continue...")

def main():
    """ Parse CLI arguments, load observation metadata, let the user manage the
        blacklist, initialise processing state, and replay scans through the
        current OPT workflow.    
    """
    global sky_q, cal_q

    sky_q = Queue()
    cal_q = Queue()

    args = init_args() # Initialize command line arguments

    obs = load_obs_metadata(args.dir, args.obs) # Load observation metadata from disk
    if obs is None:
        logger.error(f"OPT: Failed to load observation metadata for Observation {args.obs} in {args.dir}. Exiting.")
        return

    log_path = os.path.expanduser(os.path.join(args.dir, f"{args.obs}-opt.log"))
    print(f"OPT logs written to {log_path}")

    with redirect_root_logging(log_path):
        
        blacklist = []
        blacklist_msgs = ""
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            print_obs_header(obs)
            print_cal_scans(obs=obs, blacklist=blacklist)
            print_sky_scans(obs=obs, blacklist=blacklist)
            if blacklist_msgs:
                print(blacklist_msgs)
                print("")

            updated_blacklist, blacklist_msgs = blacklist_scans(obs, blacklist)
            if updated_blacklist == blacklist:
                break
            blacklist = updated_blacklist

        signal_displays = init_signal_displays(obs)
        pipeline_factory = init_pipeline_factory(input_dir='config/' + args.profile)
        sky_arrays = init_sky_arrays(obs, blacklist)
        cal_arrays = init_cal_arrays(obs, blacklist)

        aggregated_cal_scans = init_aggregated_cal_scans(
            obs,
            cal_arrays,
            pipeline_factory=pipeline_factory,
            blacklist=blacklist,
            sky_q=sky_q,
            cal_q=cal_q,
        )
        aggregated_sky_scans = init_aggregated_sky_scans(
            obs,
            sky_arrays,
            pipeline_factory=None,
            blacklist=blacklist,
            sky_q=sky_q,
            cal_q=cal_q,
        )
        
        process_scan_data(
            obs,
            sky_arrays,
            signal_displays,
            pipeline_factory=pipeline_factory,
            cal_arrays=cal_arrays,
            aggregated_cal_scans=aggregated_cal_scans,
            aggregated_sky_scans=aggregated_sky_scans,
            blacklist=blacklist,
        )
        print_cal_arrays_shape(cal_arrays)
        print_sky_scans(obs=obs, sky_q=sky_q, blacklist=blacklist)

        aggregated_sky_scan_list = []
        for freq_scan_entries in aggregated_sky_scans.values():
            aggregated_sky_scan_list.extend(freq_scan_entries.values())

        obs_display = ObsDisplay(obs_id=obs.obs_id)
        obs_display.set_scan(aggregated_sky_scan_list, obs=obs)
        obs_display.display()

    input("Press Enter to continue...")

if __name__ == "__main__":
    main()
