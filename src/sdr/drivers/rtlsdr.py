from rtlsdr import RtlSdr, RtlSdrTcpClient
from rtlsdr import librtlsdr
from rtlsdr.rtlsdr import LibUSBError
from scipy.stats import shapiro, normaltest, norm

import numpy as np
import math
import subprocess
import time
import functools
import select
import sys
import termios
import tty

from models.comms import CommunicationStatus
from util.xbase import XSoftwareFailure, XHardwareFailure

import logging
logger = logging.getLogger(__name__)

DEFAULT_READ_SIZE = 256*1024  # Default number of samples/bytes to read from the SDR

class SDR:
    """ Software Defined Radio (SDR) interface class for RTL-SDR devices.
        This class provides methods to connect to the SDR, retrieve device information,
        enable/disable the bias tee, and stabilize the device by discarding initial samples.
    """
    AUTO_GAIN_SETTLE_SEC = 0.05     # Allow tuner writes to settle before synchronous reads

    def __init__(self, bias_t_enabled=False, sdr_config=None):
        """ Initialize the SDR interface and connect to the first available RTL-SDR device.     """

        self.sdr_config = sdr_config or {}
        self.rtlsdr = None
        self.connected = CommunicationStatus.NOT_ESTABLISHED
        self.read_counter = 0

        self.info = self._read_eeprom_info()
        if self.info:
            logger.info(f"SDR connected, device information: {self.info}")
        else:
            logger.warning("SDR unable to retrieve device information.")

        # Set Bias-T if requested
        if bias_t_enabled:
            if self._set_bias_t(enable=True):
                logger.info("SDR Bias-T enabled during initialization.")
            else:
                logger.warning("SDR failed to enable Bias-T during initialization.")

        if self.open():
            logger.info("SDR connection successful during initialization.")

            # Cached rtlsdr properties
            self.gain = self.rtlsdr.gain if self.rtlsdr is not None else None
            self.center_freq = self.rtlsdr.center_freq if self.rtlsdr is not None else None
            self.bandwidth = self.rtlsdr.bandwidth if self.rtlsdr is not None else None
            self.freq_correction = self.rtlsdr.freq_correction if self.rtlsdr is not None else None
            self.sample_rate = int(math.ceil(self.rtlsdr.sample_rate)) if self.rtlsdr is not None else None
    
    def open(self) -> bool:
        """ Open the SDR device connection if not already connected.
            :returns: True if the SDR is connected, False otherwise
        """
        if self.rtlsdr is None:
            try:
                device_index = self.sdr_config.get("device_index", 0)
                self.rtlsdr = RtlSdr(device_index)
                self.connected = CommunicationStatus.ESTABLISHED
                logger.info("SDR connection established.")
            except OSError as e:
                logger.error(f"SDR could not connect due to OSError: {e}")
                return False
            except Exception as e:
                logger.exception(f"SDR could not connect due to exception: {e}")
                return False

        return True
    
    def close(self):
        if self.rtlsdr:
            self.rtlsdr.close()
            self.rtlsdr = None
            self.connected = CommunicationStatus.NOT_ESTABLISHED
            logger.info("SDR connection closed.")

    def get_comms_status(self) -> CommunicationStatus:
        """ Get the current comms status of the SDR device.
            :returns: CommunicationStatus indicating if the SDR is connected
        """
        return self.connected

    def get_eeprom_info(self) -> dict:
        """ Retrieve the EEPROM information of the connected RTL-SDR device.
            :returns: A dictionary containing the Manufacturer, Product, and Serial number of the SDR
        """
        return self.info
        
    def _read_eeprom_info(self):
        """ Internal method to read the EEPROM information from the RTL-SDR device using the rtl_eeprom command.
            :returns: A dictionary containing the Manufacturer, Product, and Serial number of the SDR

            Important: This method uses subprocess to call the rtl_eeprom command-line tool. It cannot be called
            while the SDR device is open, as rtl_eeprom requires exclusive access to the device.
        """

        if self.rtlsdr is not None:
            logger.warning("SDR device must be closed before reading EEPROM information.")
            return None

        try:
            device_index = str(self.sdr_config.get("device_index", 0))
            result = subprocess.run(['rtl_eeprom', '-d', device_index], capture_output=True, text=True)
        except Exception as e:
            logger.exception(f"SDR exception occurred while retrieving SDR information. {e}")
            raise e

        eeprom_info = {}

        # Strangely the rtl_eeprom command returns the device information in stderr, not stdout
        output = result.stderr

        if output.strip() == "No supported devices found.":
            logger.warning("No local RTL-SDR devices found.")
        else:
            for line in output.splitlines():
                if 'Manufacturer' in line:
                    eeprom_info['Manufacturer'] = line.split(':', 1)[1].strip()
                if 'Product' in line:
                    eeprom_info['Product'] = line.split(':', 1)[1].strip()
                if 'Serial number' in line:
                    eeprom_info['Serial'] = line.split(':', 1)[1].strip()

        return eeprom_info

    def _set_bias_t(self, enable=True):
        """ Enable or disable the bias tee on the RTL-SDR device.
        This is used to power external devices such as LNA (Low Noise Amplifier) or antenna preamplifiers.
        :param enable: True to enable the bias tee, False to disable it
        """ 

        if self.rtlsdr is not None:
            logger.warning("SDR device must be closed before calling rtl_biast.")
            return None

        cmd = ['rtl_biast', '-b', '1'] if enable else ['rtl_biast', '-b', '0']

        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
        except Exception as e:
            logger.exception(f"SDR exception occurred while running command: {' '.join(cmd)} {e}")
            raise e

        if result.returncode == 0:
            logger.info(f"SDR switched BiasT to {'ON' if enable else 'OFF'} with command: {' '.join(cmd)}")
        else:
            logger.error(f"SDR failed to switch BiasT {'ON' if enable else 'OFF'} with command: {' '.join(cmd)}, return code: {result.returncode}")
            logger.error(result.stdout)
            logger.error(result.stderr)

        return result.returncode == 0  # Return True if the command was successful, False otherwise

    def stabilise(self, sample_rate=2.4e6, time_in_secs=5):
        """
        Stabilize the SDR by discarding initial samples for a specified duration.
        We typically see the SDR lose power over time as it warms up.
        """

        if self.rtlsdr is None:
            logger.warning("SDR device not connected.")
            return

        logger.info(f"SDR stabilising: Discarding samples for {time_in_secs} seconds at Sample Rate {sample_rate/1e6} MHz, Center Frequency {self.get_center_freq()/1e6} MHz, Gain {self.get_gain()}")

        for _ in range(time_in_secs):  # Discard samples for each second in the duration
            discard = np.zeros(int(sample_rate), dtype=np.complex128)  # Initialize a numpy array to hold the samples
            try:
                discard = self.rtlsdr.read_samples(int(sample_rate))
            except Exception as err:
                self._handle_read_error(err, f"while stabilising with {int(sample_rate)} samples")
            logger.info(f"SDR stabilising: Discarded {discard.size} samples, Sample Rate {sample_rate/1e6} MHz, Center Frequency {self.get_center_freq()/1e6} MHz, Gain {self.get_gain()} dB, Sample Power {np.sum(np.abs(discard)**2):.2f} [a.u.]")
        del discard  # Free up memory

    def _handle_read_error(self, err: Exception, operation: str):
        if isinstance(err, LibUSBError):
            logger.error(f"SDR USB/Device error {operation}: {err}")
        else:
            logger.exception(f"SDR unexpected exception {operation}: {err}")

        try:
            if self.rtlsdr is not None:
                self.rtlsdr.close()
        except Exception as close_err:
            logger.warning(f"SDR close after read error also failed: {close_err}")

        self.rtlsdr = None
        self.connected = CommunicationStatus.NOT_ESTABLISHED
        raise XHardwareFailure(f"SDR device disconnected or unavailable {operation}: {err}")

    def _reset_buffer(self):
        """Flush the SDR's internal USB buffer if the device is available."""

        if self.rtlsdr is None:
            logger.warning("SDR device not connected.")
            return

        result = librtlsdr.rtlsdr_reset_buffer(self.rtlsdr.dev_p)
        if result < 0:
            self._handle_read_error(
                LibUSBError(result, "Could not reset buffer"),
                "while resetting SDR buffer",
            )

    def get_gain_gaussianity(self, sample_rate=None, time_in_secs=1):
        """
        Get the Gaussianity p-values (Shapiro-Wilk) of the SDR samples over a specified duration.
        """

        sample_rate = sample_rate if sample_rate is not None else self.sample_rate

        p_threshold = 0.05 # p-value threshold for Gaussian detection
        sample_limit = 5000  # limit for Shapiro–Wilk

        samples = int(time_in_secs * sample_rate)

        if self.rtlsdr is None:
            logger.warning("SDR device not connected.")
            return False, (0.0, 0.0)

        try:
            x = self.rtlsdr.read_samples(samples)
        except Exception as err:
            self._handle_read_error(err, f"while reading {samples} samples for gaussianity test")

        # Take a random subset of samples to avoid warning and speed up test
        sample_count = len(x)
        if sample_count == 0:
            raise XHardwareFailure("SDR returned zero samples for gaussianity test.")

        idx = np.random.choice(sample_count, size=min(sample_limit, sample_count), replace=False)
        r_samples = x.real[idx]
        i_samples = x.imag[idx]

        # Run Gaussianity test (Shapiro–Wilk)
        stat_r, p_r = shapiro(r_samples)
        stat_i, p_i = shapiro(i_samples)

        if p_r > p_threshold and p_i > p_threshold:
            logger.info(f"SDR gaussianity test at gain={self.get_gain()} dB passed: P-Values: Real={p_r:.3f} AND Imaginary={p_i:.3f} greater than threshold {p_threshold} with power {np.sum(np.abs(x)**2):.2f} [a.u.]")
            return True, (p_r, p_i)
        else:
            logger.info(f"SDR gaussianity test at gain={self.get_gain()} dB failed: P-Values: Real={p_r:.3f} OR Imaginary={p_i:.3f} less than threshold {p_threshold} with power {np.sum(np.abs(x)**2):.2f} [a.u.]")
            return False, (p_r, p_i)

    def get_auto_gain(self, sample_rate=None, time_in_secs=1, p_threshold=0.05) -> (int, int):
        """Iterate through all SDR gain settings to find the optimal gain for Gaussianity.
            :param sample_rate: Sample rate in Hz
            :param time_in_secs: Duration in seconds to sample for each gain setting
            :param p_threshold: p-value threshold for Gaussian detection
            :returns:
            Return the gain setting that meets the Gaussianity criteria.
        """
        # Gain settings
        Glist = [1,3,7,9,12,14,16,17,19,21,23,25,28,30,32,34,36,37,39,40,42,43,44,45,48,50]

        # Lists to hold p-values per gain
        p_r_list = []
        p_i_list = []

        if self.rtlsdr is None:
            logger.warning("SDR device not connected.")
            return None, None

        # Remember original SDR gain setting
        orig_gain = self.gain
        sample_rate = sample_rate if sample_rate is not None else self.sample_rate

        # Loop over each gain setting
        for gain in Glist:
            self.set_gain(gain)  # Set the SDR gain
            # Auto-gain rapidly retunes the tuner, so give the hardware a short
            # settle period and flush any stale USB samples before testing.
            time.sleep(self.AUTO_GAIN_SETTLE_SEC)
            self._reset_buffer()
            result, (p_r, p_i) = self.get_gain_gaussianity(sample_rate=sample_rate, time_in_secs=time_in_secs)

            p_r_list.append(p_r)
            p_i_list.append(p_i)

        gaussian = False
        gauss_gain = None
        for i in range(len(Glist) - 1):
            if (p_r_list[i] > p_threshold and p_i_list[i] > p_threshold and
                p_r_list[i+1] > p_threshold and p_i_list[i+1] > p_threshold):
                gaussian = True
                gauss_gain = Glist[i+1]
                break

        self.set_gain(orig_gain)  # Restore original gain setting

        # If we find a gaussian gain
        if gaussian:
            logger.info(f"SDR optimal gain for gaussianity: {gauss_gain} dB\n")
        else: 
            logger.warning("\nNo SDR gain meets Gaussianity criteria — check signal chain.\n")

            max_p_r = np.max(p_r_list)
            # Set gauss gain to gain in Glist corresponding to maximum p_r_list else orig_gain if max=0.0
            gauss_gain = Glist[np.argmax(p_r_list)] if max_p_r > 0.0 else orig_gain
            logger.warning(f"Propose SDR gain {gauss_gain} dB based on maximum p_r value {max_p_r}\n")

        return gauss_gain

    def set_auto_gain(self, sample_rate=None, time_in_secs=1, p_threshold=0.05):
        """Automatically set the SDR gain to the optimal setting for Gaussianity.
            :param sample_rate: Sample rate in Hz
            :param time_in_secs: Duration in seconds to sample for each gain setting
            :param p_threshold: p-value threshold for Gaussian detection
            :returns:
            Return the gain setting that meets the Gaussianity criteria.
        """
        gain = self.get_auto_gain(sample_rate=sample_rate, time_in_secs=time_in_secs, p_threshold=p_threshold)
        if gain is not None:
            self.set_gain(gain)
            logger.info(f"SDR auto gain set to {gain} dB for optimal gaussianity.")
            return gain
        else:
            logger.warning("SDR auto gain could not be determined.")
            return None

    def get_center_freq(self):
        if self.rtlsdr is None:
            logger.warning("SDR device not connected.")
            return

        return self.rtlsdr.center_freq # Hz

    def set_center_freq(self, value):
        if self.rtlsdr is None:
            logger.warning("SDR device not connected.")
            return

        self.rtlsdr.center_freq = value
        self.center_freq = value

    def get_sample_rate(self):
        if self.rtlsdr is None:
            logger.warning("SDR device not connected.")
            return

        return self.rtlsdr.sample_rate # Hz

    def set_sample_rate(self, value):
        if self.rtlsdr is None:
            logger.warning("SDR device not connected.")
            return

        self.rtlsdr.sample_rate = value
        self.sample_rate = int(math.ceil(value))

    def get_bandwidth(self):
        if self.rtlsdr is None:
            logger.warning("SDR device not connected.")
            return

        return self.rtlsdr.bandwidth # MHz

    def set_bandwidth(self, value):
        if self.rtlsdr is None:
            logger.warning("SDR device not connected.")
            return

        self.rtlsdr.bandwidth = value
        self.bandwidth = value

    def get_gain(self):
        if self.rtlsdr is None:
            logger.warning("SDR device not connected.")
            return

        return self.rtlsdr.gain

    def set_gain(self, value):
        if self.rtlsdr is None:
            logger.warning("SDR device not connected.")
            return

        self.rtlsdr.gain = value
        self.gain = value

    def get_freq_correction(self):
        if self.rtlsdr is None:
            logger.warning("SDR device not connected.")
            return

        return self.rtlsdr.ppm # ppm

    def set_freq_correction(self, value):
        if self.rtlsdr is None:
            logger.warning("SDR device not connected.")
            return

        self.rtlsdr.ppm = value
        self.freq_correction = value

    def get_gains(self):
        if self.rtlsdr is None:
            logger.warning("SDR device not connected.")
            return

        return self.rtlsdr.get_gains()

    def get_tuner_type(self):
        if self.rtlsdr is None:
            logger.warning("SDR device not connected.")
            return

        return self.rtlsdr.get_tuner_type()
    
    def set_direct_sampling(self, value):
        if self.rtlsdr is None:
            logger.warning("SDR device not connected.")
            return

        self.rtlsdr.direct_sampling = value

    def read_bytes(self) -> (dict, bytes):
        """ Read self.sample_rate number of bytes from the SDR device.
            :returns: 
                A dictionary of metadata associated with the byte read
                A numpy array of uint8 samples read from the SDR
        """
        if self.rtlsdr is None:
            logger.warning("SDR device not connected.")
            return None, None

        self.sample_rate = int(self.rtlsdr.sample_rate)

        x = bytes(self.sample_rate)

        try:
            # Record start/end times associated with sample set (in epoch seconds)
            read_start = time.time()
            x = self.rtlsdr.read_bytes(self.sample_rate)
            read_end = time.time()

            self.read_counter += 1
            count = self.read_counter

        except Exception as e:
            self._handle_read_error(e, "while reading bytes from SDR")

        metadata = {
            'read_counter': count,
            'num_bytes': len(x),
            'read_start': read_start,
            'read_end': read_end,
        }
        logger.debug(f"SDR READ BYTES: requested {self.sample_rate} bytes, read {len(x)} bytes, start={read_start}, end={read_end}, duration={(read_end-read_start):.3f} seconds")
        return metadata, x

    def read_samples(self) -> (dict, np.ndarray):
        """ Read self.sample_rate number of bytes from the SDR device.
            :returns: 
                A dictionary of metadata associated with the sample read
                A numpy array of complex64 samples read from the SDR
        """
        if self.rtlsdr is None:
            logger.warning("SDR device not connected.")
            return None, None

        try:
            self.sample_rate = int(self.rtlsdr.sample_rate)

            # Record start/end times associated with sample set (in epoch seconds)
            read_start = time.time()
            x = self._read_complex_samples(self.sample_rate)
            read_end = time.time()

            self.read_counter += 1
            count = self.read_counter

        except Exception as e:
            self._handle_read_error(e, "while reading samples from SDR")

        metadata = {
            'read_counter': count,
            'num_samples': x.size,
            'read_start': read_start,
            'read_end': read_end,
        }

        #logger.info(f"SDR READ SAMPLES: requested {self.sample_rate} samples, read {x.size} samples, start={read_start}, end={read_end}, duration={(read_end-read_start):.3f} seconds")
        return metadata, x

    def _read_complex_samples(self, num_samples: int) -> np.ndarray:
        if self.rtlsdr is None:
            raise XHardwareFailure("SDR device is not connected.")

        try:
            samples = self.rtlsdr.read_samples(int(num_samples))
        except Exception as err:
            self._handle_read_error(err, f"while reading {num_samples} complex samples from SDR")

        return np.asarray(samples, dtype=np.complex64)

