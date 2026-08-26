import threading
from queue import Queue, Empty
import time
import weakref

import logging
logger = logging.getLogger(__name__)

class Processor(threading.Thread):

    _state_changed = threading.Condition()
    _single_threaded = False
    _single_thread_owner = None
    _active_processors = set()
    _instances = weakref.WeakSet()  # Track live processors for compatibility helpers

    def __init__(self, name=None, event_q=None):

        super().__init__(name=name, daemon=True) # Ensure thread exits when main program exits

        self._event_q = event_q if event_q else Queue()
        self._running = True
        self._event = None
        self._event_timestamp = None
        Processor._instances.add(self)

    @staticmethod
    def stop_all():
        for processor in list(Processor._instances):
            processor.stop()

    def stop(self):
        self._running = False
        with Processor._state_changed:
            Processor._state_changed.notify_all()

    @staticmethod
    def single_thread():
        """Prevent new events from starting and wait for busy processors to finish.

        When called from a processor event, the calling processor remains active
        and becomes the sole processor allowed to continue until free_thread().
        """
        owner = threading.current_thread()

        with Processor._state_changed:
            if Processor._single_threaded and Processor._single_thread_owner is owner:
                return

            while Processor._single_threaded:
                Processor._state_changed.wait()

            Processor._single_threaded = True
            Processor._single_thread_owner = owner

            while any(processor is not owner for processor in Processor._active_processors):
                Processor._state_changed.wait()

    @staticmethod
    def free_thread():
        """Leave single-threaded mode and allow waiting processors to continue."""
        owner = threading.current_thread()

        with Processor._state_changed:
            if not Processor._single_threaded:
                return

            if Processor._single_thread_owner is not owner:
                logger.warning("Processor free_thread called by a thread that does not own single-threaded mode")
                return

            Processor._single_threaded = False
            Processor._single_thread_owner = None
            Processor._state_changed.notify_all()

    def _begin_event_processing(self) -> bool:
        """Wait for the processing gate and register this processor as active."""
        with Processor._state_changed:
            while (
                self._running
                and Processor._single_threaded
                and Processor._single_thread_owner is not self
            ):
                Processor._state_changed.wait()

            if not self._running:
                return False

            Processor._active_processors.add(self)
            return True

    def _end_event_processing(self):
        """Register completion of the current event and wake barrier waiters."""
        with Processor._state_changed:
            Processor._active_processors.discard(self)
            Processor._state_changed.notify_all()

    def put_queue(self, event_q: Queue):
        self._event_q = event_q

    def get_queue(self) -> Queue:
        return self._event_q

    def get_current_event(self):
        return self._event

    def get_current_event_processing_time(self):
        return (time.time() - self._event_timestamp) * 1000 if self._event_timestamp else None

    def run(self):
        """ Thread run method to process events from the queue 
            in either single-threaded or free-threaded mode.
            In single-threaded mode, only one processor can process events at a time.
            In free-threaded mode, multiple processors can process events concurrently.
        """
        logger.debug(f"Processor {self.name} started running")

        while self._running:
            processing_event = False
            try:
                self._event = self._event_q.get(timeout=1)  # Wait for an event for up to 1 second
                self._event_timestamp = time.time()

                if not self._begin_event_processing():
                    self._event_q.task_done()
                    break

                processing_event = True
                try:
                    self.process_event(self._event)
                finally:
                    self._event_q.task_done()

            except Empty:
                pass
            except Exception as e:
                logger.exception(f"Processor: Exception occurred while processing event {self._event} in processor {self.name}: {e}")
            finally:
                if processing_event:
                    self._end_event_processing()

                self._event = None
                self._event_timestamp = None

        logger.debug(f"Processor {self.name} stopped")

    def process_event(self, event) -> bool:
        """ Processes an event from the queue.
            Subclasses must implement this method.
            : returns: True if event was processed, False if event was ignored.
        """
        raise NotImplementedError("Subclasses must implement process_event method")

class TestProcessor(Processor):

    def __init__(self, nr:int, event_q=None):
        super().__init__(event_q=event_q)
        
        self._nr = nr

    def process_event(self, event) -> bool:

        if event > 20 and event < 40:
            Processor.single_thread()
            print(f"s:", end='', flush=True)
        else:
            Processor.free_thread()

        print(f"{self._nr}", end='', flush=True)
        print(f", ", end='', flush=True)
        print(f"{event}", flush=True)
        
        return True

if __name__ == "__main__":

    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG,  # Or DEBUG for more verbosity
        format='%(asctime)s %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    logger = logging.getLogger(__name__)

    q = Queue()

    test1 = TestProcessor(1, event_q=q)
    test2 = TestProcessor(2, event_q=q)
    test3 = TestProcessor(3, event_q=q)
    test4 = TestProcessor(4, event_q=q)

    test1.start()
    test2.start()
    test3.start()
    test4.start()

    for i in range(400):
        q.put(i)

    time.sleep(5)  # Allow some time for processing to complete

    Processor.stop_all()
