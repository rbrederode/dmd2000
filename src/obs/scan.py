import numpy as np
import threading
import logging
import time
import json
import os
from datetime import datetime, timezone

from models.pipeline import StepConfig, StepType
from models.qa import ScanQA, QA
from models.scan import ScanModel, ScanState, ScanType
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

        # Compose a key from obs_id, tgt_idx, freq_scan
        # Obs_id is unique per observation and per dish (and hence digitiser)
        key = (scan_model.obs_id, scan_model.tgt_idx, scan_model.freq_scan) if scan_model.status != ScanState.COMPLETE else None

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

            self.loaded_secs = self.scan_model.duration * [False]    # List of seconds for which samples have been loaded
            self.prev_read_end = None                                # Timestamp of the previous read end

            # Data arrays that hold data for a given scan of {duration} seconds
            self.raw = None  # Raw IQ samples for the duration of the scan
            self.pwr = None  # Power spectrum for the duration of the scan
            self.spr = None  # Summed power spectrum for each second in the duration of the scan
            self.cal = None  # Calibrated power spectrum for each second in the duration of the scan
            self.mpr = None  # Mean power spectrum over duration of the scan
            self.snr = None  # Signal to Noise Ratio (SNR) for each second in the duration of the scan

            self.mean_real = 0.0  # Mean of real value of the raw samples (I)
            self.mean_imag = 0.0  # Mean of imaginary value of the raw samples (Q)
             
            # QA attributes for the signal in this scan
            self.scan_qa = ScanQA(scan_id=self.scan_model.scan_id, scan_duration=self.scan_model.duration)

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
            self.snr = np.zeros((self.scan_model.duration, 5), dtype=np.float64)            # Signal to Noise Ratio, Signal dB, Noise dB, signal start bin, signal end bin for each second in duration

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

    def is_load_scan(self) -> bool:
        """
        Check if this scan is a load scan (i.e. load flag is True in the scan model).
            :returns: True if this is a load scan, False otherwise
        """
        return self.scan_model.load

    def set_load_scan(self, load_scan: "Scan"):
        """
        Associate a load scan with this sky scan
            :param load_scan: The load scan to associate with this sky scan
        """
        if not isinstance(load_scan, Scan):
            raise XSoftwareFailure(f"Scan {self.scan_model.scan_id} - Provided load_scan is not a Scan instance")

        if not load_scan.is_load_scan():
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

        spr = self.pipeline.process(signal=spr, context={"pipeline": "spr"}) if self.pipeline else spr                # Push the summed power spectrum through the spr pipeline
        cal = self.pipeline.process(signal=spr.copy(), context={"pipeline": "cal"}) if self.pipeline else spr.copy()  # Push the summed power spectrum through the cal pipeline

        # Store the raw, power and summed spectrum data in the appropriate rows of the scan data arrays
        with self._rlock:
            self.raw[row_start:row_start + iq.shape[0],:] = iq
            self.pwr[row_start:row_start + iq.shape[0],:] = pwr
            self.spr[sec - 1,:] = spr  # sec is 1-based index, so adjust for 0-based array index
            self.cal[sec - 1,:] = cal  # sec is 1-based index, so adjust for 0-based array index
            self.loaded_secs[sec - 1] = True  # Mark this second as loaded

            indices = np.linspace(row_start, row_end - 1, int(self.raw.shape[0]*0.01), dtype=int)

            self.mean_real = np.mean(np.abs(self.raw[row_start:row_end, ].real))*100  # Find the mean real value in the raw samples (I)
            self.mean_imag = np.mean(np.abs(self.raw[row_start:row_end, ].imag))*100  # Find the mean imaginary value in the raw samples (Q)

            self.snr[sec - 1] = self.calc_snr(self.cal[sec - 1, :])  # (snr_db, signal_db, noise_db)
            snr_db, signal_db, noise_db, signal_start, signal_end = self.snr[sec - 1]
            logger.info(f"Scan {self.scan_model.scan_id} - SNR for second {sec}: {snr_db:.2f} dB | Signal: {signal_db:.2f} dB | Noise: {noise_db:.2f} dB")

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
            # Populate mean power spectrum (mpr) with the mean of the summed power spectrum (spr) across the duration for each channel
            self.mpr = np.mean(self.spr, axis=0)

        return True

    def calc_snr(self, spectrum: np.ndarray, window_frac: float = 0.20) -> tuple:
        """
        Calculate the signal, noise, and signal-to-noise ratio (SNR) for a given power spectrum using robust statistics.
        - The signal region is a window around the peak (default: 10% of channels, min 3 bins).
        - The noise region is all bins outside the signal window.
        - The baseline is the median of the noise region.
        - The signal is the peak value in the signal region above the baseline.
        - The noise is the robust RMS estimated via the MAD (median absolute deviation) of the noise region.
        - The SNR is computed as (signal - baseline) / noise (linear), and also reported in dB.
        :param spectrum: 1D numpy array of power values (e.g., a row from self.spr)
        :param window_frac: Fraction of channels to use for the signal window (default 20%)
        :return: tuple with values 'snr_db', 'signal_db', 'noise_db', 'signal_start', 'signal_end'

        For reference:
            SNR 3 dB - Barely detectable signal
            SNR 7 dB - Weak signal
            SNR 10 dB - Strong signal
            SNR 20 dB - Very strong signal
        """
        channels = len(spectrum)
        peak_bin = np.argmax(spectrum)

        window_width = max(3, int(window_frac * channels))
        half_width = window_width // 2

        signal_start = max(0, peak_bin - half_width)
        signal_end = min(channels, peak_bin + half_width + 1)

        signal_region = spectrum[signal_start:signal_end]
        if signal_start == 0:
            noise_region = spectrum[signal_end:]
        elif signal_end == channels:
            noise_region = spectrum[:signal_start]
        else:
            noise_region = np.concatenate((spectrum[:signal_start], spectrum[signal_end:]))

        # --- Baseline (robust)
        baseline = np.median(noise_region)

        # --- Signal (peak above baseline)
        peak = np.max(signal_region)
        signal_lin = max(peak - baseline, 1e-12)  # avoid log(0)

        # --- Noise (robust RMS via MAD)
        noise_std = 1.4826 * np.median(np.abs(noise_region - baseline))
        noise_lin = max(noise_std, 1e-12)

        # --- SNR (linear)
        snr = signal_lin / noise_lin

        # --- Convert to dB
        signal_db = 10 * np.log10(signal_lin)
        noise_db = 10 * np.log10(noise_lin)
        snr_db = 10 * np.log10(snr)

        return snr_db, signal_db, noise_db, signal_start, signal_end

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

            filename = prefix + "-spr" + ".csv"
            with open(f"{output_dir}/{filename}", 'w') as f:
                np.savetxt(f, self.spr, delimiter=",", fmt="%.6f")
        
        except Exception as e:
            logger.error(f"Scan {self} - Failed to save to {output_dir}/{filename}: {e}")
            return False

        logger.info(f"Scan {self} - Saved to {output_dir}/{prefix}-*")
        return True

    @classmethod
    def from_disk(cls, file_prefix: str, input_dir: str, include_iq: bool = False) -> 'Scan':
        """
        Static constructor that creates a Scan instance by loading scan data from files on disk.
            :param file_prefix: The file prefix to match against filenames in the input directory
            :param input_dir: Directory where the scan data files are located
            :param include_iq: Whether to load the IQ data or not (default is False)
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
            logger.warning(f"Scan - No meta data ({file_prefix}*meta.json) scan files found in dir {input_dir} matching prefix.")
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

        try:
            prefix = gen_file_prefix(dt=scan.scan_model.read_start, entity_id=scan.scan_model.dig_id, gain=scan.scan_model.gain, 
                duration=scan.scan_model.duration, sample_rate=scan.scan_model.sample_rate, center_freq=scan.scan_model.center_freq, 
                channels=scan.scan_model.channels, instance_id=scan.scan_model.scan_id, scan_type=scan.scan_model.scan_type)

            from sdp.sdp import SDP
            # Instantiate a processing pipeline and push the summed power spectrum through the spr and cal pipelines
            scan.pipeline = SDP.pipeline_factory.create_pipeline(scan) if SDP.pipeline_factory is not None else None

            if include_iq:
                # Load raw IQ samples 
                filename = prefix + "-raw" + ".iq"
                with open(f"{input_dir}/{filename}", 'rb') as f:

                    logger.info(f"Scan - Loading scan data from {input_dir}/{filename}")

                    scan.raw = np.fromfile(f, dtype=np.complex64)
                    scan.raw = scan.raw.reshape(-1, scan.scan_model.channels)

                # Recalculate power spectrum (scan.pwr)
                num_rows = scan.raw.shape[0]
                for row in range(num_rows):
                    scan.pwr[row,:] = np.abs(np.fft.fftshift(np.fft.fft(scan.raw[row,:])))**2 # The power spectrum is the absolute value of the signal squared

                # Recalculate the summed power spectrum (scan.spr)
                for sec in range(scan.scan_model.duration):
                    row_start = sec * (num_rows // scan.scan_model.duration)
                    row_end = (sec + 1) * (num_rows // scan.scan_model.duration) if sec < scan.scan_model.duration - 1 else num_rows  # Ensure we cover all rows

                    # Calculate the sum of the power spectrum for each frequency bin in a given second
                    scan.spr[sec,:] = np.sum(scan.pwr[row_start:row_end,:], axis=0)  # Sum the power spectrum in a given sec for each frequency bin (in columns)



                    # Push the summed power spectrum through the spr pipeline and cal pipeline if a pipeline is associated with this scan
                    scan.spr = self.pipeline.process(signal=scan.spr, context={"pipeline": "spr"}) if self.pipeline else scan.spr               
                    scan.cal = self.pipeline.process(signal=scan.spr.copy(), context={"pipeline": "cal"}) if self.pipeline else scan.spr.copy() 


                scan.loaded_secs = [True] * scan.scan_model.duration
            else:
                # Load summed power spectrum only
                filename = prefix + "-spr" + ".csv"
                with open(f"{input_dir}/{filename}", 'r') as f:

                    logger.info(f"Scan - Loading scan data from {input_dir}/{filename}")

                    scan.spr = np.loadtxt(f, delimiter=",")
                    scan.spr = scan.spr.reshape(-1, scan.scan_model.channels)

                scan.loaded_secs = [True] * scan.spr.shape[0]

            scan.mpr = np.mean(scan.spr, axis=0)  # Populate mean power spectrum (mpr) with the mean of the summed power spectrum (spr) across the duration for each channel

        except Exception as e:
            logger.error(f"Scan - Failed to load data from {input_dir}: {e}")
            return None

        logger.info(f"Scan - Completed loading scan {input_dir}:\n\n{scan}\n")
        return scan

    def find_equiv_scan(self, input_dir: str, scan_type: ScanType = ScanType.UNKNOWN) -> "Scan":
        """
        Get the calibration scan associated with this scan (if any).
        Searches for calibration scans in the input directory.
            :returns: The associated calibration scan if it exists, None otherwise
        """

        file_prefix = gen_file_prefix(dt=None, entity_id=self.scan_model.dig_id, gain=self.scan_model.gain, duration=self.scan_model.duration,
                sample_rate=self.scan_model.sample_rate, center_freq=self.scan_model.center_freq, channels=self.scan_model.channels,
                scan_type=scan_type if scan_type != ScanType.UNKNOWN else '')

        logger.info(f"*** Scan scan id {self.scan_model.scan_id} **** DO NOT MATCH START")

        cal_files = [f for f in os.listdir(input_dir) if file_prefix in f and not scan_id in f and f.endswith('spr.csv')]
        logger.info(f"Scan - Found {len(cal_files)} calibration scans in {input_dir} with prefix {file_prefix} for digitiser {self.scan_model.dig_id}")
        cal_files = sorted(cal_files, key=lambda f: os.path.getctime(os.path.join(input_dir, f)), reverse=True) if len(cal_files) > 0 else []
        cal_file = cal_files[0].removesuffix('load.csv') if len(cal_files) > 0 else None

        if load_file is not None:
            load_scan = Scan.from_disk(file_prefix=load_file, input_dir=input_dir, include_iq=False)
                
            if load_scan is not None and load_scan.is_load_scan():
                load_found = True
                logger.info(f"Scan {self.scan_model.scan_id} - Found load scan with id {load_scan.scan_model.scan_id} for digitiser {self.scan_model.dig_id}")
                return load_scan
        
        logger.info(f"Scan {self.scan_model.scan_id} - No load scan found for digitiser {self.scan_model.dig_id} in dir {input_dir} matching prefix {file_prefix}")
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

if __name__ == "__main__":

    # Setup logging configuration
    logging.basicConfig(
        level=logging.DEBUG,  # Set the logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        format="%(asctime)s - %(levelname)s - %(message)s",  # Log format
        handlers=[
            logging.StreamHandler(),                     # Log to console
            logging.FileHandler("client.log", mode="a")  # Log to a file
            ]
    )

    INPUT_DIR = '~/samples'  # Directory to store samples
    INPUT_DIR = os.path.expanduser(INPUT_DIR)

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
        prev_read_end=datetime.now(timezone.utc),
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

    print("="*40)
    print("Creating scan from scan model:")
    print("="*40)

    sky_scan1 = Scan(scan_model=scan_model)
    print (sky_scan1)
    
    prefix = "dig002-g23.0-du60-bw2.05-cf1420.73-ch2048"

    print("="*150)
    print(f"Creating scan using from_disk with file prefix {prefix} in dir {INPUT_DIR} (make sure this file exists):")
    print("="*150)
    scan2 = Scan.from_disk(file_prefix=prefix, input_dir=INPUT_DIR, include_iq=False)
    print(scan2)

    print("="*150)
    print(f"Load matching cal scan from_disk with file prefix {prefix} in dir {INPUT_DIR}:")
    print("="*150)

    cal_scan2 = sky_scan2.find_equiv_cal_scan(input_dir=INPUT_DIR)
    print(cal_scan2)
    exit(1)

    from sdp.signal_display import SignalDisplay

    display = SignalDisplay(dig_id="dig001")
    display.set_scan(scan=scan, load=scan)
    display.display()

    # press a key to continue
    input("Press Enter to continue...")

    scan.save_to_disk(output_dir=INPUT_DIR, include_iq=False)