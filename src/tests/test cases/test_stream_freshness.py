import threading
import time
from types import MethodType, SimpleNamespace

import numpy as np
import pytest

from api import protocol, sdp_dig
from dig.dig import Digitiser
from models.comms import CommunicationStatus
from models.dig import DigitiserModel
from sdr.drivers import stream
from sdr.drivers.soapy import SDR as SoapySDR
from util.xbase import XHardwareFailure


class BufferedBackend:
    """An idle hardware FIFO retains the previous phase until restarted."""

    def __init__(self, **kwargs):
        self.phase = 0
        self.active = True
        self.reads = []

    def get_sample_rate(self):
        return 1000

    def get_comms_status(self):
        return CommunicationStatus.ESTABLISHED

    def stop_acquisition(self):
        self.active = False

    def start_acquisition(self):
        assert not self.active
        self.active = True
        self.phase += 1

    def _read_complex_samples(self, count):
        assert self.active
        time.sleep(count / 1000)
        self.reads.append((self.phase, count))
        return np.full(count, self.phase, dtype=np.complex64)

    def set_auto_gain(self, **kwargs):
        self.start_acquisition()
        self._read_complex_samples(10)
        return 42

    def close(self):
        self.active = False


@pytest.fixture
def receiver(monkeypatch):
    monkeypatch.setattr(stream, "SoapySDRDriver", BufferedBackend)
    receiver = stream.SDR(sdr_config={
        "sample_rate": 1000, "read_samples": 10,
        "producer_chunk_samples": 10, "settle_seconds": 0.02,
        "ring_seconds": 0.1,
    })
    try:
        yield receiver
    finally:
        receiver.close()


def test_restart_discards_hardware_fifo_and_settling_samples(receiver):
    first, samples = receiver.read_samples()
    assert np.all(samples == 1)
    assert receiver.driver.reads[:3] == [(1, 10)] * 3  # settling, settling, recording
    receiver.stream_reset()
    assert not receiver.driver.active
    time.sleep(0.05)  # represents target acquisition while the hardware is idle
    restart = time.time()
    second, samples = receiver.read_samples()
    assert np.all(samples == 2)
    assert second["read_counter"] == first["read_counter"] + 1
    assert second["stream_phase"] == first["stream_phase"] + 1
    assert second["read_start"] >= restart
    assert second["read_start"] > first["read_end"] + 0.04
    assert second["timestamp_source"] == "host_estimated"


def test_auto_gain_samples_cannot_seed_recording(receiver):
    receiver.read_samples()
    assert receiver.set_auto_gain() == 42
    assert not receiver.driver.active
    _, samples = receiver.read_samples()
    assert np.all(samples == 3)  # phase 2 was calibration only


def prepare_ring(receiver, monkeypatch):
    # Exercise timing/index arithmetic without a producer racing the test.
    monkeypatch.setattr(receiver, "_start_stream", lambda: None)
    receiver._producer_running = True
    receiver._acquisition_epoch = time.time() - 1
    receiver._acquisition_monotonic = time.monotonic() - 1


def test_overflow_advances_sample_indexes_and_timestamps(receiver, monkeypatch):
    prepare_ring(receiver, monkeypatch)
    receiver._write_ring(np.arange(10, dtype=np.complex64))
    first, _ = receiver.read_samples()
    receiver._write_ring(np.arange(10, 210, dtype=np.complex64))
    second, samples = receiver.read_samples()
    assert samples[0] == 110  # oversized producer chunk retains its newest tail
    assert second["sample_index_start"] == 110
    assert second["dropped_samples"] == 100
    assert second["read_start"] - first["read_end"] == pytest.approx(0.1, abs=1e-6)


def test_stale_samples_fail_instead_of_getting_current_timestamp(receiver, monkeypatch):
    prepare_ring(receiver, monkeypatch)
    receiver._acquisition_monotonic = time.monotonic() - 10
    receiver._write_ring(np.zeros(10, dtype=np.complex64))
    with pytest.raises(XHardwareFailure, match="stale samples"):
        receiver.read_samples()


def test_backend_stop_failure_is_a_hardware_error(receiver, monkeypatch):
    def fail():
        raise RuntimeError("deactivate failed")

    monkeypatch.setattr(receiver.driver, "stop_acquisition", fail)
    with pytest.raises(XHardwareFailure, match="deactivate failed"):
        receiver.stream_reset()


