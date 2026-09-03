from types import MethodType

from api import protocol as dmd_protocol
from api import tm_dig
from dig.dig import Digitiser
from models.comms import CommunicationStatus
from models.dig import DigitiserModel
from util.xbase import XHardwareFailure


class AutoGainSdr:
    def __init__(self, digitiser, *, fail=False):
        self.digitiser = digitiser
        self.fail = fail
        self.load_states_seen = []

    def set_auto_gain(self, **kwargs):
        self.load_states_seen.append(self.digitiser.dig_model.load_active)
        if self.fail:
            raise XHardwareFailure("auto gain failed")
        return 34.0


class ResetSdr:
    def __init__(self):
        self.reset_calls = 0

    def stream_reset(self):
        self.reset_calls += 1
        return 123


def make_digitiser(load_active=True, fail=False):
    digitiser = Digitiser.__new__(Digitiser)
    digitiser.stop = MethodType(lambda self: None, digitiser)
    digitiser.dig_model = DigitiserModel(
        dig_id="dig001",
        load_active=load_active,
        sdr_connected=CommunicationStatus.ESTABLISHED,
    )
    digitiser.app_model = digitiser.dig_model.app
    digitiser.load_state_changes = []

    def set_load_active(self, value):
        load_active = bool(value)
        self.load_state_changes.append(load_active)
        self.dig_model.load_active = load_active

    digitiser.set_load_active = MethodType(set_load_active, digitiser)
    digitiser.sdr = AutoGainSdr(digitiser, fail=fail)
    return digitiser


def test_set_auto_gain_temporarily_disables_load_and_restores_it():
    digitiser = make_digitiser(load_active=True)

    status, message, value, payload = digitiser.handle_method_call(
        {"method": tm_dig.METHOD_SET_AUTO_GAIN, "params": {"time_in_secs": 0.5}}
    )

    assert status == dmd_protocol.STATUS_SUCCESS
    assert value == 34.0
    assert payload is None
    assert digitiser.sdr.load_states_seen == [False]
    assert digitiser.load_state_changes == [False, True]
    assert digitiser.dig_model.load_active is True
    assert digitiser.dig_model.gain == 34.0


def test_set_auto_gain_restores_load_after_hardware_failure():
    digitiser = make_digitiser(load_active=True, fail=True)

    status, message, value, payload = digitiser.handle_method_call(
        {"method": tm_dig.METHOD_SET_AUTO_GAIN, "params": {"time_in_secs": 0.5}}
    )

    assert status == dmd_protocol.STATUS_ERROR
    assert "auto gain failed" in message
    assert value is None
    assert payload is None
    assert digitiser.sdr.load_states_seen == [False]
    assert digitiser.load_state_changes == [False, True]
    assert digitiser.dig_model.load_active is True


def test_stopping_scan_resets_stream_buffer():
    digitiser = Digitiser.__new__(Digitiser)
    digitiser.stop = MethodType(lambda self: None, digitiser)
    digitiser.dig_model = DigitiserModel(dig_id="dig001", scanning={"obs_id": "obs001"})
    digitiser.sdr = ResetSdr()

    digitiser.set_scanning(False)

    assert digitiser.dig_model.scanning is False
    assert digitiser.sdr.reset_calls == 1


def test_starting_scan_does_not_reset_stream_buffer():
    digitiser = Digitiser.__new__(Digitiser)
    digitiser.stop = MethodType(lambda self: None, digitiser)
    digitiser.dig_model = DigitiserModel(dig_id="dig001", scanning=False)
    digitiser.sdr = ResetSdr()

    scanning = {"obs_id": "obs001"}
    digitiser.set_scanning(scanning)

    assert digitiser.dig_model.scanning == scanning
    assert digitiser.sdr.reset_calls == 0
