import os
import json
import numpy as np
import logging
from queue import Queue

from models.obs import Observation
from models.pipeline import PipelineConfig, StepConfig, StepType
from models.scan import ScanModel, ScanType, ScanState
from scan import Scan
from sdp.pipeline.pipeline_factory import ProcessingPipelineFactory
from sdp.signal_display import SignalDisplay

logger = logging.getLogger(__name__)

def load_obs_metadata() -> Observation | None:
    """Prompt for and load observation metadata as an Observation instance."""
   
    obs_path = input("Enter the path and filename to the observation JSON file: ").strip()
    obs_path = os.path.expanduser(obs_path)
   
    if not os.path.isfile(obs_path):
        logger.error(f"File not found: {obs_path}")
        return None
   
    with open(obs_path, 'r') as f:
        obs_data = json.load(f)
    obs = Observation().from_dict(obs_data)
   
    return obs

def init_data_arrays(obs: Observation) -> dict:
    """Initialise a dict of numpy arrays for each target (freq_scans x channels)."""
    
    data_arrays = {}
    
    for tgt_idx, target_scans in enumerate(obs.target_scans):
    
        # Determine freq_scans and scan_iterations
        freq_scans = getattr(target_scans, 'freq_scans', 0) or 0
        scan_iterations = getattr(target_scans, 'scan_iterations', 0) or 0
        freq_scan_arrays = []
    
        for freq_scan_idx in range(freq_scans):

            # Find the first valid ScanModel for this freq_scan to get channels
            first_scan_model = None
           
            for scan_iter in range(scan_iterations):

                scan_model = target_scans.get_scan_by_index(freq_scan_idx, scan_iter)
                if scan_model is not None:
                    first_scan_model = scan_model
                    break
           
            if first_scan_model is not None:
                channels = first_scan_model.channels
                freq_scan_arrays.append(np.zeros(channels))
            else:
                freq_scan_arrays.append(None)
        data_arrays[tgt_idx] = freq_scan_arrays

    return data_arrays

def init_signal_displays(obs: Observation) -> dict:
    """Initialise a dict of SignalDisplay instances for each digitiser used in the observation."""
    
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

    # Load processing pipeline factory configuration from disk
        
    filename = "PipelineConfig.json"

    try:
        pipeline_config = PipelineConfig().load_from_disk(input_dir=input_dir, filename=filename)
    except FileNotFoundError:
        pipeline_config = None
        logger.warning(f"Observation could not load processing pipeline factory configuration from directory {input_dir} file {filename}. File not found.")
    
    if pipeline_config is not None:
        logger.info(f"Observation loaded processing pipeline factory configuration from directory {input_dir} file {filename}:\n{pipeline_config}")
    else:
        pipeline_config = PipelineConfig()
        logger.info(f"Observation using default processing pipeline factory configuration as pipelineconfig file not found in directory {input_dir} file {filename}:\n{pipeline_config}")

    return ProcessingPipelineFactory(pipeline_config=pipeline_config)

def get_scan_data(obs: Observation, data_arrays: dict, signal_displays: dict, pipeline_factory: "ProcessingPipelineFactory" = None):
    """Load scan data from disk and sum into the data arrays."""

    scan_q = Queue()
    cal_q = Queue()

    for tgt_idx, target_scans in enumerate(obs.target_scans):

        freq_scans = getattr(target_scans, 'freq_scans', 0) or 0
        scan_iterations = getattr(target_scans, 'scan_iterations', 0) or 0
        
        for freq_scan_idx in range(freq_scans):
        
            if data_arrays[tgt_idx][freq_scan_idx] is None:
                continue

            freq_scan_mpr = data_arrays[tgt_idx][freq_scan_idx]
        
            for scan_iter in range(scan_iterations):
        
                scan_model = target_scans.get_scan_by_index(freq_scan_idx, scan_iter)
                if scan_model is None:
                    continue
                
                dig_id = scan_model.dig_id
                files_prefix = scan_model.files_prefix
                files_directory = scan_model.files_directory

                if not files_prefix or not files_directory:
                    logger.error(f"  Skipping scan with missing files_prefix or files_directory.")
                    continue

                scan = Scan(scan_model=scan_model)

                # If the scan does not have an equivalent calibration scan in cal_q, then attempt to load an equivalent calibration scan from disk
                cal_scan = None
                for cal in list(cal_q.queue):
                    if cal.scan_model.equivalent(scan_model) and cal.get_scan_type() == ScanType.LOAD and cal.get_status() == ScanState.COMPLETE and cal.scan_model.scan_id != scan.scan_model.scan_id:
                        cal_scan = cal
                        cal_q.put(cal_scan)
                        logger.info(f"  Found equivalent calibration scan in cal_q for scan {scan_model.scan_id}:\n{cal_scan}")
                        break

                    logger.info(f"  No equivalent calibration scan in cal_q for scan {scan_model.scan_id} - checked cal_scan {cal.scan_model.scan_id} with status {cal.get_status().name} and type {cal.get_scan_type().name}")

                if cal_scan is None:

                        cal_scan_model = ScanModel(
                            dig_id=scan_model.dig_id,
                            scan_type=ScanType.LOAD,
                            read_start=scan_model.read_start,
                            gain=scan_model.gain,
                            channels=scan_model.channels,
                            duration=scan_model.duration,
                            files_prefix=files_prefix.replace("sky", "load"),
                            files_directory=files_directory
                        )
                        cal_scan = Scan(scan_model=cal_scan_model)
                        cal_q.put(cal_scan)

                # Create a signal processing pipeline using the pipeline factory and associate it with the scan
                pipeline = pipeline_factory.create_pipeline(scan=scan, scan_q=scan_q, cal_q=cal_q) if pipeline_factory is not None else None
                scan = scan.from_disk(file_prefix=files_prefix, input_dir=files_directory, include_iq=False, pipeline=pipeline)

                if scan is None or scan.spr is None:
                    logger.error(f"  Failed to load scan: {files_prefix} in {files_directory}")
                    continue

                scan_q.put(scan) if scan.get_scan_type() == ScanType.SKY else cal_q.put(scan) 

                if dig_id in signal_displays:
                    signal_displays[dig_id].set_scan(scan=scan, load=cal_scan)
                    signal_displays[dig_id].display()
                    input("Press Enter to continue...")

                freq_scan_mpr += np.sum(scan.spr, axis=0)

            data_arrays[tgt_idx][freq_scan_idx] = freq_scan_mpr

def summary(data_arrays):
    """Print a summary of the loaded data arrays."""

    for tgt_idx, freq_scan_arrays in data_arrays.items():
        valid_arrays = [arr for arr in freq_scan_arrays if arr is not None]
    
        if valid_arrays:
            arr_shapes = [arr.shape for arr in valid_arrays]
            print(f"Target {tgt_idx}: {len(valid_arrays)} freq_scans, array shapes: {arr_shapes}")
        else:
            print(f"Target {tgt_idx}: No valid freq_scans loaded.")

def main():

    config_dir = "config/default"

    obs = load_obs_metadata()
    if obs is None:
        return

    data_arrays      = init_data_arrays(obs)
    signal_displays  = init_signal_displays(obs)
    pipeline_factory = init_pipeline_factory(input_dir=config_dir)

    get_scan_data(obs, data_arrays, signal_displays, pipeline_factory)
    summary(data_arrays)

    input("Press Enter to continue...")

if __name__ == "__main__":
    main()