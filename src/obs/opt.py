import argparse
from contextlib import contextmanager
import os
import json
import numpy as np
import logging
from queue import Queue

from models.obs import ObsModel
from models.pipeline import PipelineConfig
from models.scan import ScanModel, ScanType, ScanState
try:
    from obs.obs_display import ObsDisplay
    from obs.scan import Scan
except ModuleNotFoundError:
    from obs_display import ObsDisplay
    from scan import Scan
from sdp.pipeline.pipeline_factory import ProcessingPipelineFactory
from sdp.signal_display import SignalDisplay
from util.xbase import XSoftwareFailure

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

def load_obs_metadata(dir: str, obs_id: str) -> ObsModel | None:
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
    obs = ObsModel().from_dict(obs_data)
   
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

def print_obs_header(obs: ObsModel):
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
    _print_title("Aggregated SKY Scans")

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
        ("SNR (dB)", 10),
        ("FWHM", 10),
        ("DR (dB)", 10),
    ]

    header = " ".join(_fmt_cell(name, width) for name, width in columns)
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

        coords = _format_target_coordinates(target)

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
            target_config.feed.name if target_config is not None and target_config.feed is not None else "",
            scan_model.dig_id,
            _fmt_float(scan_model.center_freq, scale=1e6, precision=2, suffix=" MHz"),
            _fmt_float(scan_model.sample_rate, scale=1e6, precision=2, suffix=" MHz"),
            _fmt_float(scan_model.gain, precision=1, suffix=" dB"),
            str(scan_model.channels),
            _fmt_float(integrated_secs if integrated_secs is not None else scan_model.duration, precision=0, suffix=" s"),
            _scan_image_link(scan_model, 8),
            _fmt_float(snr_db, precision=2),
            _fmt_float(fwhm, precision=2),
            _fmt_float(dr_db, precision=2),
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


def print_pipeline_config(obs: ObsModel, pipeline_factory: ProcessingPipelineFactory):
    """ Show the configured processing steps that OPT will use as a fixed-width table.

        Parameters:
            obs: Observation model used to derive the digitiser ID whose pipeline
                 configuration should be displayed.
            pipeline_factory: Factory containing the pipeline configuration to display.
    """
    _print_title("Processing Pipeline Configuration")

    context_columns = [
        ("Context", 8),
        ("Name", 24),
        ("Input", 30),
        ("Usage", 90),
        ("Output", 6),
    ]
    context_rows = [
        ("spr", "Summed Power Spectrum", "Raw power summed over seconds", "Apply minimal pipeline steps to preserve the original spectrum for later processing.", "spr⁎"),
        ("cal", "Calibrated Spectrum", "spr⁎", "Apply calibration steps such as bandpass correction, gain calibration, and rfi exclusion.", "cal⁎"),
        ("mpr", "Mean Power Spectrum", "cal⁎", "Apply QA steps such as SNR calculation, FWHM estimation, and dynamic range calculation.", "mpr⁎"),
    ]
    context_header = " ".join(_fmt_cell(name, width) for name, width in context_columns)
    context_divider = " ".join("-" * width for _, width in context_columns)
    print(context_header)
    print(context_divider)
    for row in context_rows:
        print(" ".join(_fmt_cell(value, width) for value, (_, width) in zip(row, context_columns)))
    print("")
    
    columns = [
        ("Dig ID", 8),
        ("Context", 8),
        ("Step Name", 12),
        ("Step Description", 96),
    ]

    header = " ".join(_fmt_cell(name, width) for name, width in columns)
    divider = " ".join("-" * width for _, width in columns)
    print(header)
    print(divider)

    dig_id = None
    for target_scan_set in obs.target_scans:
        for scan_model in target_scan_set.scans:
            if scan_model is not None and getattr(scan_model, "dig_id", None):
                dig_id = scan_model.dig_id
                break
        if dig_id is not None:
            break

    for item in pipeline_factory.describe_steps_for_dig(dig_id):
        row = [
            dig_id or "default",
            item["params"].get("pipeline", ""),
            item["step"].name,
            item["description"],
        ]
        print(" ".join(_fmt_cell(value, width) for value, (_, width) in zip(row, columns)))

    print("")
    print("Processing Pipeline Steps are configurable via the PipelineConfig.json file located in the ./config/<profile> directory.")
    print("")

