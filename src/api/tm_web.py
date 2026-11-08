"""Configuration req/rsp and web status advice between the web app and TM."""

from datetime import datetime, timezone

from api.api import API
from api import protocol
from ipc.message import APIMessage
from util.xbase import XAPIValidationFailed, XAPIUnsupportedVersion

API_VERSION = '1.0'
PROPERTY_CONFIG = 'config'
PROPERTY_MODEL = 'telescope_model'
CONFIG_CATEGORIES = ('DIG', 'DSH', 'ODT', 'OET')


def get_request_id(message):
    """Read correlation through AppProcessor's local API-header echo wrapper."""
    echo = message.get('echo_data')
    while isinstance(echo, dict) and echo.get('client') == 'APIMessage':
        echo = echo.get('echo_data')
    return echo.get('request_id') if isinstance(echo, dict) else None


class TM_WEB(API):
    def get_api_version(self):
        return API_VERSION

    def get_legacy_supported_versions(self):
        return []

    def validate(self, message):
        def require(condition, reason):
            if not condition:
                raise XAPIValidationFailed(reason)

        require(isinstance(message, dict), 'TM-WEB message must be an object')
        require(message.get('api_version') == API_VERSION, 'Unsupported TM-WEB API version')
        call = message.get('api_call')
        require(isinstance(call, dict), 'Missing api_call object')
        require(call.get('msg_type') in ('req', 'rsp', 'adv'), 'Unsupported TM-WEB message type')
        if call['msg_type'] == 'adv':
            require(message.get('from') == protocol.WEB and message.get('to') == protocol.TM,
                    'Status advice must travel from web to tm')
            require(call.get('action_code') == protocol.ACTION_CODE_SET
                    and call.get('property') == protocol.PROPERTY_STATUS,
                    'TM-WEB advice supports set/status only')
            value = call.get('value')
            require(isinstance(value, dict) and value.get('_type') == 'WebServerModel',
                    'Status advice requires a WebServerModel')
            require('message' not in call or isinstance(call['message'], str), 'Invalid status message')
            return
        require(call.get('action_code') in ('get', 'set'), 'TM-WEB supports get/set only')
        require(call.get('property') in (PROPERTY_CONFIG, PROPERTY_MODEL), 'Unsupported TM-WEB property')
        if call.get('property') == PROPERTY_MODEL:
            require(call.get('action_code') == 'get', 'Telescope model supports GET only')
        echo = message.get('echo_data')
        require(isinstance(echo, dict), 'Missing request correlation')
        request_id = get_request_id(message)
        require(isinstance(request_id, str) and 0 < len(request_id) <= 128, 'Invalid request ID')
        if call['msg_type'] == 'req':
            require(message.get('from') == protocol.WEB and message.get('to') == protocol.TM,
                    'Requests must travel from web to tm')
            if call['action_code'] == 'set':
                value = call.get('value')
                require(isinstance(value, dict), 'Configuration value must be an object')
                require(value.get('category') in CONFIG_CATEGORIES, 'Unsupported configuration category')
                require(isinstance(value.get('config'), dict), 'Configuration must be an object')
                require(value.get('old_config') is None or isinstance(value['old_config'], dict),
                        'Old configuration must be an object or null')
        else:
            require(message.get('from') == protocol.TM and message.get('to') == protocol.WEB,
                    'Responses must travel from tm to web')
            require(call.get('status') in ('success', 'error'), 'Invalid response status')
            require(isinstance(call.get('message'), str), 'Missing response message')
            require(call.get('code') is None or isinstance(call['code'], str), 'Invalid response code')
            require(call.get('payload') is None or isinstance(call['payload'], dict), 'Invalid response payload')

    def translate(self, api_msg, target_version=API_VERSION):
        if api_msg.get('api_version') != API_VERSION or target_version != API_VERSION:
            raise XAPIUnsupportedVersion('Unsupported TM-WEB API version')
        return api_msg


def configuration_request(category, config, request_id, old_config=None):
    message = APIMessage()
    message.set_json_api_header(
        api_version=API_VERSION, dt=datetime.now(timezone.utc),
        from_system=protocol.WEB, to_system=protocol.TM,
        echo={'request_id': request_id},
        api_call={'msg_type': 'req', 'action_code': 'set', 'property': PROPERTY_CONFIG,
                  'value': {'category': category, 'config': config, 'old_config': old_config}},
    )
    TM_WEB().validate(message.get_json_api_header())
    return message


def model_request(request_id):
    message = APIMessage()
    message.set_json_api_header(
        api_version=API_VERSION, dt=datetime.now(timezone.utc),
        from_system=protocol.WEB, to_system=protocol.TM,
        echo={'request_id': request_id},
        api_call={'msg_type': 'req', 'action_code': 'get', 'property': PROPERTY_MODEL})
    TM_WEB().validate(message.get_json_api_header())
    return message


def configuration_response(request, result):
    message = APIMessage(api_msg=request)
    message.switch_from_to()
    message.set_timestamp(datetime.now(timezone.utc))
    call = request['api_call']
    message.set_api_call({'msg_type': 'rsp', 'action_code': call['action_code'],
                          'property': call['property'], **result.to_dict()})
    TM_WEB().validate(message.get_json_api_header())
    return message
