from types import SimpleNamespace

import pytest

from models.comms import CommunicationStatus
from sdr.drivers.rtlsdr import SDR
from util.xbase import XHardwareFailure


def _connected_driver():
    driver = SDR.__new__(SDR)
    driver.sdr_config = {"device_index": 0}
    driver.rtlsdr = SimpleNamespace(device_opened=True)
    driver.connected = CommunicationStatus.ESTABLISHED
    return driver


def test_connection_check_abandons_handle_when_usb_device_is_absent(monkeypatch):
    driver = _connected_driver()
    native_handle = driver.rtlsdr
    monkeypatch.setattr(
        "sdr.drivers.rtlsdr.librtlsdr.rtlsdr_get_device_count",
        lambda: 0,
    )

    assert driver.get_comms_status() == CommunicationStatus.NOT_ESTABLISHED
    assert driver.rtlsdr is None
    assert native_handle.device_opened is False


def test_hardware_setter_is_not_called_when_usb_device_is_absent(monkeypatch):
    class NativeHandle:
        device_opened = True

        @property
        def sample_rate(self):
            raise AssertionError("native getter must not be called")

        @sample_rate.setter
        def sample_rate(self, _value):
            raise AssertionError("native setter must not be called")

    driver = _connected_driver()
    driver.rtlsdr = NativeHandle()
    monkeypatch.setattr(
        "sdr.drivers.rtlsdr.librtlsdr.rtlsdr_get_device_count",
        lambda: 0,
    )

    with pytest.raises(XHardwareFailure, match="setting sample rate"):
        driver.set_sample_rate(2_048_000)

    assert driver.connected == CommunicationStatus.NOT_ESTABLISHED
    assert driver.rtlsdr is None
