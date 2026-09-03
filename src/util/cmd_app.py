"""Send a command API request to any DMD2000 application.

Examples::

    python -m util.cmd_app --system dm --port 60002 set trace ON
    python -m util.cmd_app --system sdp --port 60003 get debug
    python -m util.cmd_app --system tm --port 60001 resync
"""

import argparse
import logging
import threading
import time
from datetime import datetime, timezone
from queue import Queue

from api import protocol as dmd_protocol
from api.command import CommandAPI
from env.app_processor import AppProcessor
from ipc.action import Action
from ipc.message import APIMessage
from ipc.tcp_client import TCPClient
from models.app import AppModel
from models.comms import InterfaceType
from util.xbase import XSoftwareFailure


logger = logging.getLogger(__name__)

SUPPORTED_SYSTEMS = (
    dmd_protocol.TM,
    dmd_protocol.DM,
    dmd_protocol.DIG,
    dmd_protocol.SDP,
    dmd_protocol.WS,
    dmd_protocol.IMU,
    dmd_protocol.APP,
)
COMMAND_PROPERTIES = (
    dmd_protocol.PROPERTY_TRACE,
    dmd_protocol.PROPERTY_DEBUG,
)


def normalise_command_config(config: dict) -> tuple[str, str | None, str | None]:
    """Translate a UI command payload to cmd_app action arguments."""

    if not isinstance(config, dict):
        raise ValueError("Command configuration must be a JSON object")

    command = str(config.get("cmd") or "").strip().lower()
    property_name = config.get("property")
    property_name = str(property_name).strip().lower() if property_name is not None else None
    property_name = property_name or None
    value = config.get("value")
    value = str(value).strip().upper() if value is not None else None

    # The UI's convenient form uses cmd=Trace/Debug. cmd_app's protocol form
    # calls these SET (with a value) or GET (without a value) operations.
    if command in dmd_protocol.PROPERTIES:
        if property_name is not None and property_name != command:
            raise ValueError(
                f"Command '{command}' conflicts with property '{property_name}'"
            )
        property_name = command
        command = (
            dmd_protocol.ACTION_CODE_SET
            if value is not None
            else dmd_protocol.ACTION_CODE_GET
        )

    if command == dmd_protocol.ACTION_CODE_RESYNC:
        # UI command records use a common schema and may include property/value
        # fields even though the command protocol does not use them for resync.
        return command, None, None

    if command not in (dmd_protocol.ACTION_CODE_GET, dmd_protocol.ACTION_CODE_SET):
        raise ValueError(f"Unsupported command '{config.get('cmd')}'")
    if property_name not in COMMAND_PROPERTIES:
        raise ValueError(f"Unsupported command property '{config.get('property')}'")
    if command == dmd_protocol.ACTION_CODE_GET:
        if value is not None:
            raise ValueError("Get does not accept a value")
        return command, property_name, None
    if value not in ("ON", "OFF"):
        raise ValueError(
            f"Command property '{property_name}' requires value 'On' or 'Off'"
        )
    return command, property_name, value