def test_soapy_gain_flush_restarts_fifo_before_settling():
    driver = SoapySDR.__new__(SoapySDR)
    calls = []
    driver.device = SimpleNamespace(
        deactivateStream=lambda stream: calls.append("stop") or 0,
        activateStream=lambda stream: calls.append("start") or 0,
    )
    driver.stream = object()
    driver._stream_active = True
    driver.sample_rate = 1000
    driver.sdr_config = {"settle_seconds": 0.02}
    driver._read_complex_samples = lambda count: calls.append(("discard", count))

    driver._reset_buffer()

    assert calls == ["stop", "start", ("discard", 20)]


@pytest.mark.parametrize("operation", ["start", "stop"])
def test_soapy_rejected_lifecycle_operation_is_reported(operation):
    driver = SoapySDR.__new__(SoapySDR)
    driver.device = SimpleNamespace(
        deactivateStream=lambda stream: -1,
        activateStream=lambda stream: -1,
    )
    driver.stream = object()
    driver._stream_active = operation == "stop"
    with pytest.raises(XHardwareFailure, match="Stream failed"):
        getattr(driver, operation + "_acquisition")()


def digitiser():
    dig = Digitiser.__new__(Digitiser)
    dig.stop = MethodType(lambda self: None, dig)
    dig.dig_model = DigitiserModel(
        dig_id="dig001", scanning={"obs_id": "obs001", "tgt_idx": 1},
        sdp_connected=CommunicationStatus.ESTABLISHED,
    )
    dig._scan_samples_generation = 1
    dig._scan_samples_generation_lock = threading.Lock()
    dig.sdp_api = sdp_dig.SDP_DIG()
    return dig


def test_phase_is_invalidated_before_blocking_reset():
    dig = digitiser()
    reset_started, release_reset = threading.Event(), threading.Event()

    def reset():
        reset_started.set()
        assert release_reset.wait(2)
        return 0

    dig.sdr = SimpleNamespace(stream_reset=reset)
    transition = threading.Thread(target=dig.set_scanning, args=(False,))
    transition.start()
    try:
        assert reset_started.wait(2)
        assert not dig._is_current_scan_samples_generation(1)
    finally:
        release_reset.set()
        transition.join(2)


def test_failed_reset_does_not_prevent_disconnect_cleanup():
    dig = digitiser()
    dig.set_last_err = lambda message: message

    def fail():
        raise XHardwareFailure("cannot stop hardware")

    dig.sdr = SimpleNamespace(stream_reset=fail)
    assert "cannot stop hardware" in dig._stop_scanning_for_cleanup()
    assert dig.dig_model.scanning is False
    assert dig.dig_model.sdr_connected == CommunicationStatus.NOT_ESTABLISHED
    assert not dig._is_current_scan_samples_generation(1)


def test_read_finishing_after_transition_is_not_sent_or_rescheduled():
    dig = digitiser()

    def read(_call):
        dig._advance_scan_samples_generation()
        dig.dig_model.scanning = {"obs_id": "obs001", "tgt_idx": 2}
        return protocol.STATUS_SUCCESS, "", {}, np.ones(10, dtype=np.complex64)

    dig.handle_method_call = read
    action = dig.process_timer_event(SimpleNamespace(name="scan_samples_1", user_ref=1))
    assert action.msgs_to_remote == []
    assert action.timer_actions == []


def test_sample_advice_keeps_original_context_and_freshness_metadata():
    dig = digitiser()
    context = dig._sample_context()
    dig.dig_model.scanning = {"obs_id": "obs001", "tgt_idx": 2}
    value = {"read_counter": 1, "read_start": 1, "read_end": 2,
             "stream_phase": 3, "timestamp_source": "host_estimated", "sample_age_sec": 0.1}
    advice = dig._construct_adv_to_sdp(protocol.STATUS_SUCCESS, "", value, b"", context)
    metadata = {item["property"]: item["value"] for item in advice.get_api_call()["metadata"]}
    assert metadata["scanning"]["tgt_idx"] == 1
    assert metadata["stream_phase"] == 3
    assert metadata["timestamp_source"] == "host_estimated"
    assert metadata["sample_age_sec"] == 0.1
    assert all(key in sdp_dig.PROPERTIES for key in metadata)