def init_integrated_arrays(obs: ObsModel, blacklist: list[str] | None = None) -> dict:
    """ Create a dictionary of integrated arrays to aggregate scan interations for each tgt_idx and freq_scan combination.
        Excludes blacklisted scans. 
        
        Aggregates: summed power (spr), mean power (mpr), total power (tpw), seconds, count of scan iterations

        Parameters:
            obs:        Observation model providing calibration scan metadata.
            blacklist:  Optional list of full scan IDs to exclude.

        Returns:
            A dictionary: {tgt_idx-freq_scan_idx: {"spr_sum": np.array, "mpr_sum": np.array, "tpw_sum": [], "secs": float, "scans": int}}
    """
    blacklist = set(blacklist or [])
    int_arrays = {}             # Structure: {(tgt_idx,freq_scan_idx): {"spr_sum": np.array, "mpr_sum": np.array, "tpw_sum": [], "secs": float, "scans": int}}
    scanmodel_templates = {}    # Structure: {(tgt_idx,freq_scan_idx): ScanModel} used to find a template for each aggregated scan based on the freq_scan

    for target_scan_set in obs.target_scans:
        for scan_model in target_scan_set.scans:
            if scan_model is None or scan_model.scan_id in blacklist:
                continue

            tgt_idx = scan_model.tgt_idx
            freq_scan_idx = scan_model.freq_scan

            scanmodel_templates.setdefault((tgt_idx, freq_scan_idx), scan_model)

    for (tgt_idx, freq_scan_idx), scanmodel in scanmodel_templates.items():
        int_arrays.setdefault((tgt_idx, freq_scan_idx), {})
        int_arrays[(tgt_idx, freq_scan_idx)] = {
            "spr_sum": np.zeros(scanmodel.channels),    # Known to be sized by channel count
            "mpr_sum": np.zeros(scanmodel.channels),    # Known to be sized by channel count
            "tpw_sum": [],                              # A list total power readings that can grow incrementally
            "secs": 0.0,                                # Total seconds accumulated across scans 
            "scans": 0,                                 # Count of scan iterations accumulated
        }

    return int_arrays

def update_int_arrays(int_arrays: dict, scan: Scan):
    """ Add a processed scan's summed power spectrum to the relevant integration entry.
        
        Parameters:
            int_arrays: Integration aggregation dictionary created by ``init_int_arrays``.
            scan:       Processed integration ``Scan`` whose ``spr`` should be accumulated.
    """
    if scan is None or scan.spr is None or scan.mpr is None:
        return

    tgt_idx = scan.scan_model.tgt_idx
    freq_scan_idx = scan.scan_model.freq_scan

    if (tgt_idx, freq_scan_idx) not in int_arrays:
        return

    int_arrays[(tgt_idx, freq_scan_idx)]["spr_sum"] += np.sum(scan.spr, axis=0)  # Note: summing multiple rows (seconds) to get a single spectrum per scan iteration
    int_arrays[(tgt_idx, freq_scan_idx)]["mpr_sum"] += np.sum(scan.mpr, axis=0)  # Note: summing a single integrated row per scan iteration
    int_arrays[(tgt_idx, freq_scan_idx)]["tpw_sum"].extend(np.sum(scan.cal, axis=1).tolist())  # Extending the list by summing across channels to get total power per second

    int_arrays[(tgt_idx, freq_scan_idx)]["secs"] += scan.get_loaded_seconds()
    int_arrays[(tgt_idx, freq_scan_idx)]["scans"] += 1

def print_int_arrays_shape(int_arrays: dict):
    """ Show the current integrated array structure and accumulation progress without dumping the full arrays.

        Parameters:
            int_arrays: Integrated array dictionary to summarise.
    """
    _print_title("Integrated Array Shape")

    if not int_arrays:
        print("No integrated arrays initialised.")
        print("")
        return

    for (tgt_idx, freq_scan_idx), entry in sorted(int_arrays.items()):
        spr_sum = entry.get("spr_sum")
        mpr_sum = entry.get("mpr_sum")
        tpw_sum = entry.get("tpw_sum")
        secs = entry.get("secs")
        scans = entry.get("scans")
        spr_shape = spr_sum.shape if spr_sum is not None else None
        mpr_shape = mpr_sum.shape if mpr_sum is not None else None
        tpw_shape = len(tpw_sum) if tpw_sum is not None else None
        print(
            f"(tgt_idx={tgt_idx}, freq_scan={freq_scan_idx}): "
            f"spr_sum shape={spr_shape}, mpr_sum shape={mpr_shape}, tpw_sum shape={tpw_shape}, secs={secs}, scans={scans}"
        )
    print("")

