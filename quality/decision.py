from dataclasses import dataclass

from quality.evidence import VerifiedEvidence
from quality.limits import LimitOutcome, classify_peak


@dataclass(frozen=True)
class Verdict:
    evidence: VerifiedEvidence
    outcome: LimitOutcome


def decide(evidence: VerifiedEvidence) -> Verdict:
    return Verdict(evidence, classify_peak(evidence.metrics.peak, evidence.stopped.trial.limit))
