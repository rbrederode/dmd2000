import logging
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
    assert second["timestamp_source"] == "host_read"


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
    receiver._write_ring(np.arange(10, dtype=np.complex64), read_start=100., read_end=100.02, completed_monotonic=time.monotonic())
    first, _ = receiver.read_samples()
    receiver._write_ring(np.arange(10, 210, dtype=np.complex64), read_start=101., read_end=101.4, completed_monotonic=time.monotonic())
    second, samples = receiver.read_samples()
    assert samples[0] == 110  # oversized producer chunk retains its newest tail
    assert second["sample_index_start"] == 110
    assert second["dropped_samples"] == 100
    assert first["read_start"] == 100.
    assert first["read_end"] == 100.02
    assert second["read_start"] == pytest.approx(101.2)
    assert second["read_end"] == pytest.approx(101.22)
    assert second["read_start"] - first["read_end"] == pytest.approx(1.18)
    assert len(receiver._read_intervals) == 1


def test_stale_samples_fail_instead_of_getting_current_timestamp(receiver, monkeypatch):
    prepare_ring(receiver, monkeypatch)
    receiver._acquisition_monotonic = time.monotonic() - 10
    receiver._write_ring(np.zeros(10, dtype=np.complex64), read_start=1., read_end=2., completed_monotonic=time.monotonic()-10)
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
             "stream_phase": 3, "timestamp_source": "host_read", "sample_age_sec": 0.1}
    advice = dig._construct_adv_to_sdp(protocol.STATUS_SUCCESS, "", value, b"", context)
    metadata = {item["property"]: item["value"] for item in advice.get_api_call()["metadata"]}
    assert metadata["scanning"]["tgt_idx"] == 1
    assert metadata["stream_phase"] == 3
    assert metadata["timestamp_source"] == "host_read"
    assert metadata["sample_age_sec"] == 0.1
    assert all(key in sdp_dig.PROPERTIES for key in metadata)


@pytest.mark.parametrize("level", [logging.DEBUG, logging.INFO])
@pytest.mark.parametrize("stale", [False, True])
def test_sample_timing_diagnostic_is_debug_only_and_precedes_stale_error(receiver, monkeypatch, caplog, level, stale):
    prepare_ring(receiver, monkeypatch)
    monkeypatch.setattr(stream.time, "monotonic", lambda: 100.)
    receiver._acquisition_monotonic = 90. if stale else 99.
    receiver._producer_last_read_end_monotonic = 99.5
    receiver.producer_read_count = 3
    receiver.producer_last_read_duration = 0.0103
    receiver.producer_last_inter_read_gap = 0.0002
    receiver._write_ring(np.arange(30, dtype=np.complex64), read_start=10., read_end=10.03, completed_monotonic=97. if stale else 99.5)
    caplog.set_level(level, logger="sdr.drivers.stream")
    if stale:
        with pytest.raises(XHardwareFailure, match="stale samples"):
            receiver.read_samples()
    else:
        metadata, samples = receiver.read_samples()
        assert samples.size == 10
        assert metadata["num_samples"] == 10
        # Diagnostic counters do not change the normal sample metadata contract.
        assert "phase_effective_sample_rate" not in metadata
    diagnostics = [record for record in caplog.records if record.msg.startswith("Stream SDR timing")]
    if level != logging.DEBUG:
        assert diagnostics == []
        return
    assert len(diagnostics) == 1
    assert f"freshness={'stale' if stale else 'ok'}" in diagnostics[0].getMessage()
    metrics = diagnostics[0].args[-1]
    assert metrics["producer_read_count"] == 3
    assert metrics["producer_last_read_duration"] == 0.0103
    assert metrics["producer_last_inter_read_gap"] == 0.0002
    assert metrics["ring_available_samples"] == 20  # Snapshot after consuming the block.
    assert metrics["ring_available_seconds"] == 0.02
    assert metrics["total_samples_consumed"] == 10
    assert metrics["total_samples_dropped"] == 0
    assert metrics["phase_elapsed_sec"] == (10. if stale else 1.)
    assert metrics["phase_acquired_samples"] == 30
    assert metrics["phase_effective_sample_rate"] == (3. if stale else 30.)
    assert metrics["producer_timeline_lag_sec"] == pytest.approx(9.97 if stale else .97)
    assert metrics["seconds_since_producer_read"] == .5
    assert metrics["read_wait_and_copy_sec"] == 0.