def integrate_cal_scans(dir: str, obs: ObsModel, int_arrays: dict, pipeline_factory: "ProcessingPipelineFactory" = None, blacklist: list[str] | None = None, sky_q: Queue | None = None, cal_q: Queue | None = None, signal_displays: list[SignalDisplay] | None = None):
    """ Iterate over calibration scan iterations for each target and aggregate their summed power spectra into integrated arrays, 
        then build synthetic calibration ``Scan`` objects that will used by sky scans later. 

        Parameters:
            dir:        Directory containing the scan files.
            obs:        Observation model used to source template scan metadata.
            int_arrays: Integrated aggregation dictionary describing which
                        aggregated scans are required.
            pipeline_factory: Optional factory used to attach a processing pipeline
                              to each manufactured scan.
            blacklist:  Optional list of full scan IDs to exclude from template
                        selection.
            sky_q:      Optional SKY queue passed into the pipeline factory.
            cal_q:      Optional calibration queue passed into the pipeline factory.
            signal_displays: Optional list of signal displays for visualizing the integrated scans.
    """

    if obs is None or cal_q is None:
        logger.error("Observation and calibration queue are required to initialize integrated calibration scans.")
        raise XSoftwareFailure("Missing required parameters for initializing integrated calibration scans.")

    dir = os.path.expanduser(dir) if dir is not None else dir
    blacklist = set(blacklist or [])

    # Load non-blacklisted calibration scans from disk and accumulate their spectra into the integrated arrays based on their tgt_idx and freq_scan.
    # Calibration scans are processed by the pipeline before integration to give the pipeline a chance to apply necessary corrections 
    for target_scan_set in obs.target_scans:
        for scan_model in target_scan_set.scans:
            if scan_model is None or scan_model.scan_id in blacklist or scan_model.scan_type == ScanType.SKY:
                continue

            files_prefix = scan_model.files_prefix

            if not files_prefix:
                logger.error(f"OPT skipping scan {scan_model.scan_id} with missing files_prefix while initializing integrated calibration scans.")
                continue

            scan = Scan(scan_model=scan_model)

            print(f"Loading scan {scan_model.scan_id} from disk for integration...{files_prefix} in {dir}")

            # Calibration scans are loaded and pushed through the processing pipeline immediately.
            pipeline = pipeline_factory.create_pipeline(scan=scan, sky_q=sky_q, cal_q=cal_q) if pipeline_factory is not None and scan_model.scan_type != ScanType.SKY else None
            scan = scan.from_disk(file_prefix=files_prefix, input_dir=dir, include_iq=False, pipeline=pipeline)

            if scan is None or scan.mpr is None:
                logger.error(f"OPT failed to load scan: {files_prefix} in {dir} while initializing integrated calibration scans.")
                continue

            update_int_arrays(int_arrays=int_arrays, scan=scan)
            scan.__del__()  # Explicitly release memory as quickly as possible 

    # Now build a synthetic calibration scan for each tgt_idx and freq_scan combination to be available to SKY scan processing
    for (tgt_idx, freq_scan_idx), entry in sorted(int_arrays.items()):
        spr_sum = entry.get("spr_sum")
        mpr_sum = entry.get("mpr_sum")
        secs = entry.get("secs")
        scans = entry.get("scans")

        if spr_sum is None or mpr_sum is None or secs == 0 or scans == 0:
            logger.warning(f"Skipping integrated calibration scan for (tgt_idx={tgt_idx}, freq_scan={freq_scan_idx}) due to missing data or zero seconds/scans.")
            continue

        scanmodel_template = obs.get_target_scan_by_index(tgt_idx, freq_scan_idx, 0)

        if scanmodel_template is None:
            logger.error(f"Failed to find a template scan model for (tgt_idx={tgt_idx}, freq_scan={freq_scan_idx}) while initializing integrated calibration scans.")
            continue

        synthetic_scan_model = ScanModel(
            obs_id=obs.obs_id,
            tgt_idx=tgt_idx,
            freq_scan=freq_scan_idx,
            scan_iter=-1,
            scan_id=f"{obs.obs_id}-int-{tgt_idx}-{freq_scan_idx}",
            dig_id=scanmodel_template.dig_id,
            scan_type=scanmodel_template.scan_type,
            status=ScanState.COMPLETE,
            channels=scanmodel_template.channels,
            center_freq=scanmodel_template.center_freq,
            sample_rate=scanmodel_template.sample_rate,
            gain=scanmodel_template.gain,
            duration=1,
        )

        synthetic_scan = Scan(scan_model=synthetic_scan_model)
        synthetic_scan.load_spr(sec=1, spr=spr_sum / secs)  # Load the summed power spectrum directly into the synthetic scan

        cal_q.put(synthetic_scan)
        