def _run_test_iteration(sdr: SDR):
    """Run the standalone SDR test sequence once."""

    sdr.stabilise()
    gain = sdr.get_auto_gain(sample_rate=2.4e6, time_in_secs=1, p_threshold=0.05)

    sdr.set_sample_rate(2.0e6)
    logger.info(f"SDR Sample Rate set to {sdr.get_sample_rate()/1e6} MHz")

    sdr.set_center_freq(435e6)  # Set frequency to 435 MHz
    logger.info(f"SDR Center Frequency set to {sdr.get_center_freq()/1e6} MHz")
    sdr.set_sample_rate(2.4e6)  # Set sample rate to 2.4 MHz
    logger.info(f"SDR Sample Rate set to {sdr.get_sample_rate()/1e6} MHz")
    sdr.set_gain(8.7)           # Set gain to 8.7 dB
    logger.info(f"SDR Gain set to {sdr.get_gain()} dB")

    logger.info(
        f"SDR Config - Center Frequency: {sdr.get_center_freq()/1e6} MHz, "
        f"Sample Rate: {sdr.get_sample_rate()/1e6} MHz, Gain: {sdr.get_gain()} dB"
    )

    if sdr.get_center_freq() != 435e6 or sdr.get_sample_rate() != 2.4e6 or sdr.get_gain() != 8.7:
        logger.error("SDR configuration did not apply correctly.")

    logger.info(f"SDR Tuner Type: {sdr.get_tuner_type()}, Available Gains: {sdr.get_gains()}")

    samples = sdr.read_samples()
    logger.info(f"Meta Data returned by read_samples call {samples[0]}")
    logger.info(f"Read {len(samples[1])} samples from SDR.")

def main():

    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG,  # Or DEBUG for more verbosity
        format='%(asctime)s %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    logger = logging.getLogger(__name__)

    sdr = SDR()

    info = sdr.get_eeprom_info()
    if info:
        logger.info(f"SDR Information: {info}")

    logger.info("Running SDR test loop. Press any key to stop.")

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    try:
        tty.setcbreak(fd)
        while True:
            _run_test_iteration(sdr)

            if select.select([sys.stdin], [], [], 0)[0]:
                sys.stdin.read(1)
                logger.info("Key press detected, stopping SDR demo loop.")
                break
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        sdr.close()

if __name__ == "__main__":
    main()
