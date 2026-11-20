from dataclasses import dataclass

from device.registers import RegisterSnapshot


class MetricsError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedMetrics:
    count: int
    first_sample_no: int | None
    last_sample_no: int | None
    peak: int | None
    peak_sample_no: int | None


def verify_metrics(snapshot: RegisterSnapshot, start: int, stop: int) -> VerifiedMetrics:
    if not isinstance(snapshot, RegisterSnapshot):
        raise MetricsError('meter snapshot required')
    if type(start) is not int or type(stop) is not int or not 0 <= start <= stop:
        raise MetricsError('invalid device watermarks')
    count = stop - start
    if type(snapshot.count) is not int or snapshot.count != count:
        raise MetricsError('meter count disagrees with device watermarks')
    first, last = snapshot.first_sample_no, snapshot.last_sample_no
    peak, peak_no = snapshot.peak, snapshot.peak_sample_no
    if count == 0:
        if any(field is not None for field in (first, last, peak, peak_no, snapshot.peak_sample)):
            raise MetricsError('an empty meter must have empty sample and peak fields')
    else:
        if type(first) is not int or type(last) is not int or (first, last) != (start + 1, stop):
            raise MetricsError('meter range disagrees with device watermarks')
        if type(peak) is not int or peak < 0:
            raise MetricsError('invalid peak reading')
        if type(peak_no) is not int or not first <= peak_no <= last:
            raise MetricsError('peak sample is outside the measured range')
    return VerifiedMetrics(count, first, last, peak, peak_no)
