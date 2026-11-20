from collections.abc import Callable

from device.events import ControlEvent, EventStream, EventSubscription, SampleEvent


class SampleRejectedError(ValueError):
    pass


class DeterministicSource:
    """Assign source positions inside the device port's operation lock."""

    def __init__(self) -> None:
        self._sequence = 0
        self._last_sample_no = 0
        self._stream = EventStream()
        self._controls: list[ControlEvent] = []

    @property
    def last_sample_no(self) -> int:
        return self._last_sample_no

    @property
    def controls(self) -> tuple[ControlEvent, ...]:
        return tuple(self._controls)

    def subscribe(self) -> EventSubscription:
        return self._stream.subscribe()

    def accept_sample(
        self, channel: str, value: int, append: Callable[[SampleEvent], None]
    ) -> SampleEvent:
        if type(value) is not int or value < 0:
            raise SampleRejectedError('reading must be a nonnegative integer in µm/s')
        event = SampleEvent(self._sequence + 1, self._last_sample_no + 1, channel, value)
        append(event)
        self._sequence = event.sequence
        self._last_sample_no = event.sample_no
        self._stream.publish(event)
        return event

    def record_control(
        self,
        action: str,
        channel: str | None,
        slot: int | None = None,
        previous_slot: int | None = None,
    ) -> ControlEvent:
        self._sequence += 1
        event = ControlEvent(self._sequence, action, self._last_sample_no, channel, slot, previous_slot)
        self._controls.append(event)
        self._stream.publish(event)
        return event