@pytest.mark.parametrize('backend', ['rtl', 'airspy'])
def test_slow_reads_keep_measured_wall_times_without_accumulating_staleness(receiver, monkeypatch, backend):
    prepare_ring(receiver, monkeypatch)
    receiver.stream_backend = backend
    receiver.sample_rate = receiver.read_sample_count = 1000
    receiver.producer_chunk_samples = 1000
    receiver.sdr_config.update(read_samples=1000, producer_chunk_samples=1000)
    receiver.ring_seconds = 2
    receiver._configure_ring()
    now = [100.]
    monkeypatch.setattr(stream.time, 'monotonic', lambda: now[0])
    receiver._acquisition_monotonic = now[0]
    receiver._acquisition_epoch = 1000.
    previous = None
    for index in range(180):
        start = 1000. + index*1.041  # 1.035-second read and a real .006-second gap.
        end = start+1.035
        now[0] = 100.+index*1.041+1.035
        receiver._write_ring(np.full(1000, index, dtype=np.complex64), read_start=start,
                             read_end=end, completed_monotonic=now[0])
        now[0] += .002
        metadata, samples = receiver.read_samples()
        assert metadata['read_start'] == start
        assert metadata['read_end'] == end
        assert metadata['sample_age_sec'] == pytest.approx(.002)
        assert metadata['timestamp_source'] == 'host_read'
        assert metadata['sample_index_start'] == index*1000
        assert samples[0] == index
        if previous is not None:
            assert metadata['read_start']-previous['read_end'] == pytest.approx(.006)
        previous = metadata
    assert previous['read_end']-1000. > 187.  # More than 7 seconds beyond nominal.
    assert not receiver._read_intervals  # Timestamp storage is consumed with IQ.


def test_buffered_times_survive_later_reads_and_split_or_merged_chunks(receiver, monkeypatch):
    prepare_ring(receiver, monkeypatch)
    now = [100.]
    monkeypatch.setattr(stream.time, 'monotonic', lambda: now[0])
    receiver._write_ring(np.arange(20, dtype=np.complex64), read_start=10., read_end=10.06, completed_monotonic=99.5)
    receiver._write_ring(np.arange(20,30,dtype=np.complex64), read_start=10.08, read_end=10.11, completed_monotonic=99.8)
    first, _ = receiver.read_samples()
    assert first['read_start'] == 10.
    assert first['read_end'] == pytest.approx(10.03)
    assert first['sample_age_sec'] == .5  # Uses this read, not the newest producer read.
    receiver.read_sample_count = 15
    second, samples = receiver.read_samples()
    assert second['read_start'] == pytest.approx(10.03)
    assert second['read_end'] == pytest.approx(10.095)  # Includes the .02-second gap.
    assert second['sample_age_sec'] == .5
    np.testing.assert_array_equal(samples, np.arange(10,25))
    receiver.read_sample_count = 5
    third, _ = receiver.read_samples()
    assert third['read_start'] == pytest.approx(10.095)
    assert third['read_end'] == pytest.approx(10.11)
    assert third['sample_age_sec'] == pytest.approx(.2)
    assert not receiver._read_intervals


