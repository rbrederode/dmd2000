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
        self.settle_seconds = float(self.sdr_config.get("settle_seconds", 0.1))
        if not math.isfinite(self.settle_seconds) or self.settle_seconds < 0:
            raise XSoftwareFailure("settle_seconds must be finite and non-negative.")
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
        self.stream_phase = 0
        self._acquisition_epoch = None
        self._acquisition_monotonic = None
        self._phase_sample_index = 0
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
        self._backend_acquisition("stop_acquisition")

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

        self.max_sample_age_sec = float(self.sdr_config.get(
            "max_sample_age_sec", max(2.0, 2 * self.read_sample_count / self.sample_rate)))
        if not math.isfinite(self.max_sample_age_sec) or self.max_sample_age_sec <= 0:
            raise XSoftwareFailure("max_sample_age_sec must be finite and positive.")

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

    def _backend_acquisition(self, method):
        hook = getattr(self.driver, method, None)
        if callable(hook):
            try:
                hook()
            except Exception as exc:
                raise XHardwareFailure(f"Stream SDR {method} failed: {exc}") from exc

    def _start_stream(self):
        if self._producer_thread is not None and self._producer_thread.is_alive():
            return
        if self.sample_rate <= 0 or self.ring_capacity <= 0:
            raise XSoftwareFailure("Stream SDR sample_rate must be set before streaming.")

        # Restart the backend, including its USB/plugin FIFO, before recording.
        with self._driver_lock:
            start = getattr(self.driver, "start_acquisition", None)
            if callable(start):
                self._backend_acquisition("stop_acquisition")
                self._backend_acquisition("start_acquisition")
                remaining = int(math.ceil(self.settle_seconds * self.sample_rate))
                discarded = remaining
                while remaining:
                    count = min(remaining, self.producer_chunk_samples)
                    samples = self.driver._read_complex_samples(count)
                    if len(samples) != count:
                        raise XHardwareFailure("Stream SDR returned an incomplete settling read.")
                    remaining -= count
                logger.info("Stream SDR restarted backend=%s; discarded %d settling samples.",
                            self.stream_backend, discarded)
        self.stream_phase += 1
        self._phase_sample_index = self.total_samples_acquired
        self._acquisition_epoch = None
        self._acquisition_monotonic = None
        self._reset_producer_metrics()
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
        producer_thread = self._producer_thread
        if producer_thread is not None:
            producer_thread.join(timeout=2.0)
            if producer_thread.is_alive():
                raise XHardwareFailure("Stream SDR producer did not stop within 2 seconds.")
        self._producer_thread = None

    def stream_reset(self) -> int:
        """Stop streaming and discard samples retained from the previous scan.

        The producer is stopped before the ring is cleared so an in-flight
        hardware read cannot repopulate the ring with pre-reset samples. The
        read counter remains unchanged because SDP uses it to identify scan
        ranges.

        :return: Number of buffered samples discarded.
        """
        self._stop_stream()
        with self._driver_lock:
            self._backend_acquisition("stop_acquisition")

        with self._condition:
            discarded = self.available
            self.read_pos = self.write_pos
            self.available = 0
            self.total_samples_dropped += discarded
            self._last_logged_dropped_samples = self.total_samples_dropped
            self._last_metadata_end = None
            self._acquisition_epoch = None
            self._acquisition_monotonic = None
            self._condition.notify_all()

        logger.info(
            "Stream SDR reset stopped the producer and discarded %d buffered samples (backend=%s).",
            discarded,
            self.stream_backend,
        )
        return discarded

    def _producer_loop(self):
        while self._producer_running:
            try:
                read_start_monotonic = time.monotonic()
                read_start_epoch = time.time()
                with self._driver_lock:
                    samples = self.driver._read_complex_samples(self.producer_chunk_samples)
                read_end_monotonic = time.monotonic()
                read_end_epoch = time.time()
                if not self._producer_running:
                    return  # The read belongs to the phase that has just stopped.
                if self._acquisition_epoch is None:
                    duration = len(samples) / self.sample_rate
                    self._acquisition_epoch = read_end_epoch - duration
                    self._acquisition_monotonic = read_end_monotonic - duration
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

        acquired = samples.size
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
            self.total_samples_acquired += acquired
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

            sample_index_start = self.total_samples_acquired - self.available
            self.read_pos = (self.read_pos + num_samples) % self.ring_capacity
            self.available -= num_samples
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

        offset = (sample_index_start - self._phase_sample_index) / self.sample_rate
        read_start = self._acquisition_epoch + offset
        read_end = read_start + samples.size / self.sample_rate
        sample_age = max(0.0, time.monotonic() - (
            self._acquisition_monotonic + offset + samples.size / self.sample_rate))
        max_age = self.max_sample_age_sec
        if sample_age > max_age:
            raise XHardwareFailure(
                f"Stream SDR stale samples: age {sample_age:.3f} s exceeds {max_age:.3f} s "
                f"(phase={self.stream_phase}, sample_index={sample_index_start}, dropped={dropped}).")
        if self._last_metadata_end is None:
            logger.info("Stream SDR first samples phase=%d start=%.6f age=%.3f s (host estimated).",
                        self.stream_phase, read_start, sample_age)
        self._last_metadata_end = read_end

        self.read_counter += 1
        metadata = {
            "read_counter": self.read_counter,
            "num_samples": samples.size,
            "read_start": read_start,
            "read_end": read_end,
            "stream_backend": self.stream_backend,
            "stream_phase": self.stream_phase,
            "timestamp_source": "host_estimated",
            "sample_age_sec": sample_age,
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
        return self._auto_gain("get_auto_gain", sample_rate, time_in_secs, p_threshold)

    def set_auto_gain(self, sample_rate=None, time_in_secs=1, p_threshold=0.05):
        return self._auto_gain("set_auto_gain", sample_rate, time_in_secs, p_threshold)

    def _auto_gain(self, method, sample_rate, time_in_secs, p_threshold):
        self.stream_reset()
        try:
            with self._driver_lock:
                return getattr(self.driver, method)(
                    sample_rate=sample_rate, time_in_secs=time_in_secs, p_threshold=p_threshold)
        finally:
            # Calibration samples must never seed an observation's ring/FIFO.
            self.stream_reset()

    def get_center_freq(self):
        return self.driver.get_center_freq()

    def set_center_freq(self, value):
        self.stream_reset()
        with self._driver_lock:
            return self.driver.set_center_freq(value)

    def get_sample_rate(self):
        return self.driver.get_sample_rate()

    def set_sample_rate(self, value):
        self.stream_reset()
        with self._driver_lock:
            result = self.driver.set_sample_rate(value)
        self.sample_rate = self._driver_sample_rate()
        self._configure_ring()
        # Soapy may prime the device while applying a sample-rate change.
        self._backend_acquisition("stop_acquisition")
        return result

    def get_bandwidth(self):
        return self.driver.get_bandwidth()

    def set_bandwidth(self, value):
        self.stream_reset()
        with self._driver_lock:
            return self.driver.set_bandwidth(value)

    def get_gain(self):
        return self.driver.get_gain()

    def set_gain(self, value):
        self.stream_reset()
        with self._driver_lock:
            return self.driver.set_gain(value)

    def get_freq_correction(self):
        return self.driver.get_freq_correction()

    def set_freq_correction(self, value):
        self.stream_reset()
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
