import enum
from datetime import datetime, timezone
from schema import Schema, And, Or, Use, SchemaError

from models.base import BaseModel
from models.health import HealthState

class AppModel(BaseModel):
    """A class representing an App(lication) model."""

    schema = Schema({
        "_type": And(str, lambda v: v == "AppModel"),
        "app_name": And(str, lambda v: isinstance(v, str)),                             # Name of the application e.g. "sdp", "tm", "dm", "dig", "ws"
        "app_running": And(bool, lambda v: isinstance(v, bool)),                        # Is the application currently running
        "app_tracing": And(bool, lambda v: isinstance(v, bool)),                        # Is the application currently tracing API messages
        "app_debug": And(bool, lambda v: isinstance(v, bool)),                          # Is application-wide debug logging enabled
        "app_cmd_host": Or(None, And(str, lambda v: isinstance(v, str))),               # Command host for commands such as trace ON/OFF, debug or resync
        "app_cmd_port": Or(None, And(int, lambda v: v > 0)),                            # Command port for commands such as trace ON/OFF, debug or resync
        "version": And(str, lambda v: isinstance(v, str)),                              # Display version string e.g. "v1.2.3-45"
        "health": And(HealthState, lambda v: isinstance(v, HealthState)),               # Health state of the application (see HealthState enum)
        "num_processors": And(int, lambda v: v >= 0),                                   # Number of processor instances (threads) used by the application
        "queue_size": And(int, lambda v: v >= 0),                                       # Size of the event queue for the application
        "interfaces": And(list, lambda v: isinstance(v, list)),
        "processors": And(list, lambda v: isinstance(v, list)),
        "msg_timeout_ms": And(int, lambda v: v >= 0),
        "arguments": Or(None, And(dict, lambda v: isinstance(v, dict))),
        "reliability": Or(None, And(dict, lambda v: isinstance(v, dict))),              # MTBF, MTTR, Reliability (last hour)
        "availability": Or(None, And(float, lambda v: 0.0 <= v <= 100.0)),              # Availability percentage (last hour)
        "last_err_msg": Or(None, And(str, lambda v: isinstance(v, str))),               # Last error message from the app
        "last_err_dt": Or(None, And(datetime, lambda v: isinstance(v, datetime))),      # Last error datetime from the app
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

      # Default values
        defaults = {
            "_type": "AppModel",
            "app_name": "app",
            "app_running": False,
            "app_tracing": False,
            "app_debug": False,
            "app_cmd_host": "127.0.0.1",
            "app_cmd_port": None,
            "version": "",
            "health": HealthState.UNKNOWN,
            "num_processors": 0,
            "queue_size": 0,
            "interfaces": [],
            "processors": [],
            "msg_timeout_ms": 10000,
            "arguments": None,
            "reliability": None,
            "availability": None,
            "last_err_msg": None,
            "last_err_dt": None,
            "last_update": datetime.now(timezone.utc),
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

if __name__ == "__main__":

    app001 = AppModel(
        app_name="app001",
        app_running=True,
        app_tracing=True,
        version="v1.0.0",
        num_processors=2,
        queue_size=0,
        interfaces=["tm", "sdp"],
        processors=[],
        health=HealthState.UNKNOWN,
        msg_timeout_ms=10000,
        last_update=datetime.now(timezone.utc)
    )

    app002 = AppModel()

    import pprint
    print("="*40)
    print("App001")
    print("="*40)
    pprint.pprint(app001.to_dict())
    print("="*40)
    print("App002")
    print("="*40)
    pprint.pprint(app002.to_dict()) 
    print("="*40)
    print('Tests from_dict method')
    print('='*40)

    app003 = AppModel().from_dict(app001.to_dict())

    pprint.pprint(app003.to_dict())
