import pytest
import numpy as np
import time

from models.dig import DigitiserModel
from sdr.sdr import SDR, _normalise_sdr_type
from util.xbase import XSoftwareFailure


def test_digitiser_model_defaults_to_rtlsdr_backend():
    dig = DigitiserModel(dig_id="dig001")

    assert dig.sdr_type == "rtlsdr"
    assert dig.sdr_config == {}


def test_digitiser_model_loads_legacy_dict_without_sdr_fields():
    dig = DigitiserModel.from_dict(
        {
            "_type": "DigitiserModel",
            "dig_id": "dig001",
            "load_active": False,
            "gain": 0.0,
            "sample_rate": 0.0,
            "bandwidth": 0.0,
            "center_freq": 0.0,
            "freq_correction": 0,
            "channels": 0,
            "scan_duration": 0,
            "scanning": False,
            "sdr_eeprom": {},
            "last_update": {"_type": "datetime", "value": "2026-01-01T00:00:00+00:00"},
        }
    )

    assert dig.sdr_type == "rtlsdr"
    assert dig.sdr_config == {}


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("rtlsdr", "rtlsdr"),
        ("soapy", "soapy"),
        ("airspy", "airspy"),
        ("gqrxraw", "gqrxraw"),
        ("airstream", "airstream"),
        ("rtlstream", "rtlstream"),
    ],
)
def test_sdr_type_aliases(configured, expected):
    assert _normalise_sdr_type(configured) == expected


@pytest.mark.parametrize(
    "configured",
    [
        "rtl",
        "rtl2832",
        "RTL-SDR",
        "rtl_sdr",
        "soapysdr",
        "gqrx",
        "gqrx_replay",
        "gqrxreplay",
        "stream",
        "soapy_stream",
        "soapystream",
        "rtlsdr_stream",
        "rtlsdrstream",
        "not-a-real-backend",
    ],
)
def test_unknown_sdr_type_is_rejected_before_driver_import(configured):
    with pytest.raises(XSoftwareFailure):
        SDR(sdr_type=configured)


def test_gqrxraw_driver_reads_complex64_and_pads_after_eof(tmp_path):
    iq_file = tmp_path / "capture.raw"
    samples = (np.arange(8, dtype=np.float32) + 1j * np.arange(20, 28, dtype=np.float32)).astype(np.complex64)
    samples.tofile(iq_file)

    sdr = SDR(
        sdr_type="gqrxraw",
        sdr_config={
            "iq_file": str(iq_file),
            "sample_rate": 10,
            "read_samples": 5,
            "realtime": False,
        },
    )

    _, first = sdr.read_samples()
    assert np.array_equal(first, samples[:5])

    metadata, eof_boundary = sdr.read_samples()
    expected_eof_boundary = np.zeros(5, dtype=np.complex64)
    expected_eof_boundary[:3] = samples[5:]
    assert metadata["sample_offset"] == 5
    assert np.array_equal(eof_boundary, expected_eof_boundary)

    metadata, after_eof = sdr.read_samples()
    assert metadata["sample_offset"] == 10
    assert np.array_equal(after_eof, np.zeros(5, dtype=np.complex64))

    sdr.close()


def test_gqrxraw_driver_primes_cache_before_starting_playback_clock(tmp_path, monkeypatch):
    iq_file = tmp_path / "capture.raw"
    np.arange(20, dtype=np.complex64).tofile(iq_file)

    sdr = SDR(
        sdr_type="gqrxraw",
        sdr_config={
            "iq_file": str(iq_file),
            "sample_rate": 10,
            "read_samples": 5,
            "cache_samples": 10,
            "realtime": False,
        },
    )

    monotonic_values = iter([100.0, 103.0])
    monkeypatch.setattr("sdr.drivers.gqrx.time.monotonic", lambda: next(monotonic_values))

    metadata, samples = sdr.read_samples()
    assert metadata["sample_offset"] == 0
    assert np.array_equal(samples, np.arange(5, dtype=np.complex64))
    assert sdr.driver.playback_start_monotonic == 100.0

    sdr.close()


