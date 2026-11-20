import json
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from device.events import DeviceEvent
from device.port import DevicePort
from device.registers import RegisterSnapshot


@dataclass(frozen=True)
class PlaybackStep:
    action: str
    channel: str | None = None
    value: int | None = None
    slot: int | None = None

    def __post_init__(self) -> None:
        if self.action == 'sample':
            if not isinstance(self.channel, str) or not self.channel:
                raise ValueError('sample step requires a channel')
            if type(self.value) is not int or self.value < 0 or self.slot is not None:
                raise ValueError('sample step requires a nonnegative integer value')
            return
        if self.action not in ('read', 'switch', 'reset_idle'):
            raise ValueError(f'unsupported recording action: {self.action!r}')
        if self.channel is not None or self.value is not None:
            raise ValueError('slot steps take only a slot argument')
        if self.action == 'read' and self.slot is None:
            return
        if type(self.slot) is not int or self.slot < 0:
            raise ValueError('slot step requires a nonnegative integer slot')


@dataclass(frozen=True)
class PlaybackResult:
    reads: tuple[RegisterSnapshot, ...]
    events: tuple[DeviceEvent, ...]


def load_recording(path: str | Path) -> tuple[PlaybackStep, ...]:
    with Path(path).open(encoding='utf-8') as source:
        recording = json.load(source, parse_int=lambda digits: int(Decimal(digits)))
    if not isinstance(recording, list):
        raise ValueError('recording must be a JSON array')
    steps = []
    for index, attributes in enumerate(recording, start=1):
        try:
            steps.append(PlaybackStep(**attributes))
        except (TypeError, ValueError) as exc:
            raise ValueError(f'invalid recording step {index}: {exc}') from exc
    return tuple(steps)


def run_playback(device: DevicePort, steps: Iterable[PlaybackStep]) -> PlaybackResult:
    reads = []
    with device.subscribe() as subscription:
        for step in steps:
            if step.action == 'sample':
                device.sample(step.channel, step.value)
            elif step.action == 'read':
                reads.append(device.read_slot(step.slot))
            elif step.action == 'switch':
                device.switch_write_slot(step.slot)
            else:
                device.reset_idle_slot(step.slot)
        return PlaybackResult(tuple(reads), subscription.drain())
