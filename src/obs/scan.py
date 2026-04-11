import numpy as np
import threading
import logging
import time
import json
import os
from datetime import datetime, timezone

from models.pipeline import StepConfig, StepType
from models.qa import ScanQA, QA
from models.scan import ScanDataSource, ScanModel, ScanState, ScanType
from sdp.pipeline.steps.dc_spike import DCSpike
from util import gen_file_prefix
from util.xbase import XSoftwareFailure 

logger = logging.getLogger(__name__)

class Scan:

    _id_lock = threading.Lock()
    _scan_iter_counter = {}

    @staticmethod
    def reset_scan_iter_counter(obs_id: str, tgt_idx: int = None, freq_scan: int = None):
        """ Reset the scan iteration counter that is used to assign unique scan_iter values to scans with the same (obs_id, tgt_idx, freq_scan).
            This is needed when a digitiser restarted (scan_iter starts at 0 again) AND the observation id remains the same (it was reset).

            Matching behaviour:
                - All three provided: match on (obs_id, tgt_idx, freq_scan)
                - freq_scan is None:  match on (obs_id, tgt_idx)
                - tgt_idx and freq_scan are None: match on obs_id only
        """

        with Scan._id_lock:
            keys_to_remove = [key for key in Scan._scan_iter_counter
                              if key[0] == obs_id
                              and (tgt_idx is None or key[1] == tgt_idx)
                              and (freq_scan is None or key[2] == freq_scan)]
            for key in keys_to_remove:
                del Scan._scan_iter_counter[key]

    def __init__(self, scan_model: ScanModel):
        """ Initialize a scan with the given parameters.
            A scan holds raw IQ samples, power spectrum, summed power spectrum and baseline data arrays.
            The data arrays are initialized to zero and incrementally loaded as samples arrive.

            Parameters
                scan_model: The ScanModel instance containing the parameters for this scan
        """

        # Compose a key from obs_id, tgt_idx, freq_scan (obs_id is unique per observation, digitiser and dish)
        
        # Do not compose a key for scans that are already marked as COMPLETE or synthesised, 
        # as they will not be receiving new data and should not be assigned a scan_iter based on 
        # the counter (which is used to track iterations of scans that are being loaded with data).
        key = (
            (scan_model.obs_id, scan_model.tgt_idx, scan_model.freq_scan)
            if scan_model.status != ScanState.COMPLETE and not scan_model.synthesised
            else None
        )

        if key is not None:

            with Scan._id_lock:
            # If the key is new or changed, start at 0, otherwise increment the scan_iter for this key
                if key not in Scan._scan_iter_counter:
                    scan_iter = 0
                else:
                    scan_iter = Scan._scan_iter_counter[key] + 1

                # Set the scan_iter in the model and update the counter
                scan_model.scan_iter = scan_iter
                Scan._scan_iter_counter[key] = scan_iter

        self._rlock = threading.RLock()  # Lock for thread-safe access to shared resources

        with self._rlock:

            self.scan_model = scan_model
            self.pipeline = None            # Processing pipeline to calibrate scan data
            self.data_source = ScanDataSource.NONE   # Highest-fidelity scan data currently loaded into this scan

            self.loaded_secs = self.scan_model.duration * [False]    # List of seconds for which samples have been loaded
            self.prev_read_end = None                                # Timestamp of the previous read end

            # Data arrays that hold data for a given scan of {duration} seconds
            self.raw = None  # Raw IQ samples for the duration of the scan
            self.pwr = None  # Power spectrum for the duration of the scan
            self.spr = None  # Summed power spectrum for each second in the duration of the scan
            self.cal = None  # Calibrated power spectrum for each second in the duration of the scan
            self.mpr = None  # Mean power spectrum over duration of the scan

            self.mean_real = 0.0  # Mean of real value of the raw samples (I)
            self.mean_imag = 0.0  # Mean of imaginary value of the raw samples (Q)
             
            # QA attributes for the signal in this scan
            self.scan_qa = None

            self.load_scan = None  # Reference to a load scan for calibration

            # Initialize data arrays for the scan
            self.init_data_arrays()

    def __str__(self):

        created = self.scan_model.created.isoformat()
        total_time = (self.scan_model.read_end - self.scan_model.read_start).total_seconds() if self.scan_model.read_start is not None and self.scan_model.read_end is not None else None

        return f"Scan(id={self.scan_model.scan_id}, created={created}, scan model {self.scan_model}, total_time={total_time})\n"

    def __eq__(self, other):
        if not isinstance(other, Scan):
            return False

        return self.scan_model.scan_id == other.scan_model.scan_id

    def __del__(self):
        """ Destructor to clean up resources when a Scan instance is deleted. """
        logger.info(f"Scan {self.scan_model.scan_id} - Deleting scan instance and cleaning up resources.")
        self.del_iq()  # Flush IQ data from memory

    def equivalent(self, other):
        """ Check if this scan is equivalent to another scan (i.e. they have the same scan parameters).
            This is used to identify scans that are essentially the same and should be replaced in the processing queue when a new scan with the same parameters arrives.
        """
        if not isinstance(other, Scan):
            return False

        return self.scan_model.equivalent(other.scan_model)

    def get_start_end_idx(self) -> (int, int):
        """ Get the starting and ending index of the digitiser read counter for this scan.
            :returns: The starting and ending index as a tuple of integers
            Example: If start_idx=1000 and duration=60, then this function returns (1000, 1059)
            where 1000 is the starting index and 1059 is the ending index (inclusive) for a scan of 60 seconds
        """
        return self.scan_model.start_idx, self.scan_model.start_idx + self.scan_model.duration - 1

    def init_data_arrays(self):
        """
        Initialize data arrays for raw samples, power spectrum, summed power & mean power spectrum based on the scan parameters.
        The data arrays are flushed every time a new scan is started.
            :param sample_rate: Sample rate in Hz
            :param duration: Duration of the scan in seconds
            :param channels: Number of channels (FFT size) for the analysis
        """
        with self._rlock:

            # Calculate the number of rows in the spectrogram based on duration and sample rate
            num_rows = int(np.ceil(self.scan_model.duration * self.scan_model.sample_rate / self.scan_model.channels))      # number of rows in the spectrogram

            self.raw = np.zeros((num_rows, self.scan_model.channels), dtype=np.complex64)   # complex64 for raw IQ samples i.e. 8 bytes per sample (4 bytes for real and 4 bytes for imaginary parts)
            self.pwr = np.zeros((num_rows, self.scan_model.channels), dtype=np.float64)     # float64 for power spectrum data
            self.spr = np.zeros((self.scan_model.duration, self.scan_model.channels), dtype=np.float64)     # float64 for summed pwr for each second in duration
            self.cal = np.zeros((self.scan_model.duration, self.scan_model.channels), dtype=np.float64)     # float64 for calibrated spectrum for each second in duration
            self.mpr = np.ones((self.scan_model.channels,), dtype=np.float64)               # float64 for mean power spectrum over duration for each channel (fft bin)

    def init_qa(self) -> ScanQA:
        """
        Initialize the QA attributes for the signal in this scan.
            :returns: A ScanQA instance containing the initialized QA attributes for this scan
        """
        with self._rlock:
            self.scan_qa = ScanQA(scan_id=self.scan_model.scan_id, duration=self.scan_model.duration)  # Create a new ScanQA instance and assign it to this scan
            return self.scan_qa

    def get_dig_id(self) -> str:
        """
        Get the digitiser ID associated with this scan.
            :returns: The digitiser ID as a string
        """
        return self.scan_model.dig_id

    def get_scan_type(self) -> ScanType:
        """
        Get the scan type (e.g., SKY, LOAD) for this scan.
            :returns: The scan type as a ScanType enum value
        """
        return self.scan_model.scan_type

    def get_qa(self) -> ScanQA:
        """
        Get the QA attributes for the signal in this scan.
            :returns: A ScanQA instance containing the QA attributes for this scan
        """
        return self.scan_qa

    def get_obs_id(self) -> str:
        """
        Get the observation ID associated with this scan.
            :returns: The observation ID as a string
        """
        return self.scan_model.obs_id
    
    def get_status(self) -> str:
        """
        Get the current status of the scan.
            :returns: A string representation of the scan status
        """
        with self._rlock:
            return self.scan_model.status

    def set_status(self, status: ScanState):
        """
        Set the status of the scan.
            :param status: The new status to set
        """
        with self._rlock:
            self.scan_model.status = status

    def set_pipeline(self, pipeline: "ProcessingPipeline"):
        """
        Associates a Processing Pipeline with this scan for processing the samples as they are loaded.
        """
        with self._rlock:
            self.pipeline = pipeline

    def set_load_scan(self, load_scan: "Scan"):
        """
        Associate a load scan with this sky scan
            :param load_scan: The load scan to associate with this sky scan
        """
        if not isinstance(load_scan, Scan):
            raise XSoftwareFailure(f"Scan {self.scan_model.scan_id} - Provided load_scan is not a Scan instance")

        if not load_scan.get_scan_type() == ScanType.LOAD:
            raise XSoftwareFailure(f"Scan {self.scan_model.scan_id} - Provided load_scan {load_scan.scan_model.scan_id} is not a load scan (load flag is not True)")

        if not self.equivalent(load_scan):
            raise XSoftwareFailure(f"Scan {self.scan_model.scan_id} - Provided load_scan {load_scan.scan_model.scan_id} is not equivalent to this scan (different scan parameters)")

        with self._rlock:
            self.load_scan = load_scan
            self.scan_model.load_scan_id = load_scan.scan_model.scan_id

    def get_loaded_seconds(self) -> int:
        """
        Get the number of seconds for which samples have been loaded in this scan.
            :returns: Number of seconds with loaded samples
        """
        return np.count_nonzero(self.loaded_secs)

    def load_samples(self, sec: int, iq: np.ndarray, read_start: datetime, read_end: datetime) -> bool:
        """
        Load raw IQ samples into the scan's data array and calculate the power spectrum.
            :param sec: Second within the scan to load samples (1 <= sec <= scan duration)
            :param iq: A numpy array of complex64 IQ samples to load
            :param read_start: Timestamp when the samples were read (UTC)
            :param read_end: Timestamp when the samples were read (UTC)
            :returns: True if samples were loaded successfully, False otherwise
        Example: load_samples(1, 10, iq) will load samples for seconds 1 to 10 (inclusive) of the scan
        """

        if sec < 1 or sec > self.scan_model.duration:
            logger.warning(f"Scan {self.scan_model.scan_id} - Invalid second ({sec}) for scan duration {self.scan_model.duration}")
            self.scan_model.load_failures += 1
            return False

        if iq is None or len(iq) < self.scan_model.sample_rate:
            logger.warning(f"Scan {self.scan_model.scan_id} - Not enough samples provided. Expected {self.scan_model.sample_rate}, got {len(iq) if iq is not None else 0}. Skipping samples...")
            self.scan_model.load_failures += 1
            return False

        if read_start is None or read_end is None or read_start >= read_end:
            logger.warning(f"Scan {self.scan_model.scan_id} - Invalid read start/end timestamps provided. Skipping samples...")
            self.scan_model.load_failures += 1
            return False

        logger.debug(f"Scan {self.scan_model.scan_id} - Loading {iq.shape} samples for second {sec} into scan.")

        # Reshape the samples to fit into a number of rows, each of channels columns and convert to complex64 (if needed) for better efficiency
        iq = iq[:int(self.scan_model.sample_rate - (self.scan_model.sample_rate % self.scan_model.channels))].astype(np.complex64)  # Discard excess samples that don't fit into the channels
        iq = iq.reshape(-1, self.scan_model.channels) # Reshape to have rows each of size channels columns

        row_start = int((sec - 1) * self.scan_model.sample_rate / self.scan_model.channels)   # Calculate the starting row index (zero based) using sec
        row_end = int(sec * self.scan_model.sample_rate / self.scan_model.channels)           # Calculate the ending row index (zero based) using sec

        # Calculate the power spectrum for all rows in one vectorized FFT pass
        # This is more efficient than iterating through rows and calculating the FFT for each row separately
        # for j in range(iq.shape[0]):
        #    pwr[j,:] = np.abs(np.fft.fftshift(np.fft.fft(iq[j,:])))**2  
        pwr = np.abs(np.fft.fftshift(np.fft.fft(iq, axis=1), axes=1)) ** 2  # The power spectrum is the absolute value of the signal squared

        spr = np.sum(pwr, axis=0)  # Sum power across all rows for this second

        spr = self.pipeline.process(signal=spr, context={"pipeline": "spr", "sec": sec}) if self.pipeline else spr                # Push the summed power spectrum through the spr pipeline
        cal = self.pipeline.process(signal=spr.copy(), context={"pipeline": "cal", "sec": sec}) if self.pipeline else spr.copy()  # Push the summed power spectrum through the cal pipeline

        # Store the raw, power and summed spectrum data in the appropriate rows of the scan data arrays
        with self._rlock:
            self.raw[row_start:row_start + iq.shape[0],:] = iq
            self.pwr[row_start:row_start + iq.shape[0],:] = pwr
            self.spr[sec - 1,:] = spr  # sec is 1-based index, so adjust for 0-based array index
            self.cal[sec - 1,:] = cal  # sec is 1-based index, so adjust for 0-based array index

            # Build the mean spectrum from the loaded calibrated rows, including the
            # current second that was just written above.
            loaded_mask = np.array(self.loaded_secs, dtype=bool)
            loaded_mask[sec - 1] = True
            mpr = np.mean(self.cal[loaded_mask, :], axis=0) if np.any(loaded_mask) else np.zeros((self.scan_model.channels,), dtype=np.float64)
            self.mpr = self.pipeline.process(signal=mpr, context={"pipeline": "mpr", "sec": sec}) if self.pipeline else mpr

            self.loaded_secs[sec - 1] = True  # Mark this second as loaded only after mpr is populated
            self.data_source = ScanDataSource.RAW

            indices = np.linspace(row_start, row_end - 1, int(self.raw.shape[0]*0.01), dtype=int)

            self.mean_real = np.mean(np.abs(self.raw[row_start:row_end, ].real))*100  # Find the mean real value in the raw samples (I)
            self.mean_imag = np.mean(np.abs(self.raw[row_start:row_end, ].imag))*100  # Find the mean imaginary value in the raw samples (Q)

        # Count how many rows have self.loaded_secs marked as True
        actual_rows = np.count_nonzero(self.loaded_secs)
        expected_rows = self.scan_model.duration

        self.scan_model.read_start = read_start if self.scan_model.read_start is None else min(self.scan_model.read_start, read_start)  # Update read start time
        self.scan_model.read_end = read_end if self.scan_model.read_end is None else max(self.scan_model.read_end, read_end)  # Update read end time
        self.scan_model.gap = (read_start - self.prev_read_end).total_seconds() if self.prev_read_end is not None else None
        if self.scan_model.gap is not None:
            logger.debug(f"Scan {self.scan_model.scan_id} - Gap of {self.scan_model.gap:.3f} seconds detected between last read end {self.prev_read_end} and current read start {read_start}.")
        self.prev_read_end = read_end  # Update last read end time

        # Update scan status based on loaded rows
        if actual_rows == 0:
            self.set_status(ScanState.EMPTY)
        elif actual_rows > 0 and actual_rows < expected_rows:
            self.set_status(ScanState.WIP)
        elif actual_rows >= expected_rows:
            self.set_status(ScanState.COMPLETE)

        return True

    def load_spr(self, sec: int, spr: np.ndarray, read_start: datetime = None, read_end: datetime = None) -> bool:
        """
        Load a pre-computed summed power spectrum row into the scan.
            :param sec: Second within the scan to load the spectrum (1 <= sec <= scan duration)
            :param spr: Summed power spectrum for the given second
            :param read_start: Optional timestamp when the source data started
            :param read_end: Optional timestamp when the source data ended
            :returns: True if the spectrum was loaded successfully, False otherwise
        """

        if sec < 1 or sec > self.scan_model.duration:
            logger.warning(f"Scan {self.scan_model.scan_id} - Invalid second ({sec}) for scan duration {self.scan_model.duration}")
            self.scan_model.load_failures += 1
            return False

        if spr is None or len(spr) != self.scan_model.channels:
            logger.warning(
                f"Scan {self.scan_model.scan_id} - Invalid summed power spectrum length. "
                f"Expected {self.scan_model.channels}, got {len(spr) if spr is not None else 0}."
            )
            self.scan_model.load_failures += 1
            return False

        spr = np.asarray(spr, dtype=np.float64)
        cal = self.pipeline.process(signal=spr.copy(), context={"pipeline": "cal", "sec": sec}) if self.pipeline else spr.copy()

        with self._rlock:
            self.spr[sec - 1, :] = spr
            self.cal[sec - 1, :] = cal

            loaded_mask = np.array(self.loaded_secs, dtype=bool)
            loaded_mask[sec - 1] = True
            mpr = np.mean(self.cal[loaded_mask, :], axis=0) if np.any(loaded_mask) else np.zeros((self.scan_model.channels,), dtype=np.float64)
            self.mpr = self.pipeline.process(signal=mpr.copy(), context={"pipeline": "mpr", "sec": sec}) if self.pipeline else mpr

            self.loaded_secs[sec - 1] = True
            self.data_source = ScanDataSource.SPR

        if read_start is not None:
            self.scan_model.read_start = read_start if self.scan_model.read_start is None else min(self.scan_model.read_start, read_start)
        if read_end is not None:
            self.scan_model.read_end = read_end if self.scan_model.read_end is None else max(self.scan_model.read_end, read_end)

        loaded_count = np.count_nonzero(self.loaded_secs)
        if loaded_count == 0:
            self.set_status(ScanState.EMPTY)
        elif loaded_count < self.scan_model.duration:
            self.set_status(ScanState.WIP)
        else:
            self.set_status(ScanState.COMPLETE)

        return True

    def process_pipeline(self) -> bool:
        """
        Process all loaded scan data through the associated pipeline.
        - If raw IQ data is available, recompute power, summed power, and calibrated spectra.
        - If only summed power spectra are available, process those through the calibration pipeline.
            :returns: True if processing completed, False otherwise
        """

        loaded_sec_indices = [sec for sec, loaded in enumerate(self.loaded_secs) if loaded]

        if len(loaded_sec_indices) == 0:
            logger.warning(f"Scan {self.scan_model.scan_id} - No loaded data available to process through pipeline.")
            return False

        with self._rlock:
            self.cal.fill(0.0)  # Clear the calibrated spectrum array before re-processing

            if self.data_source == ScanDataSource.RAW:
                self.pwr = np.abs(np.fft.fftshift(np.fft.fft(self.raw, axis=1), axes=1)) ** 2
                rows_per_sec = self.pwr.shape[0] // self.scan_model.duration if self.scan_model.duration > 0 else 0

                for sec in loaded_sec_indices:
                    row_start = sec * rows_per_sec
                    row_end = (sec + 1) * rows_per_sec if sec < self.scan_model.duration - 1 else self.pwr.shape[0]

                    signal = np.sum(self.pwr[row_start:row_end, :], axis=0)
                    self.spr[sec, :] = self.pipeline.process(signal=signal, context={"pipeline": "spr", "sec": sec + 1}) if self.pipeline else signal
                    self.cal[sec, :] = self.pipeline.process(signal=self.spr[sec, :].copy(), context={"pipeline": "cal", "sec": sec + 1}) if self.pipeline else self.spr[sec, :].copy()

                valid_raw = self.raw[np.any(self.raw != 0, axis=1)]
                if valid_raw.shape[0] > 0:
                    self.mean_real = np.mean(np.abs(valid_raw.real)) * 100
                    self.mean_imag = np.mean(np.abs(valid_raw.imag)) * 100
            elif self.data_source == ScanDataSource.SPR:
                for sec in loaded_sec_indices:
                    signal = self.spr[sec, :]
                    self.cal[sec, :] = self.pipeline.process(signal=signal.copy(), context={"pipeline": "cal", "sec": sec + 1}) if self.pipeline else signal.copy()
            else:
                logger.warning(f"Scan {self.scan_model.scan_id} - No raw IQ or summed power data available to process.")
                return False

            valid_cal_rows = self.cal[loaded_sec_indices, :]
            self.mpr = np.mean(valid_cal_rows, axis=0) if valid_cal_rows.shape[0] > 0 else np.zeros((self.scan_model.channels,), dtype=np.float64)
            loaded_count = len(loaded_sec_indices)
            if loaded_count > 0:
                self.mpr = self.pipeline.process(signal=self.mpr.copy(), context={"pipeline": "mpr", "sec": loaded_count}) if self.pipeline else self.mpr

            if loaded_count == 0:
                self.set_status(ScanState.EMPTY)
            elif loaded_count < self.scan_model.duration:
                self.set_status(ScanState.WIP)
            else:
                self.set_status(ScanState.COMPLETE)

        logger.info(f"Scan - {self.scan_model.scan_id} Completed processing loaded scan data through pipeline.")
        return True

    def save_to_disk(self, output_dir, include_iq: bool = False) -> bool:
        """
        Flush the IQ sample data of the scan to a file on disk.
            :param output_dir: Directory where the IQ data file will be saved
            :param include_iq: Whether to flush the IQ data or not (default is False)
            :returns: True if the data was saved successfully, False otherwise
        """

        if self.scan_model.status != ScanState.COMPLETE:
            logger.warning(f"Scan - Saving an incomplete scan: {self}.")

        if output_dir is None or output_dir == '':
            output_dir = "./"

        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)

        prefix = gen_file_prefix(
            dt=self.scan_model.read_start, entity_id=self.scan_model.dig_id, gain=self.scan_model.gain, 
            duration=self.scan_model.duration, sample_rate=self.scan_model.sample_rate, center_freq=self.scan_model.center_freq, 
            channels=self.scan_model.channels, instance_id=self.scan_model.scan_id, scan_type=self.scan_model.scan_type
        )

        self.scan_model.files_prefix = prefix
        self.scan_model.files_directory = output_dir

        try:
            if include_iq:
                filename = prefix + "-raw" + ".iq"
                with open(f"{output_dir}/{filename}", 'wb') as f:
                    self.raw.tofile(f)

            filename = prefix + "-meta" + ".json"
            with open(f"{output_dir}/{filename}", 'w') as f:
                json.dump(self.get_scan_meta(), f, indent=4)  

            filename = prefix + "-qa" + ".json"
            with open(f"{output_dir}/{filename}", "w") as f:
                json.dump(self.get_qa_meta(), f, indent=4)

            filename = prefix + "-spr" + ".csv"
            with open(f"{output_dir}/{filename}", 'w') as f:
                np.savetxt(f, self.spr, delimiter=",", fmt="%.6f")
        
        except Exception as e:
            logger.error(f"Scan {self} - Failed to save to {output_dir}/{filename}: {e}")
            return False

        logger.info(f"Scan {self} - Saved to {output_dir}/{prefix}-*")
        return True

    @classmethod
    def from_disk(cls, file_prefix: str, input_dir: str, include_iq: bool = False, pipeline: "ProcessingPipeline" = None) -> 'Scan':
        """
        Static constructor that creates a Scan instance by loading scan data from files on disk.
            :param file_prefix: The file prefix to match against filenames in the input directory
            :param input_dir: Directory where the scan data files are located
            :param include_iq: Whether to load the IQ data or not (default is False)
            :param pipeline: Optional pre-built processing pipeline to attach after loading
            :returns: A Scan instance if loaded successfully, None otherwise
        """

        if file_prefix is None or file_prefix == '':
            logger.warning("Scan - file_prefix parameter is required to load scan from disk.")
            return None

        if input_dir is None or input_dir == '':
            input_dir = os.path.expanduser("./")

        logger.info(f"Scan - Searching for scans in {input_dir} with prefix {file_prefix}")
        read_files = [f for f in os.listdir(input_dir) if file_prefix in f and f.endswith('meta.json')]

        if read_files is None or len(read_files) == 0:
            logger.warning(f"Scan - No meta data ({file_prefix} meta.json) scan files found in dir {input_dir} matching prefix.")
            return None

        read_files = sorted(read_files, key=lambda f: os.path.getctime(os.path.join(input_dir, f)), reverse=True)
        read_file = read_files[0]

        logger.info(f"Scan - Reading meta data from {input_dir}/{read_file}")
        try:
            with open(f"{input_dir}/{read_file}", 'r') as f:
                meta = json.load(f)
                scan_model = ScanModel().from_dict(meta)

        except Exception as e:
            logger.error(f"Scan - Failed reading metadata from {input_dir}/{read_file}: {e}")
            return None

        if scan_model.status.value != ScanState.COMPLETE:
            logger.warning(f"Scan - Loading incomplete scan with status: {scan_model.status.name} from disk.\n{scan_model.to_dict()}")

        # Create the Scan instance (this initialises data arrays via __init__)
        scan = cls(scan_model)
        scan.set_pipeline(pipeline)

        try:
            prefix = gen_file_prefix(dt=scan.scan_model.read_start, entity_id=scan.scan_model.dig_id, gain=scan.scan_model.gain, 
                duration=scan.scan_model.duration, sample_rate=scan.scan_model.sample_rate, center_freq=scan.scan_model.center_freq, 
                channels=scan.scan_model.channels, instance_id=scan.scan_model.scan_id, scan_type=scan.scan_model.scan_type)

            if include_iq:
                # Load raw IQ samples 
                filename = prefix + "-raw" + ".iq"
                with open(f"{input_dir}/{filename}", 'rb') as f:

                    logger.info(f"Scan - Loading scan data from {input_dir}/{filename}")

                    scan.raw = np.fromfile(f, dtype=np.complex64)
                    scan.raw = scan.raw.reshape(-1, scan.scan_model.channels)

                scan.data_source = ScanDataSource.RAW
                scan.loaded_secs = [True] * scan.scan_model.duration
            else:
                # Load summed power spectrum only
                filename = prefix + "-spr" + ".csv"
                with open(f"{input_dir}/{filename}", 'r') as f:

                    logger.info(f"Scan - Loading scan data from {input_dir}/{filename}")

                    scan.spr = np.loadtxt(f, delimiter=",")
                    scan.spr = scan.spr.reshape(-1, scan.scan_model.channels)

                loaded_spectra = scan.spr.shape[0]
                if loaded_spectra < scan.scan_model.duration:
                    logger.warning(
                        f"Scan - Loaded {loaded_spectra} summed spectra from {input_dir}/{filename}, "
                        f"but scan duration expects {scan.scan_model.duration}. Treating as a partial scan."
                    )

                scan.data_source = ScanDataSource.SPR
                scan.loaded_secs = [True] * loaded_spectra + [False] * max(0, scan.scan_model.duration - loaded_spectra)

            # Potentially overwrites the quality metrics with regenerated metrics if the qa pipeline step is included 
            scan.process_pipeline()

        except Exception as e:
            logger.error(f"Scan - Failed to load data from {input_dir}: {e}")
            return None

        # Attempt to load QA metadata if not already present in the scan model (e.g., from a previous pipeline processing step)
        if scan.scan_qa is None:

            logger.info(f"Scan - Attempting to read QA metrics from {input_dir}/{file_prefix}-qa.json")
            try:
                with open(f"{input_dir}/{file_prefix}-qa.json", 'r') as f:
                    qa_meta = json.load(f)
                    scan.scan_qa = ScanQA.from_dict(qa_meta)
            except FileNotFoundError:
                logger.warning(f"Scan - QA metadata file not found: {input_dir}/{file_prefix}-qa.json")
                scan.scan_qa = None
            except Exception as e:
                logger.warning(f"Scan - Failed reading QA metadata from {input_dir}/{file_prefix}-qa.json: {e}")
                scan.scan_qa = None

        logger.info(f"Scan - Completed loading scan {input_dir}:\n\n{scan}\n")
        return scan

    def find_equiv_scan(self, input_dir: str, scan_type: ScanType = ScanType.UNKNOWN) -> "Scan":
        """
        Find the most recent scan on disk that is equivalent to this scan.
        If scan_type is provided, only scans of that type are considered.
            :returns: The matching equivalent scan if it exists, None otherwise
        """

        file_prefix = gen_file_prefix(dt=None, entity_id=self.scan_model.dig_id, gain=self.scan_model.gain, duration=self.scan_model.duration,
                sample_rate=self.scan_model.sample_rate, center_freq=self.scan_model.center_freq, channels=self.scan_model.channels,
                scan_type=scan_type if scan_type != ScanType.UNKNOWN else None)

        logger.info(f"Scan {self.scan_model.scan_id} - Searching for equivalent scans in {input_dir} with prefix {file_prefix}")

        equiv_files = [
            f for f in os.listdir(input_dir)
            if file_prefix in f
            and not (
                self.scan_model.scan_id is not None
                and self.scan_model.scan_id.lower() in f.lower()
                and self.scan_model.scan_type is not None
                and f"-{self.scan_model.scan_type.name.lower()}-" in f.lower()
            )
            and f.endswith('spr.csv')
        ]
        logger.info(
            f"Scan - Found {len(equiv_files)} equivalent scan files in {input_dir} with prefix {file_prefix} "
            f"for digitiser {self.scan_model.dig_id}"
        )
        equiv_files = sorted(equiv_files, key=lambda f: os.path.getctime(os.path.join(input_dir, f)), reverse=True) if len(equiv_files) > 0 else []
        equiv_file = equiv_files[0].removesuffix('-spr.csv') if len(equiv_files) > 0 else None

        if equiv_file is not None:
            equiv_scan = Scan.from_disk(file_prefix=equiv_file, input_dir=input_dir, include_iq=False)

            if equiv_scan is not None:
                logger.info(
                    f"Scan {self.scan_model.scan_id} - Found equivalent scan with id {equiv_scan.scan_model.scan_id} "
                    f"for digitiser {self.scan_model.dig_id}"
                )
                return equiv_scan
        
        logger.info(
            f"Scan {self.scan_model.scan_id} - No equivalent scan found for digitiser {self.scan_model.dig_id} "
            f"in dir {input_dir} matching prefix {file_prefix}"
        )
        return None

    def del_iq(self):
        """ Flush the iq data to the bin """
        with self._rlock:
            if hasattr(self, 'raw') and self.raw is not None:
                logger.info(f"Scan {self.scan_model.scan_id} - Deleting raw IQ data from memory.")
                del self.raw

    def get_scan_meta(self) -> dict:
        """
        Get metadata about the scan as a dictionary.
            :returns: A dictionary containing metadata about the scan
        """
        with self._rlock:
            return self.scan_model.to_dict()

    def get_qa_meta(self) -> dict:
        """
        Get metadata about the scan QA as a dictionary.
            :returns: A dictionary containing metadata about the scan QA
        """
        with self._rlock:
            return self.scan_qa.to_dict() if self.scan_qa is not None else {}

