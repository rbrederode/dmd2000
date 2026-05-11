from util.xbase import EXC_ID_HARDWARE_FAILURE, XBase, XHardwareFailure


def test_xbase_string_uses_human_readable_message():
    err = XHardwareFailure("SDR device disconnected")

    assert err.message == "SDR device disconnected"
    assert str(err) == "SDR device disconnected"


def test_xbase_string_falls_back_to_exception_metadata_without_message():
    err = XBase(EXC_ID_HARDWARE_FAILURE, data=b"abc")

    assert err.message == ""
    assert str(err) == "XBase(id=5, data=[3])"
