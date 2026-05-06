import argparse
import asyncio
from datetime import datetime, timezone, timedelta
from logging.handlers import TimedRotatingFileHandler
import os
from pathlib import Path
from queue import Queue, Empty
import time
import threading

from api.api import API
from ipc.tcp_server import TCPServer
from ipc.message import AppMessage, APIMessage
from ipc.action import Action
from models.app import AppModel
from models.comms import InterfaceType
from models.proc import ProcessorModel
from models.health import HealthState
from util.availability import get_app_reliability, get_app_availability
from util.format import fmt_bool
from util import log
from util.version import get_version_info
from util.xbase import XBase, XSoftwareFailure
from util.timer import Timer, TimerManager
from env import events
from env.events import InitEvent, StatusUpdateEvent
from env.processor import Processor
from env.app_processor import AppProcessor

import logging
logger = logging.getLogger(__name__)

class App:

    logs_dir = Path("./logs").expanduser()

    def __init__(self, app_name: str, app_model: AppModel):

        version_info = get_version_info()

        if app_name is None or app_name.strip() == "":
            raise XSoftwareFailure("App requires a non-empty app name to initialise itself")

        # Ensure we have an app context for logging during initialisation
        log.set_current_app(app_name)  

        self.app_model = app_model if app_model is not None else AppModel(app_name=app_name)
        self.app_model.app_name = app_name
        
        self.app_model.version = version_info
        self.app_model.app_running = True
        
        self.queue = Queue()                     # Event queue for the application
        self.status_update_event = events.StatusUpdateEvent()  # Reusable status update event
        self._queue_overload_active = False
        self._queue_overload_lock = threading.Lock()
        
        self.interfaces = {}                    # Dictionary to hold registered App interfaces
        self.entity_connection_map = {}         # Map of entity IDs to client sockets for entity driving interfaces

        self.arg_parser = argparse.ArgumentParser(description=self.app_model.app_name)
        self.add_args(self.arg_parser)
        self.app_model.arguments = vars(self.get_args())

        # Set log level based on verbose argument
        if self.get_args().verbose:
            logging.getLogger().setLevel(logging.DEBUG)
        else:
            logging.getLogger().setLevel(logging.INFO)

        self.app_model.num_processors = max(1, self.get_args().num_processors)
        self.processors = []                    # List to hold processor threads
        self.status_thread = None

        self.start_timer_manager()              # Ensure timer manager is started before any timers are created

        self.app_model.health = HealthState.UNKNOWN
        self._last_heartbeat = None
        self._lock = threading.Lock()
        self.avail_logger = self.get_availability_logger()
        self.report_availability()

    def __del__(self):
        self.stop()

    def set_app_model(self, app_model: AppModel):
        if app_model is None:
            raise XSoftwareFailure("App model cannot be set to None")

        self.app_model = app_model
        self.app_model.num_processors = max(1, self.get_args().num_processors)
        self.app_model.health = HealthState.UNKNOWN
        self.app_model.last_update = datetime.now(timezone.utc)

    def get_args(self):
        # Use parse_known_args to avoid pytest's extra CLI arguments causing failures
        args, _ = self.arg_parser.parse_known_args()
        return args

    def get_queue(self):
        return self.queue

    def get_queue_watermarks(self) -> tuple[int, int]:
        num_processors = max(1, len(self.processors) if hasattr(self, "processors") else self.app_model.num_processors)
        args = self.get_args()
        high = getattr(args, "queue_high_watermark", 0) or max(8, num_processors * 2)
        low = getattr(args, "queue_low_watermark", 0) or max(2, num_processors // 2)
        return high, min(low, high)

    def get_queue_overload_reason(self) -> str:
        high, low = self.get_queue_watermarks()
        return f"queue={self.queue.qsize()}, high={high}, low={low}"

    def is_queue_overloaded(self) -> bool:
        high, low = self.get_queue_watermarks()
        queue_size = self.queue.qsize()

        with self._queue_overload_lock:
            if self._queue_overload_active:
                if queue_size <= low:
                    self._queue_overload_active = False
                    logger.info(f"App {self.app_model.app_name} event queue overload recovered: queue={queue_size}, high={high}, low={low}")
            elif queue_size >= high:
                self._queue_overload_active = True
                logger.warning(f"App {self.app_model.app_name} event queue overload detected: queue={queue_size}, high={high}, low={low}")

            return self._queue_overload_active

    def get_name(self):
        return self.app_model.app_name

    def is_headless(self) -> bool:
        return bool(getattr(self.get_args(), "headless", False))

    def set_name(self, name: str):
        self.app_model.app_name = name

    def get_arg_parser(self):
        return self.arg_parser

    def get_last_err_msg(self) -> str:
        return self.app_model.last_err_msg

    def get_last_err_dt(self) -> datetime:
        return self.app_model.last_err_dt

    def set_last_err(self, err_msg: str, err_dt: datetime=None) -> str:
        self.app_model.last_err_msg = err_msg
        self.app_model.last_err_dt = err_dt if err_dt is not None else datetime.now(timezone.utc)
        return err_msg

    def add_args(self, arg_parser):
        """Specifies the application's command line arguments.
            Subclasses should override this method to add their own arguments.
            Call the superclass method first to ensure base arguments are added.
        """
        arg_parser.add_argument("--verbose", "-v",action="store_true", help="Enable verbose logging")
        arg_parser.add_argument("--num_processors", "-np", type=int, required=False, help="Number of processor threads to create", default=4)
        arg_parser.add_argument("--profile", type=str, required=False, help="Configuration profile to use e.g. default, alston etc. See ./config directory for existing profiles", default="default") 
        arg_parser.add_argument("--entity_id", type=str, required=False, help="Alphanumeric entity ID to uniquely identify a dish or digitiser instance <[A-Z][a-z][0-9]+> e.g. dsh001", default="<undefined>")
        arg_parser.add_argument("--headless",type=fmt_bool,nargs="?",const=True,required=False,default=False,help="Disable displays and UI interactions for apps that support them. Accepts true/false.")
        arg_parser.add_argument("--queue_high_watermark", type=int, required=False, help="Queue size at which app-specific overload handling starts; 0 derives from processor count", default=0)
        arg_parser.add_argument("--queue_low_watermark", type=int, required=False, help="Queue size at which app-specific overload handling stops; 0 derives from processor count", default=0)

    def start(self):
        """Starts the application."""

        self.queue.put(InitEvent(self.app_model.app_name))  # Start with an initialisation event
        self.start_processors()
        self.start_status_thread()

        logger.info(f"App {self.app_model.app_name} started")

    def run(self):
        
        while self.app_model.app_running:
            try:
                logger.info(f"App {self.app_model.app_name} status thread checking status update event {self.status_update_event}")
                
                # Sends heartbeat to the availability logger (I'm alive!)
                self.heartbeat()
                # If we have just passed the hour mark, report app availability
                now = datetime.now(timezone.utc)
                if now.minute == 0 and now.second < 30:
                    self.report_availability()

                if self.status_update_event.is_update_pending():
                    if self.status_update_event.get_dequeued_count() == 0:
                        # First status update event is still enqueued, so initialisation 
                        # has not completed yet. Skip this status update.
                        pass
                    else:
                        # An update is still pending, and we know initialisation has completed
                        # (since the dequeued count is > 0), so alert the user
                        ms_since_update_queued = self.status_update_event.get_millis_since_update_enqueued()

                        debug_info = []
                        debug_info.append("-"*40+"\n")
                        debug_info.append(f"App {self.app_model.app_name} Debug Info\n")
                        debug_info.append("-"*40+"\n")

                        debug_info.append(f"Queue size is {self.queue.qsize()}\n")
                        debug_info.append(f"Status update event has been pending for {ms_since_update_queued} ms\n")
                        debug_info.append(f"Status update event dequeued count is {self.status_update_event.get_dequeued_count()}\n")
                        debug_info.append(f"Status update event currently being processed {self.status_update_event.is_being_processed()}\n")
                        debug_info.append(f"Number of processors: {len(self.processors)}\n")

                        for processor in self.processors:

                            debug_info.append(f"Processor {processor.name} current event: {processor.get_current_event()}\n")
                            debug_info.append(f"Processor {processor.name} elapsed processing time: {processor.get_current_event_processing_time()} ms\n")

                        debug_info.append("-"*40+"\n")

                        logger.info(f"App {self.app_model.app_name} Debug Info:\n{''.join(debug_info)}")
                else:
                    self.status_update_event.enqueue(self.queue)
                    
                time.sleep(30)  # Sleep briefly to avoid busy-waiting

            except Exception as e:
                logger.error(self.set_last_err(f"App {self.app_model.app_name} encountered an error: {e}"))
    
    def stop(self):
        """Stops the application."""

        self.app_model.app_running = False # Stops status thread
        self.stop_timer_manager()
        self.stop_processors()
        self.stop_status_thread()

        if not self.queue.empty():
            self.queue.queue.clear()

        logger.info(f"App {self.app_model.app_name} stopped")
        self.app_model.health = HealthState.UNKNOWN

    def start_processors(self):
        """Starts all processor threads."""

        for i in range(self.app_model.num_processors):
            processor = AppProcessor(name=f"{self.app_model.app_name}-Processor-{i+1}", event_q=self.queue, driver=self)
            self.processors.append(processor)
            processor.start()

    def stop_processors(self):
        """Stops all processor threads."""
        if not hasattr(self, "processors") or self.processors is None:
            return

        for processor in self.processors:
            processor.stop()

        for processor in self.processors:
            if processor.is_alive():
                processor.join(timeout=2)  # Wait for the processor thread to finish, with a timeout to avoid hanging indefinitely

        self.processors = []
        self.app_model.health = HealthState.UNKNOWN

    def start_status_thread(self):
        """Starts a thread to periodically enqueue status update events."""
        self.status_thread = threading.Thread(target=self.run, name=f"{self.app_model.app_name}-StatusThread", daemon=True)
        self.status_thread.start()
        logger.info(f"App {self.app_model.app_name} started status thread")

    def stop_status_thread(self):
        """Stops the status update thread."""
        if not hasattr(self, "status_thread") or self.status_thread is None:
            return

        if self.status_thread and self.status_thread.is_alive():
            self.status_thread.join(timeout=2)  # Wait for the status thread to finish, with a timeout to avoid hanging indefinitely
        self.status_thread = None
        logger.info(f"App {self.app_model.app_name} stopped status thread")

    def start_timer_manager(self):
        """Starts the timer manager if not already running."""
        if Timer.manager is None:
            Timer.manager = TimerManager()
            Timer.manager.start()
            logger.info(f"App {self.app_model.app_name} started timer manager")

    def stop_timer_manager(self):
        """Stops the timer manager if running."""
        if Timer.manager is not None:
            Timer.manager.stop()
            Timer.manager = None
            logger.info(f"App {self.app_model.app_name} stopped timer manager")
        
    def register_interface(self, system_name: str, api: API, endpoint, interface_type: InterfaceType = InterfaceType.UNKNOWN):
        """Registers an interface with the application.
            : param system_name: The name of the system the interface is for
            : param api: The API implementation for the interface
            : param endpoint: The endpoint (e.g. TCPServer or TCPClient) for the interface
            : param interface_type: The type of the interface (e.g. entity_driver, entity or app_app)
            An entity driving interface will connect to multiple entities e.g. dishes or digitisers.
            An entity interface connects to an entity driving interface e.g. a dish or digitiser
            An app_app interface connects to another application e.g. TM to SDP
        """

        if system_name is None or system_name.strip() == "":
            raise XSoftwareFailure(self.set_last_err(f"App {self.app_model.app_name} system name must be a non-empty string.\n{self.app_model.to_dict()}"))

        if api is None:
            raise XSoftwareFailure(self.set_last_err(f"App {self.app_model.app_name} API implementation must be provided.\n{self.app_model.to_dict()}"))

        if endpoint is None:
            raise XSoftwareFailure(self.set_last_err(f"App {self.app_model.app_name} endpoint must be provided.\n{self.app_model.to_dict()}"))

        logger.info(f"App {self.app_model.app_name} registered interface for system '{system_name}' with API version {api.get_api_version()} at endpoint {endpoint}")

        self.interfaces[system_name] = (api, endpoint, interface_type)
        self.app_model.interfaces.append(system_name)

    def deregister_interface(self, system_name: str):
        """Deregisters an interface from the application.
            : param system_name: The name of the system the interface is for
        """
        if system_name in self.interfaces:
            self.app_model.interfaces.remove(system_name)
            del self.interfaces[system_name]

            logger.info(f"App {self.app_model.app_name} deregistered interface for system '{system_name}'")
        else:
            logger.warning(f"App {self.app_model.app_name} could not find interface for system '{system_name}' to deregister")

    def get_interface(self, system_name: str):
        """Gets the interface for a given system name.
            : param system_name: The name of the system the interface is for
            : return: The API, endpoint, and interface type if found, else None
        """
        if system_name not in self.interfaces:
            raise XSoftwareFailure(self.set_last_err(f"App {self.app_model.app_name} has no registered interface for system '{system_name}'"))

        return self.interfaces.get(system_name, None)

    def get_app_processor_state(self) -> dict:
        """Updates the app(lication) model with the current state of its processors.
            : return: A dictionary representation of the updated AppModel instance
        """
        processors = []
        for processor in self.processors:
            # Get the current event and convert it to a JSON-serializable form.
            ev = processor.get_current_event()
            if ev is None:
                ev_repr = None
            else:
                # Prefer a structured representation if the event exposes it,
                # otherwise fall back to a short string.
                try:
                    if hasattr(ev, "to_dict") and callable(ev.to_dict):
                        ev_repr = ev.to_dict()
                    else:
                        # Use a concise string representation to avoid embedding
                        # large objects or types that aren't JSON serializable.
                        ev_repr = str(ev)
                except Exception:
                    ev_repr = repr(ev)

            proc_model = ProcessorModel(
                name=processor.name,
                current_event=ev_repr,
                processing_time_ms=processor.get_current_event_processing_time()
            )
            processors.append(proc_model)

        self.app_model.queue_size = self.queue.qsize()
        self.app_model.processors = processors
        self.app_model.last_update = datetime.now(timezone.utc)

        return self.app_model.to_dict()

    def heartbeat(self):
        """Updates the last heartbeat timestamp to the current time."""
        with self._lock:
            self._last_heartbeat = datetime.now(timezone.utc)
            self.avail_logger.info(f"Heartbeat state={self.app_model.health.name}")

    def set_health_state(self, health: HealthState):
        """Sets the health state of the application.
            : param health: The new health state
        """
        with self._lock:
            old_health = self.app_model.health
            if old_health != health:
                self.app_model.health = health
                self.app_model.last_update = datetime.now(timezone.utc)
                self.avail_logger.info(f"App {self.app_model.app_name} health state transition {old_health.name} -> {health.name}")

    def get_availability_logger(self) -> logging.Logger:
        """Gets a dedicated logger for availability logging only."""

        log_dir = Path(App.logs_dir).expanduser() / "availability"
        log_dir.mkdir(parents=True, exist_ok=True)

        # Use a unique logger name for availability
        logger_name = f"{self.app_model.app_name}.availability"
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.INFO)
        logger.propagate = False  # Don't propagate to root logger

        log_file = os.path.join(log_dir, f"{self.app_model.app_name}.log")

        # Remove all handlers to avoid duplicate logs if re-initialized
        logger.handlers.clear()

        handler = TimedRotatingFileHandler(
            filename=log_file,
            when="midnight",
            interval=1,
            backupCount=731,      # keep about 2 years of daily logs
            encoding="utf-8",
            utc=True)

        handler.suffix = "%Y-%m-%d"  # results in e.g. dm.log.2026-04-05
        formatter = logging.Formatter("%(asctime)s UTC | %(levelname)s | %(message)s")
        formatter.converter = time.gmtime
        handler.setFormatter(formatter)

        logger.addHandler(handler)

        return logger

    def report_availability(self):
        """Reports the current availability metrics for the last hour."""

        with self._lock:
            logs_dir = App.logs_dir / "availability"
            end_period = datetime.now(timezone.utc)
            start_period = end_period - timedelta(hours=1)
            
            self.app_model.availability = get_app_availability(
                logs_dir, 
                self.app_model.app_name, 
                start_period, 
                end_period)
            
            self.app_model.reliability = get_app_reliability(
                logs_dir, 
                self.app_model.app_name, 
                start_period, 
                end_period)
             
            
