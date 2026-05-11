from __future__ import annotations

from scipy.stats import shapiro

import math
import time
import numpy as np

from models.comms import CommunicationStatus
from util.xbase import XHardwareFailure, XSoftwareFailure

import logging

logger = logging.getLogger(__name__)


class SDR:
    """Software Defined Radio driver implemented with SoapySDR."""

    AUTO_GAIN_SETTLE_SEC = 0.05

    def __init__(self, bias_t_enabled=False, sdr_config=None):
        self.sdr_config = sdr_config or {}
        self.device = None
        self.stream = None
        self.connected = CommunicationStatus.NOT_ESTABLISHED
        self.read_counter = 0

        self.channel = int(self.sdr_config.get("channel", 0))
        self.gain_name = self.sdr_config.get("gain_name")
        self.info = {}

        if self.open():
            self.info = self._read_device_info()
            logger.info(f"SDR connected with SoapySDR device information: {self.info}")
            if bias_t_enabled:
                self._set_bias_t(True)

            self.gain = self.get_gain()
            self.center_freq = self.get_center_freq()
            self.bandwidth = self.get_bandwidth()
            self.freq_correction = self.get_freq_correction()
            self.sample_rate = int(math.ceil(self.get_sample_rate()))

    def open(self) -> bool:
        if self.device is not None:
            return True

        try:
            import SoapySDR
            from SoapySDR import SOAPY_SDR_CF32, SOAPY_SDR_RX
        except ImportError as exc:
            raise XSoftwareFailure(
                "SoapySDR Python bindings are not installed. Install SoapySDR and the Soapy Airspy module before using sdr_type='soapy'."
            ) from exc

        try:
            self._soapy = SoapySDR
            self._SOAPY_SDR_RX = SOAPY_SDR_RX
            self._SOAPY_SDR_CF32 = SOAPY_SDR_CF32

            self.device = SoapySDR.Device(self._device_args())
            self.stream = self.device.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32, [self.channel])
            self.device.activateStream(self.stream)
            self.connected = CommunicationStatus.ESTABLISHED
            logger.info("SoapySDR connection established.")
            return True
        except Exception as exc:
            self.device = None
            self.stream = None
            self.connected = CommunicationStatus.NOT_ESTABLISHED
            logger.error(f"SoapySDR could not connect due to exception: {exc}")
            return False

    def close(self):
        if self.device is not None and self.stream is not None:
            try:
                self.device.deactivateStream(self.stream)
            except Exception as exc:
                logger.warning(f"SoapySDR stream deactivate failed: {exc}")
            try:
                self.device.closeStream(self.stream)
            except Exception as exc:
                logger.warning(f"SoapySDR stream close failed: {exc}")

        self.stream = None
        self.device = None
        self.connected = CommunicationStatus.NOT_ESTABLISHED
        logger.info("SoapySDR connection closed.")

    def get_comms_status(self) -> CommunicationStatus:
        return self.connected

    def get_eeprom_info(self) -> dict:
        return self.info

    def _device_args(self) -> dict:
        if isinstance(self.sdr_config.get("device_args"), dict):
            return dict(self.sdr_config["device_args"])

        args = {}
        for key in ("driver", "serial", "label"):
            if self.sdr_config.get(key) is not None:
                args[key] = str(self.sdr_config[key])

        return args

    def _read_device_info(self) -> dict:
        if self.device is None:
            return {}

        info = {}

        try:
            info.update(self.device.getHardwareInfo())
        except Exception as exc:
            logger.warning(f"SoapySDR failed to read hardware info: {exc}")

        try:
            info["Driver Key"] = self.device.getDriverKey()
        except Exception:
            pass

        try:
            info["Hardware Key"] = self.device.getHardwareKey()
        except Exception:
            pass

        return info

    def _set_bias_t(self, enable=True):
        if self.device is None:
            logger.warning("SoapySDR device not connected.")
            return False

        try:
            if hasattr(self.device, "setBiasT"):
                self.device.setBiasT(self._SOAPY_SDR_RX, self.channel, bool(enable))
                return True
        except Exception as exc:
            logger.warning(f"SoapySDR setBiasT failed: {exc}")

        setting = self.sdr_config.get("bias_t_setting", "biastee")
        try:
            settings = self.device.listSettings()
            if setting in settings:
                self.device.writeSetting(setting, "true" if enable else "false")
                return True
        except Exception as exc:
            logger.warning(f"SoapySDR bias tee setting failed: {exc}")

        logger.warning("SoapySDR bias tee control is not available for this device.")
        return False

    def stabilise(self, sample_rate=2.4e6, time_in_secs=5):
        if self.device is None:
            logger.warning("SoapySDR device not connected.")
            return

        logger.info(f"SoapySDR stabilising: discarding samples for {time_in_secs} seconds at {sample_rate/1e6} MHz.")

        for _ in range(time_in_secs):
            discard = self._read_complex_samples(int(sample_rate))
            logger.info(f"SoapySDR stabilising: discarded {discard.size} samples, power {np.sum(np.abs(discard)**2):.2f} [a.u.]")

    def _handle_read_error(self, err: Exception, operation: str):
        logger.exception(f"SoapySDR exception {operation}: {err}")
        self.close()
        raise XHardwareFailure(f"SoapySDR device disconnected or unavailable {operation}: {err}")

    def _reset_buffer(self):
        if self.device is None:
            logger.warning("SoapySDR device not connected.")
            return

        try:
            self._read_complex_samples(min(int(self.sample_rate), 262144), timeout_us=100000)
        except XHardwareFailure:
            raise
        except Exception as exc:
            logger.debug(f"SoapySDR buffer flush did not complete: {exc}")

    def get_gain_gaussianity(self, sample_rate=None, time_in_secs=1):
        sample_rate = sample_rate if sample_rate is not None else self.sample_rate

        p_threshold = 0.05
        sample_limit = 5000
        samples = int(time_in_secs * sample_rate)

        if self.device is None:
            logger.warning("SoapySDR device not connected.")
            return False, (0.0, 0.0)

        try:
            x = self._read_complex_samples(samples)
        except Exception as err:
            self._handle_read_error(err, f"while reading {samples} samples for gaussianity test")

        sample_count = len(x)
        if sample_count == 0:
            raise XHardwareFailure("SoapySDR returned zero samples for gaussianity test.")

        idx = np.random.choice(sample_count, size=min(sample_limit, sample_count), replace=False)
        r_samples = x.real[idx]
        i_samples = x.imag[idx]

        stat_r, p_r = shapiro(r_samples)
        stat_i, p_i = shapiro(i_samples)

        if p_r > p_threshold and p_i > p_threshold:
            logger.info(f"SoapySDR gaussianity test at gain={self.get_gain()} dB passed: Real={p_r:.3f}, Imaginary={p_i:.3f}")
            return True, (p_r, p_i)

        logger.info(f"SoapySDR gaussianity test at gain={self.get_gain()} dB failed: Real={p_r:.3f}, Imaginary={p_i:.3f}")
        return False, (p_r, p_i)

    def get_auto_gain(self, sample_rate=None, time_in_secs=1, p_threshold=0.05):
        gains = self.get_gains()
        if not gains:
            logger.warning("SoapySDR device did not report usable gain values.")
            return None

        p_r_list = []
        p_i_list = []
        orig_gain = self.gain
        sample_rate = sample_rate if sample_rate is not None else self.sample_rate

        for gain in gains:
            self.set_gain(gain)
            time.sleep(self.AUTO_GAIN_SETTLE_SEC)
            self._reset_buffer()
            result, (p_r, p_i) = self.get_gain_gaussianity(sample_rate=sample_rate, time_in_secs=time_in_secs)
            p_r_list.append(p_r)
            p_i_list.append(p_i)

        gaussian = False
        gauss_gain = None
        for i in range(len(gains) - 1):
            if (
                p_r_list[i] > p_threshold and p_i_list[i] > p_threshold
                and p_r_list[i + 1] > p_threshold and p_i_list[i + 1] > p_threshold
            ):
                gaussian = True
                gauss_gain = gains[i + 1]
                break

        self.set_gain(orig_gain)

        if gaussian:
            logger.info(f"SoapySDR optimal gain for gaussianity: {gauss_gain} dB")
        else:
            max_p_r = np.max(p_r_list)
            gauss_gain = gains[int(np.argmax(p_r_list))] if max_p_r > 0.0 else orig_gain
            logger.warning(f"No SoapySDR gain meets Gaussianity criteria; proposing gain {gauss_gain} dB.")

        return gauss_gain

    def set_auto_gain(self, sample_rate=None, time_in_secs=1, p_threshold=0.05):
        gain = self.get_auto_gain(sample_rate=sample_rate, time_in_secs=time_in_secs, p_threshold=p_threshold)
        if gain is not None:
            self.set_gain(gain)
            logger.info(f"SoapySDR auto gain set to {gain} dB for optimal gaussianity.")
            return gain

        logger.warning("SoapySDR auto gain could not be determined.")
        return None

    def get_center_freq(self):
        if self.device is None:
            logger.warning("SoapySDR device not connected.")
            return
        return self.device.getFrequency(self._SOAPY_SDR_RX, self.channel)

    def set_center_freq(self, value):
        if self.device is None:
            logger.warning("SoapySDR device not connected.")
            return
        self.device.setFrequency(self._SOAPY_SDR_RX, self.channel, float(value))
        self.center_freq = float(value)

    def get_sample_rate(self):
        if self.device is None:
            logger.warning("SoapySDR device not connected.")
            return
        return self.device.getSampleRate(self._SOAPY_SDR_RX, self.channel)

    def set_sample_rate(self, value):
        if self.device is None:
            logger.warning("SoapySDR device not connected.")
            return

        sample_rate = float(value)
        supported = self._sample_rates()
        if supported and sample_rate not in supported:
            rates = ", ".join(f"{rate:g}" for rate in supported)
            raise XSoftwareFailure(f"SoapySDR sample rate {sample_rate:g} Hz is not supported by this device. Supported rates: {rates}")

        self.device.setSampleRate(self._SOAPY_SDR_RX, self.channel, sample_rate)
        self.sample_rate = int(math.ceil(sample_rate))

    def get_bandwidth(self):
        if self.device is None:
            logger.warning("SoapySDR device not connected.")
            return
        try:
            return self.device.getBandwidth(self._SOAPY_SDR_RX, self.channel)
        except Exception:
            return self.bandwidth if hasattr(self, "bandwidth") else 0.0

    def set_bandwidth(self, value):
        if self.device is None:
            logger.warning("SoapySDR device not connected.")
            return
        try:
            self.device.setBandwidth(self._SOAPY_SDR_RX, self.channel, float(value))
        except Exception as exc:
            logger.warning(f"SoapySDR bandwidth setting is not available or was rejected: {exc}")
        self.bandwidth = float(value)

    def get_gain(self):
        if self.device is None:
            logger.warning("SoapySDR device not connected.")
            return

        if self.gain_name:
            return self.device.getGain(self._SOAPY_SDR_RX, self.channel, self.gain_name)

        return self.device.getGain(self._SOAPY_SDR_RX, self.channel)

    def set_gain(self, value):
        if self.device is None:
            logger.warning("SoapySDR device not connected.")
            return

        gain = float(value)
        if self.gain_name:
            self.device.setGain(self._SOAPY_SDR_RX, self.channel, self.gain_name, gain)
        else:
            self.device.setGain(self._SOAPY_SDR_RX, self.channel, gain)
        self.gain = gain

    def get_freq_correction(self):
        if self.device is None:
            logger.warning("SoapySDR device not connected.")
            return
        return float(self.sdr_config.get("freq_correction", 0))

    def set_freq_correction(self, value):
        self.freq_correction = int(value)
        logger.warning("SoapySDR frequency correction is stored but not applied generically.")

    def get_gains(self):
        if self.device is None:
            logger.warning("SoapySDR device not connected.")
            return

        configured = self.sdr_config.get("gains")
        if configured:
            return [float(gain) for gain in configured]

        try:
            gain_range = (
                self.device.getGainRange(self._SOAPY_SDR_RX, self.channel, self.gain_name)
                if self.gain_name
                else self.device.getGainRange(self._SOAPY_SDR_RX, self.channel)
            )
            minimum = float(gain_range.minimum())
            maximum = float(gain_range.maximum())
            step = 1.0
            if hasattr(gain_range, "step"):
                range_step = float(gain_range.step())
                if range_step > 0:
                    step = range_step
            count = int(math.floor((maximum - minimum) / step)) + 1
            return [round(minimum + idx * step, 6) for idx in range(count + 1) if minimum + idx * step <= maximum]
        except Exception as exc:
            logger.warning(f"SoapySDR failed to read gain range: {exc}")
            return []

    def get_tuner_type(self):
        if self.device is None:
            logger.warning("SoapySDR device not connected.")
            return
        return self.info.get("Hardware Key", self.info.get("Driver Key", "SoapySDR"))

    def set_direct_sampling(self, value):
        logger.warning("SoapySDR direct sampling control is not available through the generic backend.")

    def read_bytes(self):
        metadata, samples = self.read_samples()
        if samples is None:
            return metadata, None
        return metadata, samples.tobytes()

    def read_samples(self):
        if self.device is None:
            logger.warning("SoapySDR device not connected.")
            return None, None

        try:
            self.sample_rate = int(self.get_sample_rate())
            read_start = time.time()
            x = self._read_complex_samples(self.sample_rate)
            read_end = time.time()

            self.read_counter += 1
            count = self.read_counter
        except Exception as exc:
            self._handle_read_error(exc, "while reading samples from SoapySDR")

        x = np.array(x, dtype=np.complex64)
        metadata = {
            "read_counter": count,
            "num_samples": x.size,
            "read_start": read_start,
            "read_end": read_end,
        }
        return metadata, x

    def _read_complex_samples(self, num_samples: int, timeout_us: int = 1000000) -> np.ndarray:
        if self.device is None or self.stream is None:
            raise XHardwareFailure("SoapySDR device stream is not open.")

        result = np.empty(num_samples, dtype=np.complex64)
        offset = 0

        while offset < num_samples:
            view = result[offset:]
            stream_result = self.device.readStream(self.stream, [view], len(view), timeoutUs=timeout_us)
            read_count = int(stream_result.ret)
            if read_count < 0:
                raise XHardwareFailure(f"SoapySDR readStream failed with code {read_count}.")
            if read_count == 0:
                raise XHardwareFailure("SoapySDR readStream returned zero samples.")
            offset += read_count

        return result

    def _sample_rates(self) -> list[float]:
        configured = self.sdr_config.get("sample_rates")
        if configured:
            return [float(rate) for rate in configured]

        try:
            return [float(rate) for rate in self.device.listSampleRates(self._SOAPY_SDR_RX, self.channel)]
        except Exception:
            return []
