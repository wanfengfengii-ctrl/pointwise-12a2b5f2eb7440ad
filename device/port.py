from dataclasses import dataclass
from threading import Lock
from uuid import uuid4

from device.events import ControlEvent, EventSubscription
from device.registers import MeterRegister, RegisterSnapshot
from device.source import DeterministicSource, SampleRejectedError


class DeviceRejectedError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoppedSlot:
    device_id: str
    start: ControlEvent
    stop: ControlEvent
    snapshot: RegisterSnapshot


@dataclass(frozen=True)
class SegmentCut:
    """The boundary event and the finished segment meter at a continuous cut."""

    boundary: ControlEvent
    snapshot: RegisterSnapshot


class DevicePort:
    def __init__(self) -> None:
        self._device_id = uuid4().hex
        self._lock = Lock()
        self._source = DeterministicSource()
        self._slots = (MeterRegister(), MeterRegister())
        self._write_slot = 0
        self._segment = MeterRegister()
        self._continuous = False
        self._channel: str | None = None
        self._start: ControlEvent | None = None
        self._stopped: StoppedSlot | None = None
        self._running = False
        self._closed = False

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def actions(self) -> tuple[ControlEvent, ...]:
        with self._lock:
            return self._source.controls

    @property
    def last_sample_no(self) -> int:
        with self._lock:
            return self._source.last_sample_no

    def _require_open(self) -> None:
        if self._closed:
            raise DeviceRejectedError('device is closed')

    def _register(self, slot: int) -> MeterRegister:
        if type(slot) is not int or not 0 <= slot < len(self._slots):
            raise DeviceRejectedError(f'unknown meter slot: {slot!r}')
        return self._slots[slot]

    def start_sampling(self, channel: str, *, continuous: bool = False) -> ControlEvent:
        with self._lock:
            self._require_open()
            if not isinstance(channel, str) or not channel or channel.strip() != channel:
                raise DeviceRejectedError('device needs a canonical channel name')
            if self._running or self._stopped is not None:
                raise DeviceRejectedError('device is occupied')
            if any(slot.snapshot().count for slot in self._slots):
                raise DeviceRejectedError('meter slots must be reset before starting')
            self._write_slot = 0
            self._channel = channel
            self._running = True
            self._segment.clear()
            self._continuous = continuous
            self._start = self._source.record_control('start', channel, self._write_slot)
            return self._start

    def sample(self, channel: str, value: int) -> int:
        with self._lock:
            self._require_open()
            if not self._running:
                raise SampleRejectedError('device is not sampling')
            if channel != self._channel:
                raise SampleRejectedError('sample channel differs from active channel')
            register = self._slots[self._write_slot]

            def append(event):
                register.append(event)
                self._segment.append(event)

            return self._source.accept_sample(channel, value, append).sample_no

    def read_slot(self, slot: int | None = None) -> RegisterSnapshot:
        with self._lock:
            self._require_open()
            return self._register(self._write_slot if slot is None else slot).snapshot()

    def stop_sampling(self) -> StoppedSlot:
        with self._lock:
            self._require_open()
            if not self._running or self._start is None:
                raise DeviceRejectedError('device is not sampling')
            self._running = False
            event = self._source.record_control('stop', self._channel, self._write_slot)
            self._stopped = StoppedSlot(
                self._device_id, self._start, event, self._slots[self._write_slot].snapshot()
            )
            self._continuous = False
            self._segment.clear()
            return self._stopped

    def cut_sampling(self) -> SegmentCut:
        """Record a continuous segment boundary while sampling keeps running."""
        with self._lock:
            self._require_open()
            if not self._running or self._start is None:
                raise DeviceRejectedError('device is not sampling')
            if not self._continuous:
                raise DeviceRejectedError('segment cuts require a continuous sampling run')
            event = self._source.record_control('cut', self._channel, self._write_slot)
            snapshot = self._segment.snapshot()
            self._segment.clear()
            return SegmentCut(event, snapshot)

    def read_stopped_slot(self) -> StoppedSlot:
        with self._lock:
            self._require_open()
            if self._stopped is None:
                raise DeviceRejectedError('device holds no stopped slot')
            return self._stopped

    def switch_write_slot(self, slot: int) -> ControlEvent:
        with self._lock:
            self._require_open()
            target = self._register(slot)
            if not self._running or slot == self._write_slot or target.snapshot().count:
                raise DeviceRejectedError('switch requires an empty inactive slot during sampling')
            previous = self._write_slot
            self._write_slot = slot
            return self._source.record_control('switch', self._channel, slot, previous)

    def reset_idle_slot(self, slot: int) -> ControlEvent:
        with self._lock:
            self._require_open()
            register = self._register(slot)
            if self._running and slot == self._write_slot:
                raise DeviceRejectedError('cannot clear the write slot')
            if self._stopped is not None and slot == self._stopped.stop.slot:
                raise DeviceRejectedError('stopped slot requires release')
            register.clear()
            return self._source.record_control('reset_idle', self._channel, slot)

    def release_stopped_slot(self, expected: StoppedSlot) -> None:
        with self._lock:
            self._require_open()
            original = self._stopped
            if original is None or original != expected:
                raise DeviceRejectedError('release does not match the held stop')
            slot = original.stop.slot
            self._register(slot).clear()
            self._source.record_control('reset_stopped', self._channel, slot)
            self._stopped = None
            self._start = None
            self._channel = None
            self._write_slot = 0
            self._continuous = False
            self._segment.clear()

    def subscribe(self) -> EventSubscription:
        with self._lock:
            self._require_open()
            return self._source.subscribe()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._source.record_control('close', self._channel)
            self._running = False
            self._closed = True
