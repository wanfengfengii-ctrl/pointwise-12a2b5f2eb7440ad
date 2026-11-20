from enum import StrEnum


class LimitOutcome(StrEnum):
    PASS = 'pass'
    FAIL = 'fail'
    NO_DATA = 'no_data'


def classify_peak(peak: int | None, limit: int) -> LimitOutcome:
    if peak is None:
        return LimitOutcome.NO_DATA
    if peak <= limit:
        return LimitOutcome.PASS
    return LimitOutcome.FAIL
