from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np

from models.comms import CommunicationStatus
from util.format import fmt_bool
from util.xbase import XHardwareFailure, XSoftwareFailure

import logging

logger = logging.getLogger(__name__)


class SDR:
    """File-backed SDR driver for replaying GQRX complex64 raw IQ captures."""

    BYTES_PER_COMPLEX64 = np.dtype(np.complex64).itemsize

    def __init__(self, bias_t_enabled=False, sdr_config=None):
        self.sdr_config = sdr_config or {}
        self.connected = CommunicationStatus.NOT_ESTABLISHED
        self.read_counter = 0

        iq_file = self.sdr_config.get("iq_file", self.sdr_config.get("file"))
        self.iq_file = Path(iq_file).expanduser() if iq_file else None
        self.file_handle = None
        self.file_samples = 0
        self.playback_start_epoch = None
        self.playback_start_monotonic = None
        self.next_sample_offset = 0
        self.cache_start_sample = None
        self.cache_data = np.empty(0, dtype=np.complex64)
        self.playback_primed = False

        self.sample_rate = int(float(self.sdr_config.get("sample_rate", 6_000_000)))
        self.center_freq = float(self.sdr_config.get("center_freq", 0.0))
        self.bandwidth = float(self.sdr_config.get("bandwidth", self.sample_rate))
        self.gain = float(self.sdr_config.get("gain", 0.0))
        self.freq_correction = int(self.sdr_config.get("freq_correction", 0))
        self.read_sample_count = int(float(self.sdr_config.get("read_samples", self.sample_rate)))
        self.realtime = fmt_bool(self.sdr_config.get("realtime", True))
        self.cache_seconds = float(self.sdr_config.get("cache_seconds", 2.0))
        self.cache_sample_count = 0
        self._configure_read_sizes()

        self.info = {}
        if self.open():
            logger.info(
                "GQRX replay SDR opened %s at %.3f MSPS with %d complex64 samples.",
                self.iq_file,
                self.sample_rate / 1e6,
                self.file_samples,
            )

    def open(self) -> bool:
        if self.file_handle is not None:
            return True

        if self.iq_file is None:
            raise XSoftwareFailure("GQRX replay SDR requires sdr_config.iq_file.")
        if not self.iq_file.exists() or not self.iq_file.is_file():
            raise XHardwareFailure(f"GQRX replay IQ file does not exist: {self.iq_file}")

        file_size = self.iq_file.stat().st_size
        if file_size % self.BYTES_PER_COMPLEX64 != 0:
            logger.warning(
                "GQRX replay IQ file size %d is not an exact complex64 sample multiple; trailing bytes will be ignored.",
                file_size,
            )

        self.file_samples = file_size // self.BYTES_PER_COMPLEX64
        self.file_handle = self.iq_file.open("rb")
        self.connected = CommunicationStatus.ESTABLISHED
        self.info = {
            "Driver": "gqrxraw",
            "Source": str(self.iq_file),
            "Samples": self.file_samples,
            "Sample Rate": self.sample_rate,
            "EOF Mode": "zeros",
        }
        return True

    def close(self):
        if self.file_handle is not None:
            self.file_handle.close()
        self.file_handle = None
        self.connected = CommunicationStatus.NOT_ESTABLISHED
        logger.info("GQRX replay SDR closed.")

    def get_comms_status(self) -> CommunicationStatus:
        return self.connected

    def get_eeprom_info(self) -> dict:
        return self.info

    def stabilise(self, sample_rate=2.4e6, time_in_secs=5):
        logger.info("GQRX replay SDR stabilise requested; no hardware warm-up required.")

    def _ensure_open(self):
        if self.file_handle is None:
            raise XHardwareFailure("GQRX replay SDR file is not open.")

    def _prime_playback(self):
        if self.playback_primed:
            return False
        self._fill_cache(0, self.read_sample_count)
        self.playback_start_epoch = time.time()
        self.playback_start_monotonic = time.monotonic()
        self.next_sample_offset = 0
        self.playback_primed = True
        return True

    def _configure_read_sizes(self):
        if self.sample_rate <= 0:
            raise XSoftwareFailure("GQRX replay SDR requires a positive sample_rate.")
        if self.read_sample_count <= 0:
            raise XSoftwareFailure("GQRX replay SDR requires a positive read_samples value.")

        configured_cache_samples = self.sdr_config.get("cache_samples")
        if configured_cache_samples is not None:
            self.cache_sample_count = int(float(configured_cache_samples))
        else:
            self.cache_sample_count = int(max(self.read_sample_count, self.cache_seconds * self.sample_rate))

        if self.cache_sample_count <= 0:
            raise XSoftwareFailure("GQRX replay SDR requires a positive cache size.")
        self.cache_sample_count = max(self.cache_sample_count, self.read_sample_count)
        self.cache_start_sample = None
        self.cache_data = np.empty(0, dtype=np.complex64)

    def _cache_contains(self, sample_offset: int, num_samples: int) -> bool:
        if self.cache_start_sample is None:
            return False
        cache_end = self.cache_start_sample + self.cache_data.size
        return self.cache_start_sample <= sample_offset and sample_offset + num_samples <= cache_end

    def _fill_cache(self, sample_offset: int, min_samples: int):
        self._ensure_open()

        cache_count = max(self.cache_sample_count, min_samples)
        self.cache_start_sample = sample_offset
        self.cache_data = np.zeros(cache_count, dtype=np.complex64)

        if sample_offset >= self.file_samples:
            return

        available = min(cache_count, self.file_samples - sample_offset)
        byte_offset = sample_offset * self.BYTES_PER_COMPLEX64
        self.file_handle.seek(byte_offset)
        data = np.fromfile(self.file_handle, dtype=np.complex64, count=available)
        self.cache_data[: data.size] = data

    def _read_complex64_at(self, sample_offset: int, num_samples: int) -> np.ndarray:
        self._ensure_open()

        if not self._cache_contains(sample_offset, num_samples):
            self._fill_cache(sample_offset, num_samples)

        cache_offset = sample_offset - self.cache_start_sample
        return self.cache_data[cache_offset: cache_offset + num_samples].copy()

    def read_samples(self) -> (dict, np.ndarray):
        if self.file_handle is None:
            logger.warning("GQRX replay SDR file not open.")
            return None, None

        self._prime_playback()
        sample_offset = self.next_sample_offset
        x = self._read_complex64_at(sample_offset, self.read_sample_count)
        self.next_sample_offset += x.size

        read_start = self.playback_start_epoch + (sample_offset / self.sample_rate)
        read_end = self.playback_start_epoch + ((sample_offset + x.size) / self.sample_rate)
        if self.realtime:
            target_end = self.playback_start_monotonic + ((sample_offset + self.read_sample_count) / self.sample_rate)
            time.sleep(max(0.0, target_end - time.monotonic()))

        self.read_counter += 1
        metadata = {
            "read_counter": self.read_counter,
            "num_samples": x.size,
            "read_start": read_start,
            "read_end": read_end,
            "sample_offset": sample_offset,
            "source_file": str(self.iq_file),
        }
        return metadata, x

    def read_bytes(self) -> (dict, bytes):
        metadata, samples = self.read_samples()
        if samples is None:
            return metadata, None
        return metadata, samples.tobytes()

    def get_gain_gaussianity(self, sample_rate=None, time_in_secs=1):
        return True, (1.0, 1.0)

    def get_auto_gain(self, sample_rate=None, time_in_secs=1, p_threshold=0.05):
        return self.gain

    def set_auto_gain(self, sample_rate=None, time_in_secs=1, p_threshold=0.05):
        return self.gain

    def get_center_freq(self):
        return self.center_freq

    def set_center_freq(self, value):
        self.center_freq = float(value)

    def get_sample_rate(self):
        return self.sample_rate

    def set_sample_rate(self, value):
        self.sample_rate = int(math.ceil(float(value)))
        self.read_sample_count = int(float(self.sdr_config.get("read_samples", self.sample_rate)))
        self._configure_read_sizes()

    def get_bandwidth(self):
        return self.bandwidth

    def set_bandwidth(self, value):
        self.bandwidth = float(value)

    def get_gain(self):
        return self.gain

    def set_gain(self, value):
        self.gain = float(value)

    def get_freq_correction(self):
        return self.freq_correction

    def set_freq_correction(self, value):
        self.freq_correction = int(value)

    def get_gains(self):
        configured = self.sdr_config.get("gains")
        if configured:
            return [float(gain) for gain in configured]
        return [self.gain]

    def get_tuner_type(self):
        return "GQRX replay"

    def set_direct_sampling(self, value):
        pass
