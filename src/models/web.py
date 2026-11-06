"""Runtime model for the standalone web application."""

from datetime import datetime, timezone
from schema import Schema, And

from models.app import AppModel
from models.base import BaseModel
from models.comms import CommunicationStatus


class WebServerModel(BaseModel):
    schema = Schema({
        '_type': And(str, lambda value: value == 'WebServerModel'),
        'id': str,
        'app': AppModel,
        'tm_connected': CommunicationStatus,
        'last_update': datetime,
    })
    allowed_transitions = {}

    def __init__(self, **kwargs):
        defaults = {
            '_type': 'WebServerModel', 'id': 'web001',
            'app': AppModel(app_name='web', app_cmd_port=60010, msg_timeout_ms=10000),
            'tm_connected': CommunicationStatus.NOT_ESTABLISHED,
            'last_update': datetime.now(timezone.utc),
        }
        super().__init__(**(defaults | kwargs))
