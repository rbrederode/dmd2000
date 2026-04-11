# -*- coding: utf-8 -*-

import time

from queue import Queue
from datetime import datetime

from models.obs import ObsModel
from util.xbase import XSoftwareFailure

import logging
logger = logging.getLogger(__name__)

class ConnectEvent:

    def __init__(self, local_sap, remote_conn, remote_addr, timestamp=None):
        """Initialize the connect event with the given parameters.
        
            Parameters
                local_sap: local service access point (sap) (e.g. TCPServer or TCPClient) on which the event occurred
                remote_conn: remote socket connection object associated with the event
                remote_addr: remote address associated with the event
                timestamp: Timestamp of the event
        """

        self.local_sap = local_sap  
        self.remote_conn = remote_conn  
        self.remote_addr = remote_addr 
        self.timestamp = timestamp

    def __str__(self):
        """
        Returns a human-readable string representation of this event.
        """
        return f"ConnectEvent@{self.timestamp} - {self.local_sap.description} connected to {self.remote_addr}"

class DisconnectEvent:

    def __init__(self, local_sap, remote_conn, remote_addr, timestamp=None):
        """Initialize the disconnect event with the given parameters.
        
            Parameters
                local_sap: local service access point (sap) (e.g. TCPServer or TCPClient) on which the event occurred
                remote_conn: remote socket connection object associated with the event
                remote_addr: remote address associated with the event
                timestamp: Timestamp of the event
        """

        self.local_sap = local_sap  
        self.remote_conn = remote_conn  
        self.remote_addr = remote_addr 
        self.timestamp = timestamp

    def __str__(self):
        """
        Returns a human-readable string representation of this event.
        """
        return f"DisconnectEvent@{self.timestamp} - {self.local_sap.description} disconnected from {self.remote_addr}"

class DataEvent:

    def __init__(self, local_sap, remote_conn, remote_addr, data, timestamp=None):
        """Initialize the disconnect event with the given parameters.
    
        Parameters
            local_sap: local service access point (sap) (e.g. TCPServer or TCPClient) on which the event occurred
            remote_conn: remote socket connection object associated with the event
            remote_addr: remote address associated with the event
            data: bytes received from the remote end point
            timestamp: Timestamp of the event
        """

        self.local_sap = local_sap  
        self.remote_conn = remote_conn  
        self.remote_addr = remote_addr 
        self.data = data
        self.timestamp = timestamp

    def __str__(self):
        """
        Returns a human-readable string representation of this event.
        """
        if self.data is None:
            return f"DataEvent@{self.timestamp} - {self.local_sap.description} received NO data from {self.remote_addr}\n"
        else:
            display_length = min(len(self.data), 1024)
            display_data = self.data[0:display_length]

            lines = []
            line_length = 30
            for i in range(0, display_length, line_length):
                chunk = display_data[i:i+line_length]
                hex_part = ' '.join(f"{b:02x}" for b in chunk)
                # Pad hex_part to always align (30 bytes * 3 chars per byte minus 1 for last space)
                hex_part = hex_part.ljust((line_length * 3) - 1)
                ascii_part = chunk.decode('ascii', errors='replace').replace('�', '.').replace('?', '.')
                # Pad ascii_part to 30 chars for alignment
                ascii_part = ascii_part.ljust(line_length)
                lines.append(f"{hex_part}  {ascii_part}")

            return (
                f"DataEvent (hex)"+" "*(line_length*3+1-len("DataEvent (hex)"))+"DataEvent (ascii)\n" +
                '\n'.join(lines) +
                f"\nDisplaying {display_length} of {len(self.data)} bytes in the msg\n"
            )

class TimerEvent:

    def __init__(self, id, name=None, user_ref=None, user_callback=None, timestamp=None):
        """Initialize the timer event with the given parameters.

        Parameters
            user_callback: The callback function to be called when the timer expires
            user_ref: A reference specified by the user when the timer was created
            timestamp: Timestamp of the event 
        """
        self.id = id
        self.name = name if name is not None else id
        self.user_ref = user_ref
        self.user_callback = user_callback
        self.timestamp = timestamp
        self.timer_cancelled = False

    def __str__(self):
        """
        Returns a human-readable string representation of this event.
        """
        return f"TimerEvent@{self.timestamp} - name {self.name}, user ref {self.user_ref} user callback {self.user_callback}, cancelled={self.timer_cancelled}"

    def cancel(self) -> bool:
        """
        Cancels the timer event.
        """
        self.timer_cancelled = True
        return True

class InitEvent:
    """ Event indicating that an environment component has been initialised and is ready to operate.
    """
    def __init__(self, app_name: str, timestamp=None):
        self.app_name = app_name
        self.timestamp = timestamp

    def __str__(self):
        return f"InitEvent@{self.timestamp} - App {self.app_name}"

