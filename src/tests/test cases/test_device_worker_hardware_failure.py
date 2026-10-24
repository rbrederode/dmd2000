from threading import Event

import pytest

from env.device import DeviceWorker
from util.xbase import XHardwareFailure


class FailingDevice:
    def __init__(self):
        self.first_started = Event()
        self.allow_failure = Event()
        self.calls = []
        self.closed = False

    def fail(self):
        self.calls.append("fail")
        self.first_started.set()
        assert self.allow_failure.wait(timeout=1.0)
        raise XHardwareFailure("USB device disappeared")

    def unsafe_after_failure(self):
        self.calls.append("unsafe_after_failure")
        return "must not run"

    def close(self):
        self.closed = True


def test_fatal_hardware_failure_rejects_queued_device_calls():
    device = FailingDevice()
    worker = DeviceWorker(lambda: device)
    worker.start()

    failed = worker.call("fail")
    assert device.first_started.wait(timeout=1.0)
    queued = worker.call("unsafe_after_failure")
    device.allow_failure.set()

    with pytest.raises(XHardwareFailure, match="USB device disappeared"):
        failed.result(timeout=1.0)
    with pytest.raises(XHardwareFailure, match="USB device disappeared"):
        queued.result(timeout=1.0)

    worker.stop()

    assert device.calls == ["fail"]
    assert device.closed is True


def test_calls_after_fatal_hardware_failure_fail_immediately():
    device = FailingDevice()
    worker = DeviceWorker(lambda: device)
    worker.start()

    failed = worker.call("fail")
    assert device.first_started.wait(timeout=1.0)
    device.allow_failure.set()
    with pytest.raises(XHardwareFailure):
        failed.result(timeout=1.0)

    subsequent = worker.call("unsafe_after_failure")
    with pytest.raises(XHardwareFailure, match="USB device disappeared"):
        subsequent.result(timeout=1.0)

    worker.stop()
    assert device.calls == ["fail"]