if __name__ == "__main__":

    # Setup logging configuration
    logging.basicConfig(
        level=logging.INFO,  # Set the logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        format="%(asctime)s - %(levelname)s - %(message)s",  # Log format
        handlers=[
            logging.StreamHandler(),                     # Log to console
            logging.FileHandler("client.log", mode="a")  # Log to a file
            ]
    )

    INPUT_DIR = './tests/test data'  # Directory to store samples
    INPUT_DIR = os.path.expanduser(INPUT_DIR)
    SPR_PREFIX = "dig002-g23.0-du60-bw2.05-cf1420.73-ch2048"
    IQ_PREFIX = "dig002-g12.0-du60-bw2.4-cf1419.69-ch1024"

    def print_banner(title: str):
        print("\n" + "=" * 150)
        print(title)
        print("=" * 150)

    def build_test_pipeline_factory():
        from models.pipeline import PipelineConfig, StepConfig, StepType
        from sdp.pipeline.pipeline_factory import ProcessingPipelineFactory

        step1 = StepConfig(step=StepType.DC_SPIKE, params={"pipeline": "spr"})
        step2 = StepConfig(step=StepType.LOAD, params={"pipeline": "cal"})
        step3 = StepConfig(step=StepType.RFI_FLAG, params={"pipeline": "cal", "threshold": 5, "window_size": 21})
        step4 = StepConfig(step=StepType.QA, params={"pipeline": "cal", "window_frac": 0.2})
        step5 = StepConfig(step=StepType.QA, params={"pipeline": "mpr", "window_frac": 0.2})
        config = PipelineConfig(steps_map={"default": [step1, step2, step3, step4, step5]})
        return ProcessingPipelineFactory(config)

    # Test 1: Basic scan creation from a ScanModel
    print_banner("Test 1 - Creating scan from scan model")
    scan_model = ScanModel(
        dig_id="dig001",
        obs_id="obs001",
        tgt_idx=0,
        freq_scan=1,
        scan_iter=5,
        scan_type=ScanType.SKY,
        created=datetime.now(timezone.utc),
        read_start=datetime.now(timezone.utc),
        read_end=datetime.now(timezone.utc),
        start_idx=100,
        duration=60,
        sample_rate=24e5,
        channels=1024,
        center_freq=1420400000.0,
        gain=12.0,
        load=False,
        status=ScanState.WIP,
        load_failures=0,
        last_update=datetime.now(timezone.utc)
    )
    sky_scan1 = Scan(scan_model=scan_model)
    print(sky_scan1)

    # Test 2: Load an SPR-only scan from disk
    print_banner(f"Test 2 - Loading SPR-only scan from disk with prefix {SPR_PREFIX}")
    sky_scan2 = Scan.from_disk(file_prefix=SPR_PREFIX, input_dir=INPUT_DIR, include_iq=False)
    print(sky_scan2)

    # Test 3: Load a sky scan from IQ data on disk without processing pipeline attached yet
    print_banner(f"Test 3 - Loading IQ scan from disk with prefix {IQ_PREFIX}")
    sky_scan3 = Scan.from_disk(file_prefix=IQ_PREFIX, input_dir=INPUT_DIR, include_iq=True)
    print(sky_scan3)

    # Test 4: Load the matching LOAD scan, process the IQ scan through the pipeline, and display it
    print_banner("Test 4 - Loading equivalent LOAD scan, processing IQ scan through pipeline, and displaying it")
    load_scan3 = None
    processed_scan3 = None

    if sky_scan3 is not None:
        load_scan3 = sky_scan3.find_equiv_scan(input_dir=INPUT_DIR, scan_type=ScanType.LOAD)
        print(load_scan3)

        if load_scan3 is not None:
            from queue import Queue
            from sdp.signal_display import SignalDisplay

            factory = build_test_pipeline_factory()
            sky_q = Queue()
            cal_q = Queue()

            cal_q.put(load_scan3)
            sky_q.put(sky_scan3)

            pipeline = factory.create_pipeline(scan=sky_scan3, sky_q=sky_q, cal_q=cal_q)
            sky_scan3.set_pipeline(pipeline)
            sky_scan3.process_pipeline()
            processed_scan3 = sky_scan3

            display = SignalDisplay(dig_id=processed_scan3.get_dig_id())
            display.set_scan(scan=processed_scan3, load=load_scan3)
            display.display()

            input("Press Enter to continue...")
        else:
            logger.warning(
                f"Unable to process IQ scan {sky_scan3.scan_model.scan_id} because no equivalent load scan was found in {INPUT_DIR}."
            )
    else:
        logger.warning(f"Unable to run pipeline/display test because IQ scan prefix {IQ_PREFIX} could not be loaded from {INPUT_DIR}.")