def test_fresh_tail_cannot_hide_stale_buffered_prefix(receiver, monkeypatch):
    prepare_ring(receiver, monkeypatch)
    monkeypatch.setattr(stream.time, 'monotonic', lambda: 100.)
    receiver._write_ring(np.ones(5,dtype=np.complex64), read_start=10.,read_end=10.01,completed_monotonic=90.)
    receiver._write_ring(np.ones(5,dtype=np.complex64), read_start=20.,read_end=20.01,completed_monotonic=99.9)
    with pytest.raises(XHardwareFailure, match='stale samples'):
        receiver.read_samples()


def test_reset_discards_read_intervals_with_samples(receiver, monkeypatch):
    prepare_ring(receiver, monkeypatch)
    receiver._write_ring(np.ones(10,dtype=np.complex64), read_start=10.,read_end=10.01,completed_monotonic=time.monotonic())
    assert receiver._read_intervals
    assert receiver.stream_reset() == 10
    assert not receiver._read_intervals


def test_measured_stream_times_reach_sdp_advice_and_saved_scan_metadata(receiver, monkeypatch, tmp_path):
    from datetime import datetime
    from models.scan import ScanModel
    from obs.scan import Scan
    prepare_ring(receiver, monkeypatch)
    receiver.sample_rate = 8
    receiver.sdr_config.update(read_samples=8, producer_chunk_samples=8)
    receiver.ring_seconds = 2
    receiver._configure_ring()
    now = [100.]
    monkeypatch.setattr(stream.time, 'monotonic', lambda: now[0])
    model = ScanModel(obs_id='wall-time-test', tgt_idx=0, dig_id='dig001', duration=60,
                      sample_rate=8, spectral_resolution=4, center_freq=1420e6)
    scan = Scan(model)
    dig = digitiser()
    dig.dig_model.sample_rate = 8.0
    for index in range(60):
        start = 1794480000. + index*1.041
        end = start+1.035
        now[0] = 100.+index*1.041+1.035
        receiver._write_ring(np.ones(8,dtype=np.complex64), read_start=start, read_end=end,
                             completed_monotonic=now[0])
        value, samples = receiver.read_samples()
        advice = dig._construct_adv_to_sdp(protocol.STATUS_SUCCESS, '', value, samples.tobytes())
        metadata = {item['property']:item['value'] for item in advice.get_api_call()['metadata']}
        assert metadata['timestamp_source'] == 'host_read'
        assert scan.load_samples(index+1, samples, datetime.fromisoformat(metadata['read_start']),
                                 datetime.fromisoformat(metadata['read_end']))
    model.save_to_disk(output_dir=str(tmp_path), filename='scan.json')
    restored = ScanModel.load_from_disk(input_dir=str(tmp_path), filename='scan.json')
    assert scan.get_loaded_seconds() == 60
    assert (restored.read_end-restored.read_start).total_seconds() == pytest.approx(62.454)
    assert len(restored.sample_times) == 60
    assert restored.sample_times[0]['gap'] == 0.
    for sample in restored.sample_times:
        assert (sample['read_end']-sample['read_start']).total_seconds() == pytest.approx(1.035)
    for sample in restored.sample_times[1:]:
        assert sample['gap'] == pytest.approx(.006)


def test_ring_wrap_overwrite_discards_matching_old_timestamps(receiver, monkeypatch):
    prepare_ring(receiver, monkeypatch)
    receiver.ring_seconds = .02
    receiver._configure_ring()  # 20 samples, two consumer blocks.
    monkeypatch.setattr(stream.time, 'monotonic', lambda: 100.)
    for index in range(3):
        receiver._write_ring(np.full(10,index,dtype=np.complex64), read_start=10.+index,
                             read_end=10.5+index,completed_monotonic=99.9)
    first, samples = receiver.read_samples()
    assert first['sample_index_start'] == 10
    assert first['dropped_samples'] == 10
    assert first['read_start'] == 11.
    assert first['read_end'] == 11.5
    assert np.all(samples == 1)
    second, samples = receiver.read_samples()
    assert second['read_start'] == 12.
    assert second['read_end'] == 12.5
    assert np.all(samples == 2)
    assert not receiver._read_intervals