def test_gqrxraw_driver_metadata_follows_sample_cursor_not_call_time(tmp_path, monkeypatch):
    iq_file = tmp_path / "capture.raw"
    np.arange(20, dtype=np.complex64).tofile(iq_file)

    sdr = SDR(
        sdr_type="gqrxraw",
        sdr_config={
            "iq_file": str(iq_file),
            "sample_rate": 10,
            "read_samples": 5,
            "cache_samples": 10,
            "realtime": False,
        },
    )

    time_values = iter([1_000.0, 9_999.0])
    monotonic_values = iter([100.0, 200.0])
    monkeypatch.setattr("sdr.drivers.gqrx.time.time", lambda: next(time_values))
    monkeypatch.setattr("sdr.drivers.gqrx.time.monotonic", lambda: next(monotonic_values))

    metadata0, samples0 = sdr.read_samples()
    metadata1, samples1 = sdr.read_samples()

    assert np.array_equal(samples0, np.arange(0, 5, dtype=np.complex64))
    assert np.array_equal(samples1, np.arange(5, 10, dtype=np.complex64))
    assert metadata0["sample_offset"] == 0
    assert metadata0["read_start"] == 1_000.0
    assert metadata0["read_end"] == 1_000.5
    assert metadata1["sample_offset"] == 5
    assert metadata1["read_start"] == metadata0["read_end"]
    assert metadata1["read_end"] == 1_001.0

    sdr.close()


@pytest.mark.parametrize(
    ("sdr_type", "driver_attribute", "expected_backend"),
    [
        ("airstream", "SoapySDRDriver", "airspy"),
        ("rtlstream", "RTLSDRDriver", "rtl"),
    ],
)
def test_stream_driver_reads_from_ring_buffer_with_contiguous_metadata(
    monkeypatch, sdr_type, driver_attribute, expected_backend
):
    from models.comms import CommunicationStatus

    class FakeDriver:
        def __init__(self, bias_t_enabled=False, sdr_config=None):
            self.sample_rate = int(sdr_config["sample_rate"])
            self.next_sample = 0

        def get_comms_status(self):
            return CommunicationStatus.ESTABLISHED

        def get_sample_rate(self):
            return self.sample_rate

        def get_eeprom_info(self):
            return {}

        def close(self):
            pass

        def _read_complex_samples(self, num_samples):
            time.sleep(0.01)
            start = self.next_sample
            self.next_sample += num_samples
            return np.arange(start, start + num_samples, dtype=np.complex64)

        def __getattr__(self, name):
            if name.startswith("get_"):
                return lambda *args, **kwargs: 0
            if name.startswith("set_"):
                return lambda *args, **kwargs: None
            raise AttributeError(name)

    monkeypatch.setattr(f"sdr.drivers.stream.{driver_attribute}", FakeDriver)
    sdr = SDR(
        sdr_type=sdr_type,
        sdr_config={
            "sample_rate": 100,
            "read_samples": 10,
            "producer_chunk_samples": 10,
            "ring_seconds": 1,
            "read_timeout_sec": 1,
        },
    )

    metadata0, samples0 = sdr.read_samples()
    metadata1, samples1 = sdr.read_samples()

    assert np.array_equal(samples0, np.arange(0, 10, dtype=np.complex64))
    assert np.array_equal(samples1, np.arange(10, 20, dtype=np.complex64))
    assert metadata0["sample_index_start"] == 0
    assert metadata0["sample_index_end"] == 10
    assert metadata1["sample_index_start"] == 10
    assert metadata1["sample_index_end"] == 20
    assert metadata0["stream_backend"] == expected_backend
    assert metadata1["read_start"] == metadata0["read_end"]
    assert metadata1["producer_read_count"] >= 2
    assert metadata1["producer_last_read_duration"] >= 0.0
    assert metadata1["producer_last_inter_read_gap"] >= 0.0
    assert metadata1["producer_expected_chunk_duration"] == 0.1
    assert 0.0 <= metadata1["ring_fill_fraction"] <= 1.0

    sdr.close()


def test_rtlstream_auto_gain_calls_hold_driver_lock(monkeypatch):
    from models.comms import CommunicationStatus

    class FakeRTLSDR:
        def __init__(self, bias_t_enabled=False, sdr_config=None):
            self.sample_rate = int(sdr_config["sample_rate"])

        def get_comms_status(self):
            return CommunicationStatus.ESTABLISHED

        def get_sample_rate(self):
            return self.sample_rate

        def get_eeprom_info(self):
            return {}

        def close(self):
            pass

        def get_auto_gain(self, sample_rate=None, time_in_secs=1, p_threshold=0.05):
            return 12.5

        def set_auto_gain(self, sample_rate=None, time_in_secs=1, p_threshold=0.05):
            return 14.4

        def __getattr__(self, name):
            if name.startswith("get_"):
                return lambda *args, **kwargs: 0
            if name.startswith("set_"):
                return lambda *args, **kwargs: None
            raise AttributeError(name)

    class RecordingLock:
        def __init__(self):
            self.entries = 0

        def __enter__(self):
            self.entries += 1

        def __exit__(self, exc_type, exc, tb):
            pass

    monkeypatch.setattr("sdr.drivers.stream.RTLSDRDriver", FakeRTLSDR)
    sdr = SDR(
        sdr_type="rtlstream",
        sdr_config={
            "sample_rate": 100,
            "read_samples": 10,
            "producer_chunk_samples": 10,
            "ring_seconds": 1,
            "read_timeout_sec": 1,
        },
    )
    recording_lock = RecordingLock()
    sdr.driver._driver_lock = recording_lock

    assert sdr.get_auto_gain(sample_rate=100, time_in_secs=1) == 12.5
    assert sdr.set_auto_gain(sample_rate=100, time_in_secs=1) == 14.4
    assert recording_lock.entries == 2

    sdr.close()


