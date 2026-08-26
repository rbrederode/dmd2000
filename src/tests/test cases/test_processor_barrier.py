import queue
import threading

from env.processor import Processor


class BarrierProcessor(Processor):
    def __init__(self, event_q, events):
        super().__init__(event_q=event_q)
        self.events = events

    def process_event(self, event):
        if event == "busy":
            self.events["busy_started"].set()
            self.events["release_busy"].wait(timeout=2)
        elif event == "exclusive":
            Processor.single_thread()
            try:
                self.events["exclusive_started"].set()
                self.events["release_exclusive"].wait(timeout=2)
            finally:
                Processor.free_thread()
        elif event == "normal":
            self.events["normal_started"].set()


def test_single_thread_waits_for_busy_processors_and_blocks_new_events():
    event_q = queue.Queue()
    events = {
        name: threading.Event()
        for name in (
            "busy_started",
            "release_busy",
            "exclusive_started",
            "release_exclusive",
            "normal_started",
        )
    }
    processors = [BarrierProcessor(event_q, events) for _ in range(2)]

    for processor in processors:
        processor.start()

    try:
        event_q.put("busy")
        assert events["busy_started"].wait(timeout=1)

        event_q.put("exclusive")
        assert not events["exclusive_started"].wait(timeout=0.1)

        events["release_busy"].set()
        assert events["exclusive_started"].wait(timeout=1)

        event_q.put("normal")
        assert not events["normal_started"].wait(timeout=0.1)

        events["release_exclusive"].set()
        assert events["normal_started"].wait(timeout=1)
        event_q.join()
    finally:
        events["release_busy"].set()
        events["release_exclusive"].set()
        for processor in processors:
            processor.stop()
        for processor in processors:
            processor.join(timeout=2)

