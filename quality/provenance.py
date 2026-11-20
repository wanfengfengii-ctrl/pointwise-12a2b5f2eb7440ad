from capture.stop import StoppedEvidence
from device.events import ControlEvent, SampleEvent
from device.port import StoppedSlot
from device.registers import RegisterSnapshot


class ProvenanceError(ValueError):
    pass


def verify_stop_origin(original: StoppedEvidence, submitted: StoppedEvidence) -> None:
    actual = original.original
    claimed = submitted.original
    if not isinstance(claimed, StoppedSlot):
        raise ProvenanceError('submitted evidence has no device stop')
    if not isinstance(claimed.start, ControlEvent) or not isinstance(claimed.stop, ControlEvent):
        raise ProvenanceError('submitted evidence has malformed control facts')
    if (
        claimed.device_id != actual.device_id
        or claimed.start != actual.start
        or claimed.stop != actual.stop
    ):
        raise ProvenanceError('device or physical start and stop facts differ from the original')


def verify_peak_origin(
    original: RegisterSnapshot, submitted: RegisterSnapshot, channel: str
) -> None:
    peak = submitted.peak_sample
    if original.peak_sample is None:
        if peak is not None:
            raise ProvenanceError('there is no original peak sample')
        return
    if not isinstance(peak, SampleEvent) or peak != original.peak_sample:
        raise ProvenanceError('submitted peak is not the original first peak sample')
    if (
        type(peak.sample_no) is not int
        or type(peak.sequence) is not int
        or type(peak.value) is not int
        or peak.channel != channel
        or peak.sample_no != submitted.peak_sample_no
        or peak.value != submitted.peak
    ):
        raise ProvenanceError('peak fields disagree with the device sample')
