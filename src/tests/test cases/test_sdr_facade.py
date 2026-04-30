from concurrent.futures import Future

import pytest
from rtlsdr.rtlsdr import LibUSBError

from models.comms import CommunicationStatus
from sdr.facade import SDR
from util.xbase import XHardwareFailure


class FailingWorker:
    def __init__(self, exc):
        self.exc = exc
        self.running = True
        self.stopped = False

    def is_running(self):
        return self.running

    def call(self, name, *args, **kwargs):
        future = Future()
        future.set_exception(self.exc)
        return future

    def stop(self):
        self.stopped = True
        self.running = False


def make_facade(worker):
    facade = SDR.__new__(SDR)
    facade._bias_t_enabled = False
    facade._worker = worker
    facade.info = {"Serial": "00000001"}
    facade.connected = CommunicationStatus.ESTABLISHED
    return facade


def test_libusb_error_is_reported_as_hardware_failure():
    worker = FailingWorker(LibUSBError(-4, "Could not get gain mode"))
    facade = make_facade(worker)

    with pytest.raises(XHardwareFailure) as err:
        facade._invoke("set_auto_gain")

    assert "SDR device disconnected or unavailable while calling set_auto_gain" in str(err.value)
    assert "Could not get gain mode" in str(err.value)
    assert facade.connected == CommunicationStatus.NOT_ESTABLISHED
    assert facade._worker is None
    assert worker.stopped is True


def test_non_hardware_errors_are_not_wrapped():
    worker = FailingWorker(ValueError("bad caller input"))
    facade = make_facade(worker)

    with pytest.raises(ValueError, match="bad caller input"):
        facade._invoke("set_auto_gain")

    assert facade.connected == CommunicationStatus.NOT_ESTABLISHED
    assert facade._worker is worker
    assert worker.stopped is False
