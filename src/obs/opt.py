import argparse
from contextlib import contextmanager
import os
import logging
from queue import Queue

from models.obs import ObsModel
from models.scan import ScanType
try:
    from obs.obs import Observation
    from obs.obs_display import ObsDisplay
except ModuleNotFoundError:
    from obs import Observation
    from obs_display import ObsDisplay
from util.format import fmt_cell, fmt_float, fmt_hyperlink, fmt_target_coords, fmt_title

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
                target_config.feed.name if target_config is not None and target_config.feed is not None else "",
                scan_model.dig_id,
                fmt_float(scan_model.center_freq, scale=1e6, precision=2, suffix=" MHz"),
                fmt_float(scan_model.sample_rate, scale=1e6, precision=2, suffix=" MHz"),
                fmt_float(scan_model.gain, precision=1, suffix=" dB"),
                str(scan_model.channels),
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
        ("Channels", 8),
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
                str(scan_model.channels),
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
        ("Channels", 8),
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
            target_config.feed.name if target_config is not None and target_config.feed is not None else "",
            scan_model.dig_id,
            fmt_float(scan_model.center_freq, scale=1e6, precision=2, suffix=" MHz"),
            fmt_float(scan_model.sample_rate, scale=1e6, precision=2, suffix=" MHz"),
            fmt_float(scan_model.gain, precision=1, suffix=" dB"),
            str(scan_model.channels),
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

        obs_display = ObsDisplay(obs_id=obs.obs_model.obs_id)
        obs_display.set_scan(
            [scan for scan in list(sky_q.queue) if scan.get_scan_type() == ScanType.SKY],
            obs=obs.obs_model,
        )
        obs_display.display()

        print_sky_scans(obs=obs.obs_model, sky_q=sky_q, blacklist=blacklist)
        print_aggregated_scans(obs=obs.obs_model, sky_q=sky_q, int_arrays=collapsed_sky_arrays, blacklist=blacklist)

    input("Press Enter to continue...")

if __name__ == "__main__":
    main()
