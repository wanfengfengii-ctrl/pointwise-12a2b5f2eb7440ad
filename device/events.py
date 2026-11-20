from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class SampleEvent:
    sequence: int
    sample_no: int
    channel: str
    value: int


@dataclass(frozen=True)
class ControlEvent:
    sequence: int
    action: str
    last_sample_no: int
    channel: str | None
    slot: int | None = None
    previous_slot: int | None = None


DeviceEvent = SampleEvent | ControlEvent


class EventSubscription:
    def __init__(self, stream: EventStream) -> None:
        self._stream = stream
        self._pending: deque[DeviceEvent] = deque()

    def drain(self) -> tuple[DeviceEvent, ...]:
        with self._stream._lock:
            events = tuple(self._pending)
            self._pending.clear()
            return events

    def close(self) -> None:
        with self._stream._lock:
            self._stream._subscribers.discard(self)
            self._pending.clear()

    def __enter__(self) -> EventSubscription:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


class EventStream:
    def __init__(self) -> None:
        self._lock = Lock()
        self._subscribers: set[EventSubscription] = set()

    def subscribe(self) -> EventSubscription:
        with self._lock:
            subscription = EventSubscription(self)
            self._subscribers.add(subscription)
            return subscription

    def publish(self, event: DeviceEvent) -> None:
        with self._lock:
            for subscriber in self._subscribers:
                subscriber._pending.append(event)
