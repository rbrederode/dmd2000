import time
import uuid
import threading
import heapq
import datetime

from queue import Queue, Empty

from sdk.util.xbase import XBase, XSoftwareFailure
from sdk.env.events import TimerEvent

import logging
logger = logging.getLogger(__name__)

class Timer:

    manager = None  # Class variable to hold the TimerManager instance

    def __init__(self, name: str, event_q: Queue, duration_ms: int, user_ref=None, user_callback=None):
        """Initialize the timer with the given parameters.
            Parameters
                event_q: Queue to which the timer event will be posted upon expiry
                duration_ms: Duration in milliseconds after which the timer expires
                user_ref: A user reference object available when the timer pops as a TimerEvent
                user_callback: Optional sync callback function to be called upon timer expiry
        """
        self.id = str(uuid.uuid4())
        self.name = name if name is not None else self.id
        self.event_q = event_q
        self.event_queued = None

        self.duration_ms = duration_ms
        self.start_time = time.monotonic() # returns time in seconds as a float
        self.expiry_time = self.start_time + self.duration_ms / 1000.0

        self.user_ref = user_ref
        self.user_callback = user_callback

        self.active = True

        if Timer.manager is None:
            raise XBase(f"Timer {self.name} cannot be created with duration {self.duration_ms} ms. TimerManager is not initialized.")
        else:
            Timer.manager.add_timer(self)
        
        logger.debug(f"Timer {self.name} started with duration {self.duration_ms} ms.")

    def cancel(self) -> bool:
        """ Cancels the timer. 
        Returns True if successfully cancelled
        Returns False if timer has already expired and queued event cannot be cancelled."""

        if self.event_queued is None:
            self.active = False
            logger.debug(f"Timer {self.name} cancelled before an event was queued.")
            return True

        logger.debug(f"Timer {self.name} cancellation requested after timer event already queued.")

        return self.event_queued.cancel()

    def is_expired(self):
        if not self.active:
            return False
        return time.monotonic() >= self.expiry_time

    def is_active(self):
        return self.active

    def queue(self):
        self.active = False
        self.event_queued = TimerEvent(id=self.id, name=self.name, user_ref=self.user_ref, user_callback=self.user_callback, timestamp=datetime.datetime.now(datetime.timezone.utc))
        self.event_q.put(self.event_queued)

    def __str__(self):
        return f"Timer(name={self.name}, active={self.active}, duration_ms={self.duration_ms}, user_ref={self.user_ref}, user_callback={self.user_callback})"

class TimerManager:

    def __init__(self):
        self.heap = []  # (expire_time, Timer)
        self.running = False
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def add_timer(self, timer: Timer):
        with self.lock:
            heapq.heappush(self.heap, (timer.expiry_time, timer))

        logger.debug(f"TimerManager added timer {timer.name} to its heap.")

    def get_timer_by_id(self, timer_id: str) -> Timer:
        with self.lock:
            for _, timer in self.heap:
                if timer.id == timer_id:
                    return timer
        return None

    def get_timers_by_name(self, name: str) -> list[Timer]:
        with self.lock:
            return [t for _, t in self.heap if t.name == name]
        return []   

    def get_timers_by_keyword(self, keyword: str) -> list[Timer]:
        """ Get all timers with a name containing the given keyword. """
        with self.lock:
            return [t for _, t in self.heap if keyword in t.name]
        return [] 

    def remove_timer(self, timer: Timer):

        timer.cancel() # Cancel the timer and event if possible

        with self.lock:
            # Rebuild the heap without the specified timer
            self.heap = [(et, t) for et, t in self.heap if t.id != timer.id]
            heapq.heapify(self.heap)

        logger.debug(f"TimerManager removed timer {timer.name} from its heap.")

    def start(self):
        self.running = True
        self.thread.start()

    def is_running(self):
        return self.running

    def stop(self):
        self.running = False
        self.thread.join()

    def _run(self):
        while self.running:
            with self.lock:
                
                next_wake = None

                if self.heap: # Heap is not empty
                    expire_time, timer = self.heap[0]  # peek earliest
                    if timer.is_active():
                        if timer.is_expired():
                            heapq.heappop(self.heap)
                            timer.queue()
                            logger.debug(f"TimerManager queued event for expired timer {timer.name}.")
                            continue
                        else:
                            next_wake = expire_time - time.monotonic()
                    else:
                        heapq.heappop(self.heap)
                        logger.debug(f"TimerManager removed inactive timer {timer.name} from its heap.")
                        continue

            # Sleep until next timer (or a short fallback)
            if next_wake is None:
                time.sleep(0.1)
            else:
                if next_wake < 0:
                    logger.debug(f"TimerManager next wake is negative ({next_wake}). Setting to 0.")
                    next_wake = 0
                time.sleep(min(next_wake, 0.1))


def process_event(event, event_timestamp) -> bool:
    """ Process the given event and return True if processed, False otherwise.
        Parameters
            event: The event to be processed
            event_timestamp: The timestamp when the event was received
        Returns
            True if the event was processed, False otherwise
    """

    if isinstance(event, TimerEvent):
        # Process timer event
        logger.debug(f"Processing timer event: {event}")
        if event.user_callback:
            try:
                event.user_callback(event.user_ref)
            except Exception as e:
                logger.error(f"Error in user callback for timer event {event.id}: {e}")
        return True
    else:
        logger.warning(f"Unknown event type: {type(event)}")
        return False

def call_back(user_ref):
    """ Example callback function for timer expiry. """
    logger.info(f"Callback executed with user_ref: {user_ref}")

# Example usage
if __name__ == "__main__":

    event_q = Queue()

    Timer.manager = TimerManager()
    Timer.manager.start()

    # Creating Timer instances automatically registers them with the TimerManager
    # We do not need to do anything with this action instance in this example
    from sdk.ipc.action import Action

    action = Action()
    action.set_timer_action(Timer(event_q, 500, user_callback=call_back))   # 0.5s
    action.set_timer_action(Timer(event_q, 1500))  # 1.5s
    action.set_timer_action(Timer(event_q, 3000))  # 3.0s

    print("Waiting for events...")
    while True:
        try:
            event = event_q.get(timeout=1)

            if event is None:
                continue
            else:
                event_timestamp = datetime.datetime.now(datetime.timezone.utc)
                if process_event(event, event_timestamp):
                    logger.debug(f"App processed event: {event} at {event_timestamp}")
                else:
                    logger.warning(f"App could not process event: {event} at {event_timestamp}. Discarding event from queue.")

                # Remove the event from the queue
                event_q.task_done()
        except Empty:
            continue
        
    Timer.manager.stop()