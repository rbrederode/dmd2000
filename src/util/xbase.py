import traceback

# Define Exception IDs
EXC_ID_STREAM_UNABLE_TO_EXTRACT             = 0x0000
EXC_ID_STREAM_UNABLE_TO_ENCODE              = 0x0001
EXC_ID_API_VALIDATION_FAILED                = 0x0002
EXC_ID_API_UNSUPPORTED_VERSION              = 0x0003
EXC_ID_SOFTWARE_FAILURE                     = 0x0004
EXC_ID_HARDWARE_FAILURE                     = 0x0005
EXC_ID_SCHEDULER_FAILURE                    = 0x0006
EXC_ID_INVALID_TRANSITION                   = 0x0007
EXC_ID_TIMEOUT_WAITING_FOR_RESPONSE         = 0x0008
EXC_ID_COMMS_FAILURE                        = 0x0009
EXC_ID_UNKNOWN_ENTITY                       = 0x0010

class XBase(Exception):
    
    def __init__(self, id:int, message: str=None, data: bytes=None):
        super().__init__(message)

        self.id = id

        self.messages = []
        if message is not None:
            self.messages.append(message)

        self.data = []
        if data is not None:
            self.data.append(data)

    @property
    def message(self) -> str:
        if self.messages:
            return "; ".join(str(message) for message in self.messages)
        return ""

    def __str__(self):
        # Provide a concise string representation. Avoid including the
        # traceback here because Python already prints tracebacks when
        # exceptions are unhandled; embedding the traceback in __str__ can
        # lead to duplicated / confusing output.
        if self.message:
            return self.message
        return f"XBase(id={self.id}, data={[len(d) for d in self.data]})"

class XStreamUnableToExtract(XBase):

    def __init__(self, message: str=None, data: bytes=None):
        super().__init__(EXC_ID_STREAM_UNABLE_TO_EXTRACT, message, data)

class XStreamUnableToEncode(XBase):

    def __init__(self, message: str=None, data: bytes=None):
        super().__init__(EXC_ID_STREAM_UNABLE_TO_ENCODE, message, data)

class XAPIValidationFailed(XBase):

    def __init__(self, message: str=None, data: bytes=None):
        super().__init__(EXC_ID_API_VALIDATION_FAILED, message, data)

class XAPIUnsupportedVersion(XBase):

    def __init__(self, message: str=None, data: bytes=None):
        super().__init__(EXC_ID_API_UNSUPPORTED_VERSION, message, data)

class XSoftwareFailure(XBase):

    def __init__(self, message: str=None, data: bytes=None):
        super().__init__(EXC_ID_SOFTWARE_FAILURE, message, data)

class XHardwareFailure(XBase):

    def __init__(self, message: str=None, data: bytes=None):
        super().__init__(EXC_ID_HARDWARE_FAILURE, message, data)

class XSchedulerFailure(XBase):

    def __init__(self, message: str=None, data: bytes=None):
        super().__init__(EXC_ID_SCHEDULER_FAILURE, message, data)

class XInvalidTransition(XBase):

    def __init__(self, message: str=None, data: bytes=None):
        super().__init__(EXC_ID_INVALID_TRANSITION, message, data)

class XTimeoutWaitingForResponse(XBase):

    def __init__(self, message: str=None, data: bytes=None):
        super().__init__(EXC_ID_TIMEOUT_WAITING_FOR_RESPONSE, message, data)

class XCommsFailure(XBase):

    def __init__(self, message: str=None, data: bytes=None):
        super().__init__(EXC_ID_COMMS_FAILURE, message, data)

class XUnknownEntity(XBase):

    def __init__(self, message: str=None, data: bytes=None):
        super().__init__(EXC_ID_UNKNOWN_ENTITY, message, data)