def integrate_sky_scans(dir: str, obs: ObsModel, int_arrays: dict, pipeline_factory: "ProcessingPipelineFactory" = None, blacklist: list[str] | None = None, sky_q: Queue | None = None, cal_q: Queue | None = None, signal_displays: list[SignalDisplay] | None = None):
    """ Build synthetic sky ``Scan`` objects that will later be loaded
        from aggregated calibration spectra and reused by the normal pipeline
        and display code.

        Parameters:
            dir:        Directory containing the scan files.
            obs:        Observation model used to source template scan metadata.
            int_arrays: Integrated aggregation dictionary describing which
                        aggregated scans are required.
            pipeline_factory: Optional factory used to attach a processing pipeline
                              to each manufactured scan.
            blacklist:  Optional list of full scan IDs to exclude from template
                        selection.
            sky_q:      Optional SKY queue passed into the pipeline factory.
            cal_q:      Optional calibration queue passed into the pipeline factory.
            signal_displays: Optional list of signal displays for visualizing the integrated scans.
    """

    if obs is None or sky_q is None or cal_q is None:
        logger.error("Observation, sky and calibration queues are required to initialize integrated sky scans.")
        raise XSoftwareFailure("Missing required parameters for initializing integrated sky scans.")


    print("Length of cal_q is {}".format(cal_q.qsize()))

    dir = os.path.expanduser(dir) if dir is not None else dir
    blacklist = set(blacklist or [])

    for target_scan_set in obs.target_scans:
        for scan_model in target_scan_set.scans:
            if scan_model is None or scan_model.scan_id in blacklist or scan_model.scan_type != ScanType.SKY:
                continue

            dig_id = scan_model.dig_id
            files_prefix = scan_model.files_prefix

            if not files_prefix:
                logger.error(f"OPT skipping scan {scan_model.scan_id} with missing files_prefix while initializing integrated sky scans.")
                continue

            scan = Scan(scan_model=scan_model)

            print(f"Loading scan {scan_model.scan_id} from disk for integration...{files_prefix} in {dir}")

             # If the scan does not have an equivalent calibration scan in cal_q, then create an equivalent load calibration scan and insert it into cal_q
            cal_scan = None            
            for cal in list(cal_q.queue):
                if cal.scan_model.equivalent(scan_model) and cal.get_scan_type() == ScanType.LOAD and cal.get_status() == ScanState.COMPLETE and cal.scan_model.scan_id != scan.scan_model.scan_id:
                    cal_scan = cal
                    logger.info(f"OPT found equivalent calibration scan in cal_q for scan {scan_model.scan_id}:\n{cal_scan}")
                    break

                logger.info(f"OPT no equivalent calibration scan in cal_q for scan {scan_model.scan_id} - checked cal_scan {cal.scan_model.scan_id} with status {cal.get_status().name} and type {cal.get_scan_type().name}")

            # Sky scans are loaded and pushed through the processing pipeline.
            pipeline = pipeline_factory.create_pipeline(scan=scan, sky_q=sky_q, cal_q=cal_q) if pipeline_factory is not None else None
            scan = scan.from_disk(file_prefix=files_prefix, input_dir=dir, include_iq=False, pipeline=pipeline)

            if scan is None or scan.spr is None or scan.mpr is None:
                logger.error(f"OPT failed to load scan: {files_prefix} in {dir} while initializing integrated sky scans.")
                continue

            if dig_id in signal_displays and scan.get_scan_type() == ScanType.SKY:

                signal_displays[dig_id].set_scan(scan=scan, load=cal_scan)
                signal_displays[dig_id].display()

            update_int_arrays(int_arrays=int_arrays, scan=scan)
            scan.__del__()  # Explicitly release memory used by the loaded scan

    # Now build synthetic SKY scans from the integrated arrays for later processing and display.
    for (tgt_idx, freq_scan_idx), entry in sorted(int_arrays.items()):

        scanmodel_template = obs.get_target_scan_by_index(tgt_idx, freq_scan_idx, 0)
        if scanmodel_template is None or scanmodel_template.scan_type != ScanType.SKY:
            continue

        spr_sum = entry.get("spr_sum")
        mpr_sum = entry.get("mpr_sum")
        secs = entry.get("secs")
        scans = entry.get("scans")

        if spr_sum is None or mpr_sum is None or secs == 0 or scans == 0:
            logger.warning(f"Skipping integrated sky scan for (tgt_idx={tgt_idx}, freq_scan={freq_scan_idx}) due to missing data or zero seconds/scans.")
            continue

        if scanmodel_template is None:
            logger.error(f"Failed to find a template scan model for (tgt_idx={tgt_idx}, freq_scan={freq_scan_idx}) while initializing integrated sky scans.")
            continue

        dig_id=scanmodel_template.dig_id

        synthetic_scan_model = ScanModel(
            obs_id=obs.obs_id,
            tgt_idx=tgt_idx,
            freq_scan=freq_scan_idx,
            scan_iter=-1,
            scan_id=f"{obs.obs_id}-int-{tgt_idx}-{freq_scan_idx}",
            dig_id=scanmodel_template.dig_id,
            scan_type=scanmodel_template.scan_type,
            status=ScanState.COMPLETE,
            channels=scanmodel_template.channels,
            center_freq=scanmodel_template.center_freq,
            sample_rate=scanmodel_template.sample_rate,
            gain=scanmodel_template.gain,
            duration=1,
        )

        synthetic_scan = Scan(scan_model=synthetic_scan_model)
        if synthetic_scan.pipeline is None and pipeline_factory is not None:
            synthetic_scan.set_pipeline(
                pipeline_factory.create_pipeline(scan=synthetic_scan, sky_q=sky_q, cal_q=cal_q)
            )
        synthetic_scan.load_spr(sec=1, spr=spr_sum / secs)  # Load the summed power spectrum directly into the synthetic scan
        synthetic_scan.save_to_disk(output_dir=dir, include_iq=False)
        sky_q.put(synthetic_scan)

        if dig_id in signal_displays:

            signal_displays[dig_id].set_scan(scan=synthetic_scan, load=None)
            signal_displays[dig_id].display()
            signal_displays[dig_id].save_scan_figure(output_dir=dir)

