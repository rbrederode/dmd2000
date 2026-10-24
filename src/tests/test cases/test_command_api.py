from datetime import datetime, timezone

import pytest

from api import protocol as dmd_protocol
from api.cmd_api import ACTION_CODE_COMMAND, CommandAPI
from api.cmd_registry import CommandRegistry
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
    command_name=None,
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
    if command_name is not None:
        api_call["command"] = command_name

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


def test_command_api_accepts_registered_dm_stop_request():
    registry = CommandRegistry.load(app_name=dmd_protocol.DM)
    api = CommandAPI(registry=registry)

    api.validate(make_command(
        action_code=ACTION_CODE_COMMAND,
        property_name=None,
        value="dish001",
        command_name="stop",
    ))


@pytest.mark.parametrize("property_name,value", [
    (dmd_protocol.PROPERTY_STATUS, "dish001"),
    (None, None),
    (None, ""),
    (None, "dish 001"),
])
def test_command_api_rejects_invalid_registered_stop_requests(property_name, value):
    registry = CommandRegistry.load(app_name=dmd_protocol.DM)
    api = CommandAPI(registry=registry)

    with pytest.raises(XAPIValidationFailed):
        api.validate(make_command(
            action_code=ACTION_CODE_COMMAND,
            property_name=property_name,
            value=value,
            command_name="stop",
        ))


def test_command_api_does_not_offer_stop_to_other_apps():
    registry = CommandRegistry.load(app_name=dmd_protocol.SDP)
    api = CommandAPI(registry=registry)

    with pytest.raises(XAPIValidationFailed):
        api.validate(make_command(
            to_system=dmd_protocol.SDP,
            action_code=ACTION_CODE_COMMAND,
            property_name=None,
            value="dish001",
            command_name="stop",
        ))


def test_command_api_accepts_response_addressed_to_command_client():
    api = CommandAPI()
    response = make_command(
        from_system=dmd_protocol.DM,
        to_system=dmd_protocol.CMD,
        msg_type=dmd_protocol.MSG_TYPE_RSP,
        status=dmd_protocol.STATUS_SUCCESS,
    )

    api.validate(response)
