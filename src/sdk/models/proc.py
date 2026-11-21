# -*- coding: utf-8 -*-

import enum
from datetime import datetime, timezone
from schema import Schema, And, Or, Use, SchemaError

from sdk.models.base import BaseModel
from sdk.util.xbase import XInvalidTransition, XAPIValidationFailed, XSoftwareFailure

class ProcessorModel(BaseModel):
    """A class representing the processor model."""

    schema = Schema({
        "_type": And(str, lambda v: v == "ProcessorModel"),
        "name": And(str, lambda v: isinstance(v, str)),
        "current_event": Or(None, And(str, lambda v: isinstance(v, str))),
        "processing_time_ms": Or(None, And(float, lambda v: v >= 0.0)),
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "ProcessorModel",
            "name": "",
            "current_event": "",
            "processing_time_ms": 0.0,
            "last_update": datetime.now(timezone.utc),
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

if __name__ == "__main__":
    
    proc001 = ProcessorModel(
        name="Thread-1",
        current_event="Processing Data",
        processing_time_ms=12.5,
        last_update=datetime.now(timezone.utc)
    )

    proc002 = ProcessorModel()

    import pprint
    print("="*40)
    print("Processor001 Model Initialized")
    pprint.pprint(proc001.to_dict())
    print("="*40)

    print("Processor002 Model Initialized")
    pprint.pprint(proc002.to_dict())
    print("="*40)

    print('Tests from_dict method')
    print('='*40)

    proc003 = ProcessorModel().from_dict(proc001.to_dict())

    pprint.pprint(proc003.to_dict())