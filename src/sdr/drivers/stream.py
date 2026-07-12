from __future__ import annotations

import math
import threading
import time

import numpy as np

from models.comms import CommunicationStatus
from util.xbase import XHardwareFailure, XSoftwareFailure

import logging

logger = logging.getLogger(__name__)

SoapySDRDriver = None
RTLSDRDriver = None


class SDR:
    """Streaming SDR driver with a producer ring buffer backed by a hardware SDR driver."""

    def __init__(self, bias_t_enabled=False, sdr_config=None):
        self.sdr_config = sdr_config or {}
        self.stream_backend = _normalise_stream_backend(self.sdr_config.get("stream_backend", "airspy"))
        self.driver = _create_driver(self.stream_backend, bias_t_enabled=bias_t_enabled, sdr_config=self.sdr_config)

        self.connected = self.driver.get_comms_status()
        self.read_counter = 0

        self.ring_seconds = float(self.sdr_config.get("ring_seconds", 10.0))
        self.read_timeout_sec = float(self.sdr_config.get("read_timeout_sec", 10.0))
        self.gap_warn_sec = self.sdr_config.get("gap_warn_sec")
        self.ring_fill_warn_fraction = float(self.sdr_config.get("ring_fill_warn_fraction", 0.8))
        self.metric_log_interval_sec = float(self.sdr_config.get("metric_log_interval_sec", 5.0))

        self.sample_rate = self._driver_sample_rate()
        self.read_sample_count = 0
        self.producer_chunk_samples = 0
        self.ring_capacity = 0
        self.ring = np.empty(0, dtype=np.complex64)
        self.read_pos = 0
        self.write_pos = 0
        self.available = 0

        self.total_samples_acquired = 0          # Total samples read from the underlying SDR into the ring buffer.
        self.total_samples_consumed = 0          # Total samples handed from the ring buffer to read_samples callers.
        self.total_samples_dropped = 0           # Total unread samples overwritten because the ring buffer filled.
        self._last_metadata_end = None           # Logical end time of the previous consumer-facing sample block.
        self.producer_read_count = 0             # Number of low-level SDR reads completed by the producer thread.
        self.producer_last_samples = 0           # Number of samples returned by the most recent low-level SDR read.
        self.producer_last_read_start = None     # Wall-clock epoch timestamp when the latest low-level SDR read started.
        self.producer_last_read_end = None       # Wall-clock epoch timestamp when the latest low-level SDR read finished.
        self.producer_last_read_duration = 0.0   # Duration in seconds of the latest low-level SDR read call.
        self.producer_last_inter_read_gap = 0.0  # Gap in seconds between the previous SDR read ending and latest read starting.
        self.producer_max_inter_read_gap = 0.0   # Largest producer inter-read gap observed since the stream was configured.
        self.producer_sum_inter_read_gap = 0.0   # Sum of producer inter-read gaps, used to report the mean gap.
        self.producer_inter_read_gap_count = 0   # Number of inter-read gaps included in the mean gap calculation.
        self._producer_last_read_end_monotonic = None  # Monotonic timestamp for measuring producer inter-read gaps.
        self._last_gap_warning_monotonic = 0.0   # Last time a producer gap warning was logged.
        self._last_fill_warning_monotonic = 0.0  # Last time a high ring-fill warning was logged.
        self._last_logged_dropped_samples = 0     # Dropped-sample count at the last overflow warning.

        self._condition = threading.Condition()
        self._driver_lock = threading.Lock()
        self._producer_thread = None
        self._producer_running = False
        self._producer_error = None
        if self.sample_rate > 0:
            self._configure_ring()

    def open(self) -> bool:
        self.connected = self.driver.get_comms_status()
        return self.connected == CommunicationStatus.ESTABLISHED

    def close(self):
        self._stop_stream()
        self.driver.close()
        self.connected = CommunicationStatus.NOT_ESTABLISHED

    def get_comms_status(self) -> CommunicationStatus:
        if self._producer_error is not None:
            return CommunicationStatus.NOT_ESTABLISHED
        return self.driver.get_comms_status()

    def get_eeprom_info(self) -> dict:
        info = self.driver.get_eeprom_info() or {}
        info = dict(info)
        info["Driver"] = f"{self.stream_backend}stream"
        info["Ring Samples"] = self.ring_capacity
        return info

    def _driver_sample_rate(self) -> int:
        sample_rate = self.driver.get_sample_rate()
        return int(math.ceil(float(sample_rate))) if sample_rate else 0

    def _configure_ring(self):
        if self.sample_rate <= 0:
            raise XSoftwareFailure("Stream SDR sample_rate must be set before streaming.")

        self.read_sample_count = int(float(self.sdr_config.get("read_samples", self.sample_rate)))
        if self.read_sample_count <= 0:
            raise XSoftwareFailure("Stream SDR requires a positive read_samples value.")

        self.producer_chunk_samples = int(float(self.sdr_config.get("producer_chunk_samples", self.read_sample_count)))
        if self.producer_chunk_samples <= 0:
            raise XSoftwareFailure("Stream SDR requires a positive producer_chunk_samples value.")

        self.ring_capacity = int(max(self.read_sample_count, self.ring_seconds * self.sample_rate))
        self.ring = np.empty(self.ring_capacity, dtype=np.complex64)
        self.read_pos = 0
        self.write_pos = 0
        self.available = 0
        self.total_samples_acquired = 0
        self.total_samples_consumed = 0
        self.total_samples_dropped = 0
        self._last_metadata_end = None
        self._reset_producer_metrics()

    def _reset_producer_metrics(self):
        self.producer_read_count = 0
        self.producer_last_samples = 0
        self.producer_last_read_start = None
        self.producer_last_read_end = None
        self.producer_last_read_duration = 0.0
        self.producer_last_inter_read_gap = 0.0
        self.producer_max_inter_read_gap = 0.0
        self.producer_sum_inter_read_gap = 0.0
        self.producer_inter_read_gap_count = 0
        self._producer_last_read_end_monotonic = None
        self._last_gap_warning_monotonic = 0.0
        self._last_fill_warning_monotonic = 0.0
        self._last_logged_dropped_samples = 0

    def _start_stream(self):
        if self._producer_thread is not None and self._producer_thread.is_alive():
            return
        if self.sample_rate <= 0 or self.ring_capacity <= 0:
            raise XSoftwareFailure("Stream SDR sample_rate must be set before streaming.")

        self._producer_error = None
        self._producer_running = True
        self._producer_thread = threading.Thread(target=self._producer_loop, name="sdr-stream-producer", daemon=True)
        self._producer_thread.start()
        logger.info(
            "Stream SDR producer started with %s backend, ring %.2f s (%d samples), chunk %d samples.",
            self.stream_backend,
            self.ring_capacity / self.sample_rate,
            self.ring_capacity,
            self.producer_chunk_samples,
        )

    def _stop_stream(self):
        self._producer_running = False
        with self._condition:
            self._condition.notify_all()
        if self._producer_thread is not None:
            self._producer_thread.join(timeout=2.0)
        self._producer_thread = None

    def _producer_loop(self):
        while self._producer_running:
            try:
                read_start_monotonic = time.monotonic()
                read_start_epoch = time.time()
                with self._driver_lock:
                    samples = self.driver._read_complex_samples(self.producer_chunk_samples)
                read_end_monotonic = time.monotonic()
                read_end_epoch = time.time()
                self._record_producer_read_metrics(
                    sample_count=len(samples),
                    read_start_epoch=read_start_epoch,
                    read_end_epoch=read_end_epoch,
                    read_start_monotonic=read_start_monotonic,
                    read_end_monotonic=read_end_monotonic,
                )
                self._write_ring(np.asarray(samples, dtype=np.complex64))
            except Exception as exc:
                self._producer_error = exc
                self.connected = CommunicationStatus.NOT_ESTABLISHED
                logger.exception("Stream SDR producer failed: %s", exc)
                with self._condition:
                    self._condition.notify_all()
                return

    def _record_producer_read_metrics(
        self,
        sample_count: int,
        read_start_epoch: float,
        read_end_epoch: float,
        read_start_monotonic: float,
        read_end_monotonic: float,
    ):
        with self._condition:
            inter_read_gap = 0.0
            if self._producer_last_read_end_monotonic is not None:
                inter_read_gap = max(0.0, read_start_monotonic - self._producer_last_read_end_monotonic)
                self.producer_sum_inter_read_gap += inter_read_gap
                self.producer_inter_read_gap_count += 1
                self.producer_max_inter_read_gap = max(self.producer_max_inter_read_gap, inter_read_gap)

            self.producer_read_count += 1
            self.producer_last_samples = int(sample_count)
            self.producer_last_read_start = read_start_epoch
            self.producer_last_read_end = read_end_epoch
            self.producer_last_read_duration = max(0.0, read_end_monotonic - read_start_monotonic)
            self.producer_last_inter_read_gap = inter_read_gap
            self._producer_last_read_end_monotonic = read_end_monotonic
        self._log_gap_warning_if_needed(inter_read_gap, read_start_monotonic)

    def _write_ring(self, samples: np.ndarray):
        if samples.size == 0:
            return

        overflow = 0
        ring_fill_fraction = 0.0
        with self._condition:
            if samples.size >= self.ring_capacity:
                overflow = self.available + max(0, samples.size - self.ring_capacity)
                samples = samples[-self.ring_capacity:]
                self.read_pos = 0
                self.write_pos = 0
                self.available = 0
                self.total_samples_dropped += overflow

            else:
                overflow = max(0, self.available + samples.size - self.ring_capacity)
            if overflow and samples.size < self.ring_capacity:
                self.read_pos = (self.read_pos + overflow) % self.ring_capacity
                self.available -= overflow
                self.total_samples_dropped += overflow

            first = min(samples.size, self.ring_capacity - self.write_pos)
            self.ring[self.write_pos: self.write_pos + first] = samples[:first]
            remaining = samples.size - first
            if remaining:
                self.ring[:remaining] = samples[first:]

            self.write_pos = (self.write_pos + samples.size) % self.ring_capacity
            self.available += samples.size
            self.total_samples_acquired += samples.size
            ring_fill_fraction = self.available / self.ring_capacity if self.ring_capacity > 0 else 0.0
            self._condition.notify_all()
        self._log_ring_warning_if_needed(overflow, ring_fill_fraction)

    def _gap_warning_threshold_sec(self) -> float:
        if self.gap_warn_sec is not None:
            return float(self.gap_warn_sec)
        expected_chunk_duration = self.producer_chunk_samples / self.sample_rate if self.sample_rate > 0 else 0.0
        return max(0.010, expected_chunk_duration * 0.01)

    def _log_gap_warning_if_needed(self, inter_read_gap: float, now_monotonic: float):
        if inter_read_gap <= self._gap_warning_threshold_sec():
            return
        if now_monotonic - self._last_gap_warning_monotonic < self.metric_log_interval_sec:
            return

        self._last_gap_warning_monotonic = now_monotonic
        logger.warning(
            "Stream SDR producer inter-read gap %.6f s exceeds threshold %.6f s "
            "(backend=%s, read_count=%d, max_gap=%.6f s, last_read_duration=%.6f s, dropped=%d).",
            inter_read_gap,
            self._gap_warning_threshold_sec(),
            self.stream_backend,
            self.producer_read_count,
            self.producer_max_inter_read_gap,
            self.producer_last_read_duration,
            self.total_samples_dropped,
        )

    def _log_ring_warning_if_needed(self, overflow: int, ring_fill_fraction: float):
        now_monotonic = time.monotonic()

        if self.total_samples_dropped > self._last_logged_dropped_samples:
            newly_dropped = self.total_samples_dropped - self._last_logged_dropped_samples
            self._last_logged_dropped_samples = self.total_samples_dropped
            logger.warning(
                "Stream SDR ring buffer dropped %d samples (%d total); consumer is behind producer "
                "(backend=%s, overflow=%d, fill=%.1f%%, acquired=%d, consumed=%d).",
                newly_dropped,
                self.total_samples_dropped,
                self.stream_backend,
                overflow,
                ring_fill_fraction * 100.0,
                self.total_samples_acquired,
                self.total_samples_consumed,
            )
            return

        if ring_fill_fraction < self.ring_fill_warn_fraction:
            return
        if now_monotonic - self._last_fill_warning_monotonic < self.metric_log_interval_sec:
            return

        self._last_fill_warning_monotonic = now_monotonic
        logger.warning(
            "Stream SDR ring buffer %.1f%% full; consumer may be falling behind producer "
            "(backend=%s, available=%d, capacity=%d, dropped=%d).",
            ring_fill_fraction * 100.0,
            self.stream_backend,
            self.available,
            self.ring_capacity,
            self.total_samples_dropped,
        )

    def _read_ring(self, num_samples: int) -> np.ndarray:
        deadline = time.monotonic() + self.read_timeout_sec
        with self._condition:
            while self.available < num_samples and self._producer_error is None and self._producer_running:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise XHardwareFailure(
                        f"Stream SDR timed out waiting for {num_samples} samples; {self.available} available."
                    )
                self._condition.wait(timeout=remaining)

            if self._producer_error is not None:
                raise XHardwareFailure(f"Stream SDR producer failed: {self._producer_error}") from self._producer_error
            if self.available < num_samples:
                raise XHardwareFailure(
                    f"Stream SDR stopped before {num_samples} samples were available; {self.available} available."
                )

            result = np.empty(num_samples, dtype=np.complex64)
            first = min(num_samples, self.ring_capacity - self.read_pos)
            result[:first] = self.ring[self.read_pos: self.read_pos + first]
            remaining = num_samples - first
            if remaining:
                result[first:] = self.ring[:remaining]

            self.read_pos = (self.read_pos + num_samples) % self.ring_capacity
            self.available -= num_samples
            sample_index_start = self.total_samples_consumed + self.total_samples_dropped
            self.total_samples_consumed += num_samples
            dropped = self.total_samples_dropped

        return result, sample_index_start, dropped

    def _producer_metrics_snapshot(self) -> dict:
        with self._condition:
            mean_gap = (
                self.producer_sum_inter_read_gap / self.producer_inter_read_gap_count
                if self.producer_inter_read_gap_count
                else 0.0
            )
            ring_available_samples = self.available
            return {
                "producer_read_count": self.producer_read_count,
                "producer_last_samples": self.producer_last_samples,
                "producer_last_read_start": self.producer_last_read_start,
                "producer_last_read_end": self.producer_last_read_end,
                "producer_last_read_duration": self.producer_last_read_duration,
                "producer_last_inter_read_gap": self.producer_last_inter_read_gap,
                "producer_mean_inter_read_gap": mean_gap,
                "producer_max_inter_read_gap": self.producer_max_inter_read_gap,
                "producer_expected_chunk_duration": (
                    self.producer_chunk_samples / self.sample_rate if self.sample_rate > 0 else 0.0
                ),
                "ring_available_samples": ring_available_samples,
                "ring_fill_fraction": (
                    ring_available_samples / self.ring_capacity if self.ring_capacity > 0 else 0.0
                ),
            }

    def read_samples(self) -> (dict, np.ndarray):
        if self.get_comms_status() != CommunicationStatus.ESTABLISHED:
            logger.warning("Stream SDR not connected.")
            return None, None

        self._start_stream()
        samples, sample_index_start, dropped = self._read_ring(self.read_sample_count)

        block_duration = samples.size / self.sample_rate
        if self._last_metadata_end is None:
            read_end = time.time()
            read_start = read_end - block_duration
        else:
            read_start = self._last_metadata_end
            read_end = read_start + block_duration
        self._last_metadata_end = read_end

        self.read_counter += 1
        metadata = {
            "read_counter": self.read_counter,
            "num_samples": samples.size,
            "read_start": read_start,
            "read_end": read_end,
            "stream_backend": self.stream_backend,
            "sample_index_start": sample_index_start,
            "sample_index_end": sample_index_start + samples.size,
            "dropped_samples": dropped,
        }
        metadata.update(self._producer_metrics_snapshot())
        return metadata, samples

    def read_bytes(self):
        metadata, samples = self.read_samples()
        if samples is None:
            return metadata, None
        return metadata, samples.tobytes()

    def stabilise(self, sample_rate=2.4e6, time_in_secs=5):
        return self.driver.stabilise(sample_rate=sample_rate, time_in_secs=time_in_secs)

    def get_gain_gaussianity(self, sample_rate=None, time_in_secs=1):
        return self.driver.get_gain_gaussianity(sample_rate=sample_rate, time_in_secs=time_in_secs)

    def get_auto_gain(self, sample_rate=None, time_in_secs=1, p_threshold=0.05):
        with self._driver_lock:
            return self.driver.get_auto_gain(sample_rate=sample_rate, time_in_secs=time_in_secs, p_threshold=p_threshold)

    def set_auto_gain(self, sample_rate=None, time_in_secs=1, p_threshold=0.05):
        with self._driver_lock:
            return self.driver.set_auto_gain(sample_rate=sample_rate, time_in_secs=time_in_secs, p_threshold=p_threshold)

    def get_center_freq(self):
        return self.driver.get_center_freq()

    def set_center_freq(self, value):
        with self._driver_lock:
            return self.driver.set_center_freq(value)

    def get_sample_rate(self):
        return self.driver.get_sample_rate()

    def set_sample_rate(self, value):
        if self._producer_thread is not None and self._producer_thread.is_alive():
            raise XSoftwareFailure("Stream SDR sample_rate cannot be changed while streaming.")
        with self._driver_lock:
            result = self.driver.set_sample_rate(value)
        self.sample_rate = self._driver_sample_rate()
        self._configure_ring()
        return result

    def get_bandwidth(self):
        return self.driver.get_bandwidth()

    def set_bandwidth(self, value):
        with self._driver_lock:
            return self.driver.set_bandwidth(value)

    def get_gain(self):
        return self.driver.get_gain()

    def set_gain(self, value):
        with self._driver_lock:
            return self.driver.set_gain(value)

    def get_freq_correction(self):
        return self.driver.get_freq_correction()

    def set_freq_correction(self, value):
        with self._driver_lock:
            return self.driver.set_freq_correction(value)

    def get_gains(self):
        return self.driver.get_gains()

    def get_tuner_type(self):
        return self.driver.get_tuner_type()

    def set_direct_sampling(self, value):
        with self._driver_lock:
            return self.driver.set_direct_sampling(value)


def _normalise_stream_backend(value: str | None) -> str:
    if value is None or str(value).strip() == "":
        return "airspy"

    normalised = str(value).strip().lower()
    aliases = {
        "airspy": "airspy",
        "rtl": "rtl",
    }

    if normalised not in aliases:
        raise XSoftwareFailure(f"Unsupported stream SDR backend: {value}")

    return aliases[normalised]


def _create_driver(stream_backend: str, bias_t_enabled=False, sdr_config=None):
    global SoapySDRDriver, RTLSDRDriver

    if stream_backend == "airspy":
        if SoapySDRDriver is None:
            from sdr.drivers.soapy import SDR as SoapySDRDriver
        return SoapySDRDriver(bias_t_enabled=bias_t_enabled, sdr_config=sdr_config)

    if stream_backend == "rtl":
        if RTLSDRDriver is None:
            from sdr.drivers.rtlsdr import SDR as RTLSDRDriver
        return RTLSDRDriver(bias_t_enabled=bias_t_enabled, sdr_config=sdr_config)

    raise XSoftwareFailure(f"Unsupported stream SDR backend: {stream_backend}")
