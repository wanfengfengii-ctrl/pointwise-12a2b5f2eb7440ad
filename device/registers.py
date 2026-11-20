from dataclasses import dataclass

from device.events import SampleEvent


@dataclass(frozen=True)
class RegisterSnapshot:
    count: int
    first_sample_no: int | None
    last_sample_no: int | None
    peak: int | None
    peak_sample_no: int | None
    peak_sample: SampleEvent | None


class MeterRegister:
    def __init__(self) -> None:
        self.clear()

    def append(self, sample: SampleEvent) -> None:
        if self._count == 0:
            self._first = sample.sample_no
        self._count += 1
        self._last = sample.sample_no
        if self._peak is None or sample.value > self._peak.value:
            self._peak = sample

    def snapshot(self) -> RegisterSnapshot:
        peak = self._peak
        return RegisterSnapshot(
            self._count,
            self._first,
            self._last,
            None if peak is None else peak.value,
            None if peak is None else peak.sample_no,
            peak,
        )

    def clear(self) -> None:
        self._count = 0
        self._first: int | None = None
        self._last: int | None = None
        self._peak: SampleEvent | None = None
