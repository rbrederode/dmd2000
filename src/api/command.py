"""API for application-wide commands received on the command endpoint."""

from typing import Any, Dict

from api import protocol as dmd_protocol
from api.api import API
from util.xbase import XAPIUnsupportedVersion, XAPIValidationFailed


API_VERSION = "1.0"
LEGACY_SUPPORTED_VERSIONS = []


class CommandAPI(API):
    """Validate the small, shared command protocol used by every application."""

    def get_api_version(self) -> str:
        return API_VERSION

    def get_legacy_supported_versions(self) -> list:
        return LEGACY_SUPPORTED_VERSIONS

    def validate(self, api_msg: Dict[str, Any]):
        if not isinstance(api_msg, dict):
            raise XAPIValidationFailed("Command API message must be a dictionary")

        api_version = api_msg.get("api_version")
        if api_version != API_VERSION:
            raise XAPIValidationFailed(f"Unsupported API version {api_version}")

        api_call = api_msg.get("api_call")
        if not isinstance(api_call, dict):
            raise XAPIValidationFailed("Command API message missing dictionary field 'api_call'")

        msg_type = api_call.get("msg_type")
        if msg_type not in (dmd_protocol.MSG_TYPE_REQ, dmd_protocol.MSG_TYPE_RSP):
            raise XAPIValidationFailed(f"Unsupported command message type '{msg_type}'")

        action_code = api_call.get("action_code")
        if action_code not in dmd_protocol.ACTION_CODES:
            raise XAPIValidationFailed(f"Unsupported command action code '{action_code}'")

        if msg_type == dmd_protocol.MSG_TYPE_REQ:
            self._validate_request(api_msg, api_call)
        else:
            self._validate_response(api_msg, api_call)

    @staticmethod
    def _validate_request(api_msg: dict, api_call: dict) -> None:
        if api_msg.get("from") != dmd_protocol.CMD:
            raise XAPIValidationFailed(
                f"Command request must originate from '{dmd_protocol.CMD}'"
            )

        action_code = api_call["action_code"]
        if action_code == dmd_protocol.ACTION_CODE_RESYNC:
            return

        prop_name = api_call.get("property")
        if prop_name not in (dmd_protocol.PROPERTY_TRACE, dmd_protocol.PROPERTY_DEBUG):
            raise XAPIValidationFailed(f"Unsupported command property '{prop_name}'")

        if action_code == dmd_protocol.ACTION_CODE_SET:
            prop_value = api_call.get("value")
            if prop_value not in ("ON", "OFF"):
                raise XAPIValidationFailed(
                    f"Command property '{prop_name}' requires value 'ON' or 'OFF'"
                )

    @staticmethod
    def _validate_response(api_msg: dict, api_call: dict) -> None:
        if api_msg.get("to") != dmd_protocol.CMD:
            raise XAPIValidationFailed(
                f"Command response must be addressed to '{dmd_protocol.CMD}'"
            )

        status = api_call.get("status")
        if status not in dmd_protocol.STATUS:
            raise XAPIValidationFailed(f"Unsupported command response status '{status}'")

    def translate(
        self,
        api_msg: Dict[str, Any],
        target_version: str = API_VERSION,
    ) -> Dict[str, Any]:
        source_version = api_msg.get("api_version")
        if source_version != API_VERSION or target_version != API_VERSION:
            raise XAPIUnsupportedVersion(
                f"Translation from version {source_version} to {target_version} not supported"
            )
        return api_msg
