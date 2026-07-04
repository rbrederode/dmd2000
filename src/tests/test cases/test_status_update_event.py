from queue import Queue

from env.events import StatusUpdateEvent


def test_status_update_processing_time_excludes_queue_wait(monkeypatch):
    event = StatusUpdateEvent()
    queue = Queue()

    times = iter([100.0, 104.0, 104.25])
    monkeypatch.setattr("env.events.time.time", lambda: next(times))

    event.enqueue(queue)
    event.notify_dequeued()
    event.notify_update_completed()

    assert event.get_dequeued_count() == 1
    assert event.get_total_processing_time() == 250.0
    assert event.get_average_processing_time() == 250.0
    event_str = str(event)
    assert "Updated Timestamp=[None]" not in event_str
    assert "Queue Time (ms)=4000.0" in event_str
    assert "Processing Time (ms)=250.0" in event_str
    assert "Average Processing Time (ms)=250.0" in event_str
    assert "Total Processing Count=" not in event_str
    assert "Total Processing Time (ms)=" not in event_str
