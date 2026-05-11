import queue

from env.processor import Processor


class IdleProcessor(Processor):
    def process_event(self, event) -> bool:
        return True


def test_stopping_one_processor_does_not_stop_others():
    event_q = queue.Queue()
    processor_a = IdleProcessor(name="processor-a", event_q=event_q)
    processor_b = IdleProcessor(name="processor-b", event_q=event_q)

    processor_a.start()
    processor_b.start()

    processor_a.stop()
    processor_a.join(timeout=2)

    assert not processor_a.is_alive()
    assert processor_b.is_alive()

    processor_b.stop()
    processor_b.join(timeout=2)
    assert not processor_b.is_alive()


def test_stop_all_stops_all_live_processors():
    event_q = queue.Queue()
    processor_a = IdleProcessor(name="processor-a", event_q=event_q)
    processor_b = IdleProcessor(name="processor-b", event_q=event_q)

    processor_a.start()
    processor_b.start()

    Processor.stop_all()

    processor_a.join(timeout=2)
    processor_b.join(timeout=2)

    assert not processor_a.is_alive()
    assert not processor_b.is_alive()