class CommandUtilityDriver:
    """Minimal command-client driver used to process the API response."""

    def __init__(self, target_system: str, action_code: str, property_name=None):
        self.app_model = AppModel(app_name=dmd_protocol.CMD, app_tracing=False)
        self.target_system = target_system
        self.action_code = action_code
        self.property_name = property_name
        self.api = CommandAPI()
        self.endpoint = None
        self.entity_connection_map = {}
        self.response = None
        self.response_received = threading.Event()
        self.last_error = None

    def get_interface(self, system_name):
        if system_name != self.target_system:
            raise XSoftwareFailure(f"Driver has no interface for system {system_name}")
        return self.api, self.endpoint, InterfaceType.APP_APP

    def set_last_err(self, message, timestamp=None):
        self.last_error = message
        return message

    def _process_response(self, api_call):
        if (
            api_call.get("msg_type") == dmd_protocol.MSG_TYPE_RSP
            and api_call.get("action_code") == self.action_code
            and (
                self.action_code == dmd_protocol.ACTION_CODE_RESYNC
                or api_call.get("property") == self.property_name
            )
        ):
            self.response = api_call
            self.response_received.set()
        return Action()

    def process_tm_msg(self, event, api_msg, api_call, payload):
        return self._process_response(api_call)

    def process_dm_msg(self, event, api_msg, api_call, payload):
        return self._process_response(api_call)

    def process_dig_msg(self, event, api_msg, api_call, payload):
        return self._process_response(api_call)

    def process_sdp_msg(self, event, api_msg, api_call, payload):
        return self._process_response(api_call)

    def process_ws_msg(self, event, api_msg, api_call, payload):
        return self._process_response(api_call)

    def process_imu_msg(self, event, api_msg, api_call, payload):
        return self._process_response(api_call)

    def process_app_msg(self, event, api_msg, api_call, payload):
        return self._process_response(api_call)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send a command to a DMD2000 application"
    )
    parser.add_argument("--host", default="localhost", help="Application command server host")
    parser.add_argument("--port", type=int, required=True, help="Application command server port")
    parser.add_argument(
        "--system",
        required=True,
        choices=SUPPORTED_SYSTEMS,
        help="Target application",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Seconds to wait for connection and response",
    )

    subparsers = parser.add_subparsers(dest="action", required=True)

    set_parser = subparsers.add_parser("set", help="Set a command property")
    set_parser.add_argument("property", choices=COMMAND_PROPERTIES)
    set_parser.add_argument("value", type=str.upper, choices=("ON", "OFF"))

    get_parser = subparsers.add_parser("get", help="Get a command property")
    get_parser.add_argument("property", choices=COMMAND_PROPERTIES)

    subparsers.add_parser("resync", help="Reload application configuration")
    return parser


def _wait_for_connection(client: TCPClient, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.connected:
            return True
        time.sleep(0.05)
    return client.connected


def construct_request(args, api: CommandAPI) -> APIMessage:
    """Construct and validate the request represented by parsed CLI arguments."""

    api_call = {
        "msg_type": dmd_protocol.MSG_TYPE_REQ,
        "action_code": args.action,
    }
    property_name = getattr(args, "property", None)
    value = getattr(args, "value", None)
    if property_name is not None:
        api_call["property"] = property_name
    if value is not None:
        api_call["value"] = value

    request = APIMessage()
    request.set_json_api_header(
        api_version=api.get_api_version(),
        dt=datetime.now(timezone.utc),
        from_system=dmd_protocol.CMD,
        to_system=args.system,
        api_call=api_call,
    )
    api.validate(request.get_json_api_header())
    return request


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    property_name = getattr(args, "property", None)
    event_queue = Queue()
    driver = CommandUtilityDriver(args.system, args.action, property_name)
    processor = AppProcessor(name="cmd-app", event_q=event_queue, driver=driver)
    client = TCPClient(
        description=args.system,
        queue=event_queue,
        host=args.host,
        port=args.port,
    )
    driver.endpoint = client
    processor.start()

    try:
        client.connect()
        if not _wait_for_connection(client, args.timeout):
            logger.error(
                "Timed out connecting to %s at %s:%s",
                args.system,
                args.host,
                args.port,
            )
            return 1

        client.send(construct_request(args, driver.api))
        if not driver.response_received.wait(args.timeout):
            logger.error(
                "Timed out waiting for %s response from %s",
                args.action,
                args.system,
            )
            return 1

        response = driver.response
        value_text = ""
        if "value" in response:
            value_text = f" value={response['value']}"
        logger.info(
            "%s %s: %s%s — %s",
            args.system,
            args.action,
            response.get("status", "unknown"),
            value_text,
            response.get("message", "no message"),
        )
        return 0 if response.get("status") == dmd_protocol.STATUS_SUCCESS else 1
    finally:
        client.stop()
        processor.stop()
        processor.join(timeout=2)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    raise SystemExit(main())
