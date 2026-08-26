from datetime import datetime, timezone

from api import protocol as dmd_protocol
from api.command import CommandAPI
from env.app_processor import AppProcessor
from env.events import ConnectEvent, DataEvent, DisconnectEvent
from ipc.message import APIMessage
from models.comms import InterfaceType
from util.set_trace import TraceUtilityDriver, build_arg_parser


class Endpoint:
    description = dmd_protocol.DM


def test_trace_utility_cli_normalises_trace_state():
    args = build_arg_parser().parse_args(
        ["--system", "dm", "--trace", "on", "--port", "50002"]
    )

    assert args.system == dmd_protocol.DM
    assert args.trace == "ON"
    assert args.port == 50002


def test_trace_utility_driver_matches_current_app_processor_interface():
    driver = TraceUtilityDriver(dmd_protocol.DM)
    endpoint = Endpoint()
    driver.endpoint = endpoint
    processor = AppProcessor(name="set-trace-test", driver=driver)
    connection = object()
    remote_addr = ("127.0.0.1", 50002)

    api, returned_endpoint, interface_type = driver.get_interface(dmd_protocol.DM)
    assert isinstance(api, CommandAPI)
    assert returned_endpoint is endpoint
    assert interface_type == InterfaceType.APP_APP

    assert processor.process_event(
        ConnectEvent(endpoint, connection, remote_addr, datetime.now(timezone.utc))
    ) is True

    driver._process_response({
        "msg_type": dmd_protocol.MSG_TYPE_ADV,
        "action_code": dmd_protocol.ACTION_CODE_SET,
        "property": dmd_protocol.PROPERTY_STATUS,
        "status": dmd_protocol.STATUS_SUCCESS,
    })
    assert driver.response is None
    assert not driver.response_received.is_set()

    response = APIMessage()
    response.set_json_api_header(
        api_version=api.get_api_version(),
        dt=datetime.now(timezone.utc),
        from_system=dmd_protocol.DM,
        to_system=dmd_protocol.CMD,
        api_call={
            "msg_type": dmd_protocol.MSG_TYPE_RSP,
            "action_code": dmd_protocol.ACTION_CODE_SET,
            "property": dmd_protocol.PROPERTY_TRACE,
            "value": "ON",
            "status": dmd_protocol.STATUS_SUCCESS,
            "message": "Tracing set to ON",
        },
    )
    assert processor.process_event(
        DataEvent(
            endpoint,
            connection,
            remote_addr,
            response.to_data(),
            datetime.now(timezone.utc),
        )
    ) is True

    assert driver.response == response.get_api_call()
    assert driver.response_received.is_set()
    assert processor.process_event(
        DisconnectEvent(endpoint, connection, remote_addr, datetime.now(timezone.utc))
    ) is True
