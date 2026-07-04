from __future__ import annotations

from env.device import DeviceWorker
from models.comms import CommunicationStatus
from sdr.sdr import SDR as LegacySDR
from util.xbase import XHardwareFailure

import logging

logger = logging.getLogger(__name__)

_SDR_METHODS = {
    "close",
    "get_comms_status",
    "get_eeprom_info",
    "stabilise",
    "get_gain_gaussianity",
    "get_auto_gain",
    "set_auto_gain",
    "get_center_freq",
    "set_center_freq",
    "get_sample_rate",
    "set_sample_rate",
    "get_bandwidth",
    "set_bandwidth",
    "get_gain",
    "set_gain",
    "get_freq_correction",
    "set_freq_correction",
    "get_gains",
    "get_tuner_type",
    "set_direct_sampling",
    "read_bytes",
    "read_samples",
}

class SDR:
    """Thread-safe facade that serialises all low-level SDR access onto one worker thread."""

    def __init__(self, bias_t_enabled: bool = False, sdr_type: str | None = None, sdr_config: dict | None = None):
        self._bias_t_enabled = bias_t_enabled
        self._sdr_type = sdr_type
        self._sdr_config = sdr_config or {}
        self._worker: DeviceWorker | None = None
        self.info: dict | None = None
        self.connected = CommunicationStatus.NOT_ESTABLISHED
        self.open()

    def open(self) -> bool:
        """Create the worker thread and the underlying SDR instance if needed."""

        if self._worker is not None and self._worker.is_running():
            return self.connected == CommunicationStatus.ESTABLISHED

        self._worker = DeviceWorker(
            lambda: LegacySDR(
                bias_t_enabled=self._bias_t_enabled,
                sdr_type=self._sdr_type,
                sdr_config=self._sdr_config,
            )
        )
        self._worker.start()

        if self._worker.startup_error is not None:
            logger.error(f"SDR worker failed to start: {self._worker.startup_error}")
            self.connected = CommunicationStatus.NOT_ESTABLISHED
            return False

        try:
            self.info = self._invoke("get_eeprom_info")
            self.connected = self._invoke("get_comms_status")
        except XHardwareFailure:
            self.connected = CommunicationStatus.NOT_ESTABLISHED
            return False

        return self.connected == CommunicationStatus.ESTABLISHED

    def close(self):
        """Close the underlying SDR cleanly and stop the worker thread."""

        try:
            if self._worker is not None and self._worker.is_running():
                self._invoke("close", raise_on_error=False)
        finally:
            if self._worker is not None:
                self._worker.stop()
            self._worker = None
            self.connected = CommunicationStatus.NOT_ESTABLISHED

    def get_comms_status(self) -> CommunicationStatus:
        if self._worker is None or not self._worker.is_running():
            self.connected = CommunicationStatus.NOT_ESTABLISHED
            return self.connected

        try:
            self.connected = self._invoke("get_comms_status")
        except XHardwareFailure:
            self.connected = CommunicationStatus.NOT_ESTABLISHED

        return self.connected

    def get_eeprom_info(self) -> dict | None:
        if self.info is not None:
            return self.info

        if self._worker is None or not self._worker.is_running():
            return None

        try:
            self.info = self._invoke("get_eeprom_info")
        except XHardwareFailure:
            return None

        return self.info

    def __getattr__(self, name: str):
        if name.startswith("_") or name not in _SDR_METHODS:
            raise AttributeError(name)

        def _call(*args, **kwargs):
            return self._invoke(name, *args, **kwargs)

        return _call

    def _invoke(self, name: str, *args, raise_on_error: bool = True, **kwargs):
        if self._worker is None or not self._worker.is_running():
            self.connected = CommunicationStatus.NOT_ESTABLISHED
            error = XHardwareFailure(f"SDR device worker is not running for call {name}.")
            if raise_on_error:
                raise error
            return None

        future = self._worker.call(name, *args, **kwargs)

        try:
            result = future.result()
        except Exception as exc:
            self.connected = CommunicationStatus.NOT_ESTABLISHED
            hardware_error = self._hardware_failure_for(name, exc)
            if hardware_error is not None:
                self._stop_worker_after_hardware_failure(name, exc)
                if raise_on_error:
                    raise hardware_error from exc
                return None

            if raise_on_error:
                raise exc
            return None

        if name == "close":
            self.connected = CommunicationStatus.NOT_ESTABLISHED
        elif name == "get_comms_status":
            self.connected = result

        return result

    def _hardware_failure_for(self, name: str, exc: Exception) -> XHardwareFailure | None:
        if isinstance(exc, XHardwareFailure):
            return exc

        if exc.__class__.__name__ == "LibUSBError":
            return XHardwareFailure(f"SDR device disconnected or unavailable while calling {name}: {exc}")

        return None

    def _stop_worker_after_hardware_failure(self, name: str, exc: Exception) -> None:
        logger.warning(f"SDR hardware call {name} failed; marking device disconnected: {exc}")

        worker = self._worker
        self._worker = None
        self.connected = CommunicationStatus.NOT_ESTABLISHED

        if worker is not None and worker.is_running():
            try:
                worker.stop()
            except Exception as stop_exc:
                logger.warning(f"SDR worker stop after hardware failure also failed: {stop_exc}")