class StatusUpdateEvent:

    STATUS_ENQUEUED = 0
    STATUS_PROCESSING = 1
    STATUS_DEQUEUED = 2

    _10_MIN = 10 * 60 * 1000 # 10 minutes in milliseconds
    _DATE_FORMAT = "%Y-%m-%d %H:%M:%S.%f %Z"

    """ Event indicating a status update from an environment component.
    """
    def __init__(self):
        self.current_status = StatusUpdateEvent.STATUS_DEQUEUED

        self.total_processing_time_ms = 0
        self.total_processing_count = 0 

        self.enqueue_time = None
        self.dequeue_time = None
        self.update_time = None

    def enqueue(self, event_q:Queue):
        self.enqueue_time = time.time()
        self.current_status = StatusUpdateEvent.STATUS_ENQUEUED
        event_q.put(self)

    def notify_dequeued(self):
        self.current_status = StatusUpdateEvent.STATUS_PROCESSING
        self.dequeue_time = time.time()
        self.total_processing_time_ms += (self.dequeue_time - self.enqueue_time) * 1000
        self.total_processing_count += 1

    def notify_update_completed(self):
        self.updated_time = time.time()
        self.current_status = StatusUpdateEvent.STATUS_DEQUEUED
        
    def get_dequeued_count(self):
        return self.total_processing_count

    def is_being_processed(self) -> bool:
        return self.current_status == StatusUpdateEvent.STATUS_PROCESSING

    def is_update_pending(self) -> bool:
        return self.current_status != StatusUpdateEvent.STATUS_DEQUEUED

    def get_average_processing_time(self) -> float:
        if self.total_processing_count == 0:
            return 0.0
        return self.total_processing_time_ms / self.total_processing_count  

    def get_millis_since_update_enqueued(self) -> float:
        return (time.time() - self.enqueue_time) * 1000

    def get_total_processing_time(self) -> float:
        return self.total_processing_time_ms

    def __str__(self):

        enqueue_str = "[None]"
        dequeue_str = "[None]"
        update_str = "[None]"

        status_str = "UNKNOWN"

        if self.current_status == StatusUpdateEvent.STATUS_ENQUEUED:
            status_str = "ENQUEUED"
        elif self.current_status == StatusUpdateEvent.STATUS_PROCESSING:
            status_str = "BEING PROCESSED"
        elif self.current_status == StatusUpdateEvent.STATUS_DEQUEUED:
            status_str = "DEQUEUED"

        if self.enqueue_time is not None:
            enqueue_str = datetime.fromtimestamp(self.enqueue_time).strftime(self._DATE_FORMAT)

        if self.dequeue_time is not None:
            dequeue_str = datetime.fromtimestamp(self.dequeue_time).strftime(self._DATE_FORMAT)

        if self.update_time is not None:
            update_str = datetime.fromtimestamp(self.update_time).strftime(self._DATE_FORMAT)

        return f"StatusUpdateEvent (" + \
            f"Enqueued Timestamp={enqueue_str}, " + \
            f"Dequeued Timestamp={dequeue_str}, " + \
            f"Updated Timestamp={update_str}, " + \
            f"Current Status={status_str}, " + \
            f"Total Processing Count={self.total_processing_count}, " + \
            f"Total Processing Time (ms)={self.total_processing_time_ms}, " + \
            f"Average Processing Time (ms)={self.get_average_processing_time()})"

class ConfigEvent:

    def __init__(self, category=None, old_config=None, new_config=None, timestamp=None):
        """Initialize the config event with the given parameters.

        Parameters
            category: Category of the configuration item being updated
            old_config: The old configuration data associated with the event
            new_config: The new configuration data associated with the event
            timestamp: Timestamp of the event
        """
        self.category = category
        self.old_config = old_config
        self.new_config = new_config
        self.timestamp = timestamp

    def __str__(self):
        """
        Returns a human-readable string representation of this event.
        """
        return f"ConfigEvent@{self.timestamp}\n" + \
            f" - Category: {self.category}\n" + \
            f" - Old Config: {self.old_config}\n" + \
            f" - New Config: {self.new_config}\n"

class ObsEvent:

    def __init__(self, obs: ObsModel=None, transition=None, user_ref=None, timestamp=None):
        """Initialize the obs event with the given parameters.

        Parameters
            obs: Observation associated with the event
            event_type: Type of the observation event
            timestamp: Timestamp of the event
        """
        self.obs = obs
        self.transition = transition
        self.user_ref = user_ref
        self.timestamp = timestamp

    def __str__(self):
        """
        Returns a human-readable string representation of this event.
        """
        return f"ObsEvent@{self.timestamp}\n" + \
            f" - Obs: {self.obs}\n" + \
            f" - Transition Type: {self.transition.name if self.transition else None}\n" + \
            f" - User Ref: {self.user_ref}\n"