def test_airstream_driver_can_configure_ring_after_sample_rate_is_set(monkeypatch):
    from models.comms import CommunicationStatus

    class FakeAirspy:
        def __init__(self, bias_t_enabled=False, sdr_config=None):
            self.sample_rate = 0
            self.next_sample = 0

        def get_comms_status(self):
            return CommunicationStatus.ESTABLISHED

        def get_sample_rate(self):
            return self.sample_rate

        def set_sample_rate(self, value):
            self.sample_rate = int(value)

        def get_eeprom_info(self):
            return {}

        def close(self):
            pass

        def _read_complex_samples(self, num_samples):
            time.sleep(0.01)
            start = self.next_sample
            self.next_sample += num_samples
            return np.arange(start, start + num_samples, dtype=np.complex64)

        def __getattr__(self, name):
            if name.startswith("get_"):
                return lambda *args, **kwargs: 0
            if name.startswith("set_"):
                return lambda *args, **kwargs: None
            raise AttributeError(name)

    monkeypatch.setattr("sdr.drivers.stream.SoapySDRDriver", FakeAirspy)
    sdr = SDR(
        sdr_type="airstream",
        sdr_config={
            "ring_seconds": 1,
            "read_timeout_sec": 1,
        },
    )

    assert sdr.driver.sample_rate == 0
    assert sdr.driver.ring_capacity == 0

    sdr.set_sample_rate(100)
    assert sdr.driver.sample_rate == 100
    assert sdr.driver.read_sample_count == 100
    assert sdr.driver.producer_chunk_samples == 100
    assert sdr.driver.ring_capacity == 100

    metadata, samples = sdr.read_samples()
    assert metadata["num_samples"] == 100
    assert np.array_equal(samples, np.arange(100, dtype=np.complex64))

    sdr.close()


def test_stream_reset_discards_buffered_samples_and_restarts_at_live_edge(monkeypatch):
    from models.comms import CommunicationStatus

    class FakeRTLSDR:
        def __init__(self, bias_t_enabled=False, sdr_config=None):
            self.sample_rate = int(sdr_config["sample_rate"])
            self.next_sample = 0

        def get_comms_status(self):
            return CommunicationStatus.ESTABLISHED

        def get_sample_rate(self):
            return self.sample_rate

        def get_eeprom_info(self):
            return {}

        def close(self):
            pass

        def _read_complex_samples(self, num_samples):
            time.sleep(0.01)
            start = self.next_sample
            self.next_sample += num_samples
            return np.arange(start, start + num_samples, dtype=np.complex64)

        def __getattr__(self, name):
            if name.startswith("get_"):
                return lambda *args, **kwargs: 0
            if name.startswith("set_"):
                return lambda *args, **kwargs: None
            raise AttributeError(name)

    monkeypatch.setattr("sdr.drivers.stream.RTLSDRDriver", FakeRTLSDR)
    sdr = SDR(
        sdr_type="rtlstream",
        sdr_config={
            "sample_rate": 100,
            "read_samples": 10,
            "producer_chunk_samples": 10,
            "ring_seconds": 1,
            "read_timeout_sec": 1,
        },
    )

    sdr.read_samples()
    deadline = time.monotonic() + 1
    while sdr.driver.available < 20 and time.monotonic() < deadline:
        time.sleep(0.01)

    discarded = sdr.stream_reset()
    live_edge = sdr.driver.driver.next_sample

    assert discarded >= 20
    assert sdr.driver.available == 0
    assert sdr.driver._producer_thread is None
    assert sdr.driver._last_metadata_end is None

    _, samples = sdr.read_samples()
    assert np.array_equal(samples, np.arange(live_edge, live_edge + 10, dtype=np.complex64))

    sdr.close()
