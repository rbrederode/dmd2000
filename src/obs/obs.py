import numpy as np
import threading
import logging
import time
import json
import os
from datetime import datetime, timezone
from queue import Queue

from models.obs import ObsModel
from models.scan import ScanModel, ScanType, ScanState
from obs.scan import Scan
from sdp.pipeline.pipeline_factory import ProcessingPipelineFactory, PipelineConfig
from sdp.signal_display import SignalDisplay
from util.format import fmt_cell, fmt_float, fmt_target_coords, fmt_hyperlink, fmt_image_link
from util import gen_file_prefix
from util.xbase import XSoftwareFailure 

logger = logging.getLogger(__name__)

class Observation:
    """ Observation class that manages the state and data of an observation.
        An observation integrates individual scans of a given target and frequency setup over the observation duration.
    """

    def __init__(self, obs_model: ObsModel, blacklist: list[str] = None, pipeline_factory: "ProcessingPipelineFactory" = None):
        """ Initialize an observation with the observation model.

            Parameters
                obs_model:        Observation metadata model with targets, configs and scans
                blacklist:        List of scan IDs to exclude from processing
                pipeline_factory: Factory for creating the processing pipeline for calibrating scan data
        """
        self._rlock = threading.RLock()  # Lock for thread-safe access to shared resources

        with self._rlock:

            self.obs_model = obs_model                  # Meta data model for the observation
            self.pipeline_factory = pipeline_factory    # Processing pipeline to calibrate scan data
            self.blacklist = blacklist if blacklist is not None else set()  # Set of scan IDs to exclude from processing

            # Initialize integrated data arrays for the observation
            self.int_data_arrays = self.init_integrated_data_arrays() if obs_model is not None else {}

    def __str__(self):

        if self.obs_model is None:
            return "Observation: No observation model available."

        return (
            f"Observation Id: {self.obs_model.obs_id}\n"
            f"Dish Id:        {self.obs_model.dsh_id}\n"
            f"User:           {self.obs_model.user_email}\n"
            f"Start Time:     {self.obs_model.start_dt.isoformat()}\n"
            f"End Time:       {self.obs_model.end_dt.isoformat()}\n"
            f"State:          {self.obs_model.obs_state.name}\n"
        )

    def __eq__(self, other):
        if not isinstance(other, Observation):
            return False

        return self.obs_model.obs_id == other.obs_model.obs_id

    def __del__(self):
        """ Destructor to clean up resources when an Observation instance is deleted. """
        logger.info(f"Observation {self.obs_model.obs_id} - Deleting observation instance and cleaning up resources.")

    def set_pipeline_factory(self, pipeline_factory: "ProcessingPipelineFactory"):
        """ Set the processing pipeline factory to be used for calibrating scan data.

            Parameters:
                pipeline_factory: An instance of ProcessingPipelineFactory that can create pipelines for scan processing.
        """
        with self._rlock:
            self.pipeline_factory = pipeline_factory
            logger.debug(f"Observation {self.obs_model.obs_id} - Set processing pipeline factory: {pipeline_factory.__class__.__name__}")

    def set_blacklist(self, scan_ids: list[str]):
        """ Set the blacklist of scan IDs to exclude from processing.

            Parameters:
                scan_ids: List of scan IDs to add to the blacklist.
        """
        with self._rlock:
            self.blacklist.update(scan_ids)
            logger.info(f"Observation {self.obs_model.obs_id} - Updated blacklist with scan IDs: {scan_ids}")

    def init_integrated_data_arrays(self) -> dict:
        """ Create a dictionary of integrated arrays to aggregate scan interations for each tgt_idx and freq_scan combination.
            Excludes blacklisted scans. 
            
            Aggregates: summed power (spr), mean power (mpr), total power (tpw), seconds, count of scan iterations

            Parameters:
                obs:        Observation model providing calibration scan metadata.
                blacklist:  Optional list of full scan IDs to exclude.

            Returns:
                A dictionary: {tgt_idx-freq_scan_idx: {"spr_sum": np.array, "mpr_sum": np.array, "tpw_sum": [], "secs": float, "scans": int}}
        """
        if self.obs_model is None:
            logger.error("Observation model is required to initialize integrated data arrays.")
            raise XSoftwareFailure("Missing observation model for initializing integrated data arrays.")

        int_data_arrays = {}             # Structure: {(tgt_idx,freq_scan, scan_iter): {"spr_sum": np.array, "mpr_sum": np.array, "tpw_sum": [], "secs": float, "scans": int}}

        for target_scan_set in self.obs_model.target_scans:
            for scan_model in target_scan_set.scans:
                if scan_model is None or (self.blacklist and scan_model.scan_id in self.blacklist):
                    continue

                tgt_idx = scan_model.tgt_idx
                freq_scan = scan_model.freq_scan
                scan_iter = scan_model.scan_iter
                
                # Summed Power (SPR) and Mean Power (MPR) arrays are initialized to zeros
                int_spr = np.zeros(scan_model.spectral_resolution, dtype=np.float64)
                int_mpr = np.zeros(scan_model.spectral_resolution, dtype=np.float64)

                # Initialise arrays to ones for GAIN and LOAD scans as the signal is divided by these during calibration
                if scan_model.scan_type in [ScanType.GAIN, ScanType.LOAD]:
                    int_spr = np.ones(scan_model.spectral_resolution, dtype=np.float64)
                    int_mpr = np.ones(scan_model.spectral_resolution, dtype=np.float64)

                int_data_arrays.setdefault((tgt_idx, freq_scan, scan_iter), {})
                int_data_arrays[(tgt_idx, freq_scan, scan_iter)] = {
                    "scan_type": scan_model.scan_type,    # Store the scan type for reference (e.g., "LOAD", "SKY", "TSYS", "GAIN" etc)
                    "int_spr":   int_spr,                 # Known to be sized by channel count
                    "int_mpr":   int_mpr,                 # Known to be sized by channel count
                    "int_tpw":   [],                      # A list of total power readings that can grow incrementally
                    "secs":      0.0,                     # Total seconds accumulated across scans 
                    "scans":     0,                       # Count of scan iterations accumulated
                }
    
        return int_data_arrays

    def _update_integrated_data_arrays(self, scan: "Scan"):
        """ Add a processed scan's summed power spectrum to the relevant integration entry.
            
            Parameters:
                scan:       Processed integration ``Scan`` whose ``spr`` should be accumulated.
        """
        if scan is None or scan.spr is None or scan.cal is None or scan.mpr is None:
            logger.warning(f"Observation could not integrate Scan {scan} without valid spr, cal and mpr datawhile updating integrated data arrays.")
            return

        tgt_idx = scan.scan_model.tgt_idx
        freq_scan = scan.scan_model.freq_scan
        scan_iter = scan.scan_model.scan_iter

        if (tgt_idx, freq_scan, scan_iter) not in self.int_data_arrays:
            logger.warning(f"Observation could not find entry for Scan {scan.scan_model.scan_id} in integrated data arrays, skipping integration for this scan.")
            return

        self.int_data_arrays[(tgt_idx, freq_scan, scan_iter)]["int_spr"] += np.sum(scan.spr, axis=0)           # Note: summing multiple rows (seconds) to get a single spectrum per scan iteration
        self.int_data_arrays[(tgt_idx, freq_scan, scan_iter)]["int_mpr"] += np.sum(scan.mpr, axis=0)           # Note: summing a single integrated row per scan iteration
        self.int_data_arrays[(tgt_idx, freq_scan, scan_iter)]["int_tpw"] += np.sum(scan.cal, axis=1).tolist()  # Extending the list by summing across channels to get total power per second

        self.int_data_arrays[(tgt_idx, freq_scan, scan_iter)]["secs"] += scan.get_loaded_duration()
        self.int_data_arrays[(tgt_idx, freq_scan, scan_iter)]["scans"] += 1

    def integrate_cal_scans(self, dir: str, sky_q: "Queue" = None, cal_q: "Queue" = None):
        """ Iterate over calibration scan iterations for each target and aggregate their summed power spectra into integrated data arrays.

            Parameters:
                dir:        Directory containing the scan files.
                sky_q:      Optional SKY queue passed into the pipeline factory.
                cal_q:      Optional calibration queue passed into the pipeline factory.
        """

        if self.obs_model is None:
            logger.error("Observation metadata is required to initialize integrated calibration scans.")
            raise XSoftwareFailure("Missing required parameters for initializing integrated calibration scans.")

        dir = os.getcwd() if dir is None else os.path.expanduser(dir)  # Default to current working directory if not provided

        # Load non-blacklisted calibration scans from disk and accumulate their spectra into the integrated data arrays based on their tgt_idx, freq_scan and scan_iter.
        # Calibration scans are processed by the pipeline as part of scan.from_disk before integration to give the pipeline a chance to apply necessary corrections 
        for target_scan_set in self.obs_model.target_scans:
            for scan_model in target_scan_set.scans:
                if scan_model is None or scan_model.scan_id in self.blacklist or scan_model.scan_type == ScanType.SKY:
                    continue

                files_prefix = scan_model.files_prefix

                if not files_prefix:
                    logger.error(f"Observation cannot integrate calibration scan without files prefix, skipping scan {scan_model.scan_id}.")
                    continue

                scan = Scan(scan_model=scan_model)

                # Calibration scans are loaded and pushed through the processing pipeline immediately.
                pipeline = self.pipeline_factory.create_pipeline(scan=scan, sky_q=sky_q, cal_q=cal_q) if self.pipeline_factory is not None else None
                scan = scan.from_disk(file_prefix=files_prefix, input_dir=dir, include_iq=False, pipeline=pipeline)

                if scan is None or scan.mpr is None:
                    logger.error(f"Observation failed to load scan: {files_prefix} in {dir} while initialising integrated calibration scans.")
                    continue

                self._update_integrated_data_arrays(scan=scan)
                scan.__del__()  # Explicitly release memory as quickly as possible 

    def integrate_sky_scans(self, dir: str, sky_q: "Queue" = None, cal_q: "Queue" = None):
        """ Iterate over sky scan iterations for each target and aggregate their summed power spectra into integrated data arrays.

            Parameters:
                dir:        Directory containing the scan files.
                sky_q:      Optional SKY queue passed into the pipeline factory.
                cal_q:      Optional calibration queue passed into the pipeline factory.
        """

        if self.obs_model is None:
            logger.error("Observation metadata is required to initialize integrated sky scans.")
            raise XSoftwareFailure("Missing required parameters for initializing integrated sky scans.")

        if cal_q is None:
            logger.warning("Observation did not find a calibration queue, integrated sky scans will not be calibrated against any calibration scans.")

        dir = os.getcwd() if dir is None else os.path.expanduser(dir)  # Default to current working directory if not provided

        # Load non-blacklisted sky scans from disk and accumulate their spectra into the integrated data arrays based on their tgt_idx, freq_scan and scan_iter.
        # Sky scans are processed by the pipeline as part of scan.from_disk before integration to give the pipeline a chance to apply necessary corrections 
        for target_scan_set in self.obs_model.target_scans:
            for scan_model in target_scan_set.scans:
                if scan_model is None or scan_model.scan_id in self.blacklist or scan_model.scan_type != ScanType.SKY:
                    continue

                files_prefix = scan_model.files_prefix

                if not files_prefix:
                    logger.error(f"Observation cannot integrate sky scan without files prefix, skipping scan {scan_model.scan_id}.")
                    continue

                scan = Scan(scan_model=scan_model)

                # Sky scans are loaded and pushed through the processing pipeline immediately.
                pipeline = self.pipeline_factory.create_pipeline(scan=scan, sky_q=sky_q, cal_q=cal_q) if self.pipeline_factory is not None else None
                scan = scan.from_disk(file_prefix=files_prefix, input_dir=dir, include_iq=False, pipeline=pipeline)

                if scan is None or scan.mpr is None:
                    logger.error(f"Observation failed to load scan: {files_prefix} in {dir} while initialising integrated sky scans.")
                    continue

                self._update_integrated_data_arrays(scan=scan)
                scan.__del__()  # Explicitly release memory as quickly as possible 

    def synthesise_integrated_scans(self, sky_q: "Queue" = None, cal_q: "Queue" = None, signal_displays: dict = None):
        """ Synthesise integrated scans for each target and frequency scan combination by averaging the integrated summed power spectra across scan iterations.
            Synthesised scans are pushed to the appropriate queue for pipeline processing.

            Parameters:
                sky_q:         Queue for sky scans.
                cal_q:         Queue for calibration scans.

            Returns:
                Populated cal_q with synthesised calibration scans
        """
        if self.obs_model is None:
            logger.error("Observation requires metadata to synthesise integrated scans.")
            return

        if self.int_data_arrays is None or len(self.int_data_arrays) == 0:
            logger.error("Observation requires integrated data arrays to synthesise integrated scans.")
            return

        if sky_q is None or cal_q is None:
            logger.error("Observation requires both sky_q and cal_q queues to synthesise integrated scans.")
            return

        # Calculate the number of scan iterations that were integrated for each target and frequency scan 
        # This is needed to initialise a synthesised scan with the correct duration and hence data array sizes
        scan_iteration_counts = {}
        for entry_tgt_idx, entry_freq_scan, _ in self.int_data_arrays.keys():
            key = (entry_tgt_idx, entry_freq_scan)
            scan_iteration_counts[key] = scan_iteration_counts.get(key, 0) + 1

        # Build a synthetic scan for each integrated data array entry and push it to the appropriate queue
        # If the synthetic scan is already in the appropriatequeue, then update the existing scan
        for (tgt_idx, freq_scan, scan_iter), int_data in sorted(self.int_data_arrays.items()):
            scan = None

            scan_model = self.obs_model.get_target_scan_by_index(tgt_idx, freq_scan, scan_iter)
            if scan_model is None:
                logger.error(f"Observation cannot synthesise scan for tgt_idx {tgt_idx}, freq_scan {freq_scan}, scan_iter {scan_iter} as no scan model found for this combination.")
                continue

            if scan_model.scan_id in self.blacklist:
                logger.info(f"Observation skipping synthesising integrated scan for blacklisted scan_id {scan_model.scan_id} (tgt_idx={tgt_idx}, freq_scan={freq_scan}, scan_iter={scan_iter}).")
                continue

            int_spr = int_data.get("int_spr")
            int_mpr = int_data.get("int_mpr")
            int_tpw = int_data.get("int_tpw")
            secs = int_data.get("secs")
            scans = int_data.get("scans")

            if int_spr is None or int_mpr is None or int_tpw is None or secs == 0 or scans == 0:
                logger.warning(f"Observation skipping integrated sky scan for (tgt_idx={tgt_idx}, freq_scan={freq_scan}) due to missing integrated data or zero seconds/scans.")
                continue

            syn_scan_id = f"{self.obs_model.obs_id}-{tgt_idx}-{freq_scan}"
            cal_scan = next((scan for scan in list(cal_q.queue) if scan.scan_model.scan_id == syn_scan_id), None,)
            sky_scan = next((scan for scan in list(sky_q.queue) if scan.scan_model.scan_id == syn_scan_id), None,)

            q_scan = cal_scan if cal_scan is not None else sky_scan

            if q_scan is None:
                logger.debug(f"Observation synthesising new scan for scan_id {syn_scan_id} with integrated data and pushing to appropriate queue.")

                synthetic_scan_model = ScanModel(
                    obs_id=self.obs_model.obs_id,
                    tgt_idx=tgt_idx,
                    freq_scan=freq_scan,
                    scan_iter=-1,
                    dig_id=scan_model.dig_id,
                    scan_type=scan_model.scan_type,
                    status=ScanState.EMPTY,
                    spectral_resolution=scan_model.spectral_resolution,
                    center_freq=scan_model.center_freq,
                    sample_rate=scan_model.sample_rate,
                    gain=scan_model.gain,
                    duration=scan_iteration_counts.get((tgt_idx, freq_scan), 0), # Duration is set to the number of scan iterations that were integrated 
                    synthesised=True)                                            # Mark this scan as synthesised from integrated data) 

                synthetic_scan = Scan(scan_model=synthetic_scan_model)

                # Integrated sky scans are also processed by the processing pipeline to calibrate then against integrated calibration scans
                if scan_model.scan_type == ScanType.SKY:

                    # Set the processing pipeline for the synthetic scan if not already set, using the pipeline factory to create a pipeline 
                    if synthetic_scan.pipeline is None and self.pipeline_factory is not None:
                        synthetic_scan.set_pipeline(self.pipeline_factory.create_pipeline(scan=synthetic_scan, sky_q=sky_q, cal_q=cal_q))
                else:
                    synthetic_scan.set_pipeline(None)  # Integrated calibration scans are not processed by the pipeline (they have already been processed when loaded from disk) 

                synthetic_scan.load_spr(sec=1, spr=int_spr / scan_model.duration)
                synthetic_scan.scan_model.status = ScanState.COMPLETE if synthetic_scan.get_loaded_seconds() >= synthetic_scan.scan_model.duration else ScanState.WIP
                
                cal_q.put(synthetic_scan) if scan_model.scan_type != ScanType.SKY else sky_q.put(synthetic_scan)  
                
                scan = synthetic_scan  # Set scan to the newly created synthetic scan for potential updating if it already exists in the queue

            elif q_scan is not None and q_scan.scan_model.status == ScanState.WIP:  # Only update the scan in the queue if it is still a work in progress
                
                logger.debug(f"Observation updating existing synthesised scan in queue for scan_id {q_scan.scan_model.scan_id} with new integrated data.")

                q_scan.load_spr(sec=q_scan.get_loaded_seconds() + 1, spr=int_spr / scan_model.duration)  # Load the averaged summed power spectrum directly into the existing scan
                q_scan.scan_model.status = ScanState.COMPLETE if q_scan.get_loaded_seconds() >= q_scan.scan_model.duration else ScanState.WIP  
                scan = q_scan  # Set scan to the existing scan in the queue that was just updated with new integrated data for potential display update below
            else:
                scan = q_scan
                      
            if scan is not None and scan_model.dig_id in signal_displays:

                signal_displays[scan_model.dig_id].set_scan(scan=scan, load=None)
                signal_displays[scan_model.dig_id].display()
                # Press enter to keep the signal displays open
                input("Press Enter to continue...")

    @classmethod
    def from_disk(cls, dir: str, obs_id: str, blacklist: list[str] = None, pipeline_factory: "ProcessingPipelineFactory" = None) -> "Observation | None":
        """Load observation metadata from disk and construct an ``Observation``.

        Parameters:
            dir:     Directory containing the observation metadata file.
            obs_id:  Observation identifier used to derive the metadata filename.
            blacklist: List of scan IDs to exclude from processing.
            pipeline_factory: Factory for creating the processing pipeline.

        Returns:
            An ``Observation`` instance when loading succeeds, otherwise ``None``.
        """
        if obs_id is None:
            logger.error("Observation requires ID to load observation metadata from disk.")
            return None

        dir = os.getcwd() if dir is None else os.path.expanduser(dir)  # Default to current working directory if not provided
        obs_path = os.path.join(dir, f"{obs_id}-obs.json")

        if not os.path.isfile(obs_path):
            logger.error(f"Observation could not load metadata, file not found: {obs_path}")
            return None

        with open(obs_path, "r") as f:
            obs_data = json.load(f)

        obs_model = ObsModel().from_dict(obs_data)
        return cls(obs_model=obs_model, blacklist=blacklist, pipeline_factory=pipeline_factory)

    @classmethod
    def init_pipeline_factory(cls, input_dir: str) -> "ProcessingPipelineFactory":
        """ Load ``PipelineConfig.json`` and construct an Observation processing pipeline factory.

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
            logger.warning(f"Observation could not load processing pipeline factory configuration from directory {input_dir} file {filename}. File not found.")
        
        if pipeline_config is not None:
            logger.debug(f"Observation loaded processing pipeline factory configuration from directory {input_dir} file {filename}")
        else:
            pipeline_config = PipelineConfig()
            logger.info(f"Observation using default processing pipeline factory configuration as pipelineconfig file not loaded from directory {input_dir} file {filename}:\n{pipeline_config}")

        return ProcessingPipelineFactory(pipeline_config=pipeline_config)

    def _get_scan_index(self, scan_id: str) -> str:
        """Return the short ``tgt-freq-iter`` suffix for a full scan ID."""
        return "-".join(scan_id.split("-")[-3:]) if scan_id else ""

    def describe_sky_scans(self) -> str:
        """Return a fixed-width table describing SKY scans from observation metadata."""

        columns = [ ("Excl", 4), ("Scan Index", 10), ("Scan Type", 10), ("Tgt ID", 14), ("Tgt Coordinates", 30),
                    ("Tgt Pointing Type", 18), ("Feed", 10), ("Dig ID", 6), ("Center Freq", 11), ("Sample Rate", 11),
                    ("Gain", 7), ("Channels", 8), ("Duration", 8), ("Image", 8),]

        lines = []
        header = " ".join(fmt_cell(name, width) for name, width in columns)
        divider = " ".join("-" * width for _, width in columns)
        lines.append(header)
        lines.append(divider)

        for tgt_idx, target_scan_set in enumerate(self.obs_model.target_scans):
            target = self.obs_model.get_target_by_index(tgt_idx)
            target_config = self.obs_model.get_target_config_by_index(tgt_idx)
            target_scan_set = self.obs_model.get_target_scan_set_by_index(tgt_idx)

            for scan_model in target_scan_set.scans:
                if scan_model.scan_type != ScanType.SKY:
                    continue

                row = [
                    "[x]" if scan_model.scan_id in self.blacklist else "[ ]",
                    self._get_scan_index(scan_model.scan_id),
                    scan_model.scan_type.name,
                    target.id if target is not None and target.id is not None else "",
                    fmt_target_coords(target),
                    target.pointing.name if target is not None and target.pointing is not None else "",
                    target_config.feed_type.name if target_config is not None and target_config.feed_type is not None else "",
                    scan_model.dig_id,
                    fmt_float(scan_model.center_freq, scale=1e6, precision=2, suffix=" MHz"),
                    fmt_float(scan_model.sample_rate, scale=1e6, precision=2, suffix=" MHz"),
                    fmt_float(scan_model.gain, precision=1, suffix=" dB"),
                    str(scan_model.spectral_resolution),
                    fmt_float(scan_model.duration, precision=0, suffix=" s"),
                    fmt_image_link(scan_model.files_directory, f"{scan_model.files_prefix}-sigfig.png", 8),
                ]
                lines.append(" ".join(fmt_cell(value, width) for value, (_, width) in zip(row, columns)))

        return "\n".join(lines) + "\n"

    def describe_cal_scans(self) -> str:
        """Return a fixed-width table describing calibration scans from observation metadata."""
        
        columns = [("Excl", 4), ("Scan Index", 10), ("Scan Type", 10), ("Dig ID", 6), ("Center Freq", 11),  
                   ("Sample Rate", 11), ("Gain", 7), ("Channels", 8), ("Duration", 8), ("Image", 8),]

        lines = []
        header = " ".join(fmt_cell(name, width) for name, width in columns)
        divider = " ".join("-" * width for _, width in columns)
        lines.append(header)
        lines.append(divider)

        for target_scan_set in self.obs_model.target_scans:
            for scan_model in target_scan_set.scans:
                if scan_model.scan_type == ScanType.SKY:
                    continue

                row = [
                    "[x]" if scan_model.scan_id in self.blacklist else "[ ]",
                    self._get_scan_index(scan_model.scan_id),
                    scan_model.scan_type.name,
                    scan_model.dig_id,
                    fmt_float(scan_model.center_freq, scale=1e6, precision=2, suffix=" MHz"),
                    fmt_float(scan_model.sample_rate, scale=1e6, precision=2, suffix=" MHz"),
                    fmt_float(scan_model.gain, precision=1, suffix=" dB"),
                    str(scan_model.spectral_resolution),
                    fmt_float(scan_model.duration, precision=0, suffix=" s"),
                    fmt_image_link(scan_model.files_directory, f"{scan_model.files_prefix}-sigfig.png", 8),
                ]
                lines.append(" ".join(fmt_cell(value, width) for value, (_, width) in zip(row, columns)))

        return "\n".join(lines) + "\n"

    def describe_processing_pipeline_factory(self) -> str:
        """Return a fixed-width table description of the processing pipeline factory configuration."""
        if self.pipeline_factory is None:
            return "No processing pipeline factory configured."

        context_columns =   [("Context", 8), ("Name", 24), ("Input", 30), ("Usage", 90), ("Output", 6),]
        step_columns =      [("Dig ID", 8), ("Context", 8), ("Step Name", 12), ("Step Description", 96),]

        lines = []

        context_header = " ".join(fmt_cell(name, width) for name, width in context_columns)
        context_divider = " ".join("-" * width for _, width in context_columns)
        lines.append(context_header)
        lines.append(context_divider)

        for item in self.pipeline_factory.describe_contexts():
            row = [item.get("context", ""), item.get("name", ""), item.get("input", ""), item.get("usage", ""), item.get("output", ""),]
            lines.append(" ".join(fmt_cell(value, width) for value, (_, width) in zip(row, context_columns)))

        lines.append("")

        step_header = " ".join(fmt_cell(name, width) for name, width in step_columns)
        step_divider = " ".join("-" * width for _, width in step_columns)
        lines.append(step_header)
        lines.append(step_divider)

        dig_id = None
        for target_scan_set in self.obs_model.target_scans:
            for scan_model in target_scan_set.scans:
                if scan_model is not None and getattr(scan_model, "dig_id", None):
                    dig_id = scan_model.dig_id
                    break
            if dig_id is not None:
                break

        for item in self.pipeline_factory.describe_steps_for_dig(dig_id):
            row = [dig_id or "default", item["params"].get("context", ""), item["step"].name, item["description"],]
            lines.append(" ".join(fmt_cell(value, width) for value, (_, width) in zip(row, step_columns)))

        lines.append("")
        lines.append("Processing Pipeline Steps are configurable via the PipelineConfig.json file located in the ./config/<profile> directory.")

        return "\n".join(lines) + "\n"

    def init_signal_displays(self) -> dict:
        """ Create one ``SignalDisplay`` per digitiser used by the observation.

            Parameters:
                obs: Observation model containing the scan metadata.
                blacklist: Optional blacklist parameter reserved for future filtering.

            Returns:
                A dictionary keyed by digitiser ID containing ``SignalDisplay``
                instances.
        """
        signal_displays = {}
        
        for tgt_idx, target_scans in enumerate(self.obs_model.target_scans):
        
            # Determine freq_scans and scan_iterations
            freq_scans = getattr(target_scans, 'freq_scans', 0) or 0
        
            for freq_scan_idx in range(freq_scans):

                scan_model = target_scans.get_scan_by_index(freq_scan_idx, 0)  # Just need to check the first scan iteration for each freq_scan to get the dig_id
                if scan_model is not None:
                    dig_id = scan_model.dig_id
                    if dig_id not in signal_displays:
                        signal_displays[dig_id] = SignalDisplay(dig_id=dig_id)

        return signal_displays

def main():
    """Run a simple manual test for loading and printing an observation."""

    sky_q = Queue()
    cal_q = Queue()

    obs = Observation.from_disk(dir="~/samples", obs_id="ODT-2026-04-11T1100Z-dish002")

    if obs is None:
        print("Observation could not be loaded from disk.")
        return

    print(obs)
    print(obs.describe_cal_scans())
    print(obs.describe_sky_scans())

    signal_displays = obs.init_signal_displays()

    pipeline_factory = Observation.init_pipeline_factory(input_dir='config/' + 'default')
    obs.set_pipeline_factory(pipeline_factory)
    print(obs.describe_processing_pipeline_factory())

    obs.integrate_cal_scans(dir="~/samples", sky_q=sky_q, cal_q=cal_q)
    obs.synthesise_integrated_scans(sky_q=sky_q, cal_q=cal_q, signal_displays=signal_displays)
    obs.integrate_sky_scans(dir="~/samples", sky_q=sky_q, cal_q=cal_q)
    obs.synthesise_integrated_scans(sky_q=sky_q, cal_q=cal_q, signal_displays=signal_displays)

    print(f"Cal Queue Length: {cal_q.qsize()}")
    print(f"Sky Queue Length: {sky_q.qsize()}")

if __name__ == "__main__":
    main()