def init_signal_displays(obs: ObsModel, blacklist: list[str] | None = None) -> dict:
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
    """ Load ``PipelineConfig.json`` and construct the OPT processing pipeline factory.

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

    with redirect_root_logging(log_path):
        
        blacklist = []
        blacklist_msgs = ""
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            print(f"OPT logs written to {log_path}")
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
        print_pipeline_config(obs, pipeline_factory)

        # Initialise cal and sky dictionaries containing the accumulated calibration (mpr) and sky power (spr) for each frequency scan 
        int_arrays = init_integrated_arrays(obs, blacklist)

        integrate_cal_scans(
            dir=args.dir,
            obs=obs,
            int_arrays=int_arrays, 
            pipeline_factory=pipeline_factory, 
            blacklist=blacklist, 
            sky_q=sky_q, 
            cal_q=cal_q,
            signal_displays=signal_displays)

        integrate_sky_scans(
            dir=args.dir,
            obs=obs,
            int_arrays=int_arrays, 
            pipeline_factory=pipeline_factory, 
            blacklist=blacklist, 
            sky_q=sky_q, 
            cal_q=cal_q,
            signal_displays=signal_displays)

        print_int_arrays_shape(int_arrays)

        obs_display = ObsDisplay(obs_id=obs.obs_id)
        obs_display.set_scan(
            [scan for scan in list(sky_q.queue) if scan.get_scan_type() == ScanType.SKY],
            obs=obs,
            int_arrays=int_arrays,
        )
        obs_display.display()

        print_sky_scans(obs=obs, sky_q=sky_q, blacklist=blacklist)
        print_aggregated_scans(obs=obs, sky_q=sky_q, int_arrays=int_arrays, blacklist=blacklist)

    input("Press Enter to continue...")

if __name__ == "__main__":
    main()
