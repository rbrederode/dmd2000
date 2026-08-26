from datetime import datetime, timezone

import pytest

from api import protocol as dmd_protocol
from api.command import CommandAPI
from ipc.message import APIMessage
from util.xbase import XAPIValidationFailed


def make_command(
    *,
    from_system=dmd_protocol.CMD,
    to_system=dmd_protocol.DM,
    msg_type=dmd_protocol.MSG_TYPE_REQ,
    action_code=dmd_protocol.ACTION_CODE_SET,
    property_name=dmd_protocol.PROPERTY_TRACE,
    value="ON",
    status=None,
):
    api_call = {
        "msg_type": msg_type,
        "action_code": action_code,
    }
    if property_name is not None:
        api_call["property"] = property_name
    if value is not None:
        api_call["value"] = value
    if status is not None:
        api_call["status"] = status

    message = APIMessage()
    message.set_json_api_header(
        api_version="1.0",
        dt=datetime.now(timezone.utc),
        from_system=from_system,
        to_system=to_system,
        api_call=api_call,
    )
    return message.get_json_api_header()


def test_command_api_accepts_shared_trace_debug_and_resync_requests():
    api = CommandAPI()
    api.validate(make_command())
    api.validate(make_command(property_name=dmd_protocol.PROPERTY_DEBUG, value="OFF"))
    api.validate(make_command(
        action_code=dmd_protocol.ACTION_CODE_GET,
        property_name=dmd_protocol.PROPERTY_TRACE,
        value=None,
    ))
    api.validate(make_command(
        action_code=dmd_protocol.ACTION_CODE_RESYNC,
        property_name=None,
        value=None,
    ))


def test_command_api_rejects_non_command_origins_and_invalid_values():
    api = CommandAPI()

    with pytest.raises(XAPIValidationFailed):
        api.validate(make_command(from_system=dmd_protocol.TM))

    with pytest.raises(XAPIValidationFailed):
        api.validate(make_command(value="on"))

    with pytest.raises(XAPIValidationFailed):
        api.validate(make_command(property_name=dmd_protocol.PROPERTY_STATUS))


def test_command_api_accepts_response_addressed_to_command_client():
    api = CommandAPI()
    response = make_command(
        from_system=dmd_protocol.DM,
        to_system=dmd_protocol.CMD,
        msg_type=dmd_protocol.MSG_TYPE_RSP,
        status=dmd_protocol.STATUS_SUCCESS,
    )

    api.validate(response)
