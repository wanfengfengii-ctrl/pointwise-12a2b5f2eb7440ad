from dataclasses import dataclass

from capture.stop import StoppedEvidence
from jobs.enrollment import FrozenTrial
from quality.metrics import MetricsError, VerifiedMetrics, verify_metrics
from quality.provenance import ProvenanceError, verify_peak_origin, verify_stop_origin


class EvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedEvidence:
    stopped: StoppedEvidence
    metrics: VerifiedMetrics


def verify_evidence(original: StoppedEvidence, submitted: StoppedEvidence) -> VerifiedEvidence:
    if not isinstance(submitted, StoppedEvidence) or submitted.session_id != original.session_id:
        raise EvidenceError('submitted evidence must identify the stopped session')
    if (
        not isinstance(submitted.trial, FrozenTrial)
        or type(submitted.trial.limit) is not int
        or submitted.trial != original.trial
    ):
        raise EvidenceError('submitted trial differs from the frozen trial')
    try:
        verify_stop_origin(original, submitted)
        snapshot = submitted.original.snapshot
        metrics = verify_metrics(
            snapshot, original.original.start.last_sample_no, original.original.stop.last_sample_no
        )
        verify_peak_origin(original.original.snapshot, snapshot, original.trial.channel)
    except (MetricsError, ProvenanceError) as exc:
        raise EvidenceError(str(exc)) from exc
    return VerifiedEvidence(submitted, metrics)
