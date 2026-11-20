from uuid import uuid4

from archive.receipts import make_receipt
from archive.schema import Report, SchemaError, encode_evidence
from archive.store import ArchiveConflictError, ArchiveStore, IssuedRecord
from capture.session import SessionRegistry, SessionStateError
from capture.stop import StoppedEvidence, require_stopped_evidence
from device.port import DevicePort
from quality.decision import decide
from quality.evidence import EvidenceError, verify_evidence


class IssueError(ValueError):
    pass


def _matches_replay(evidence: StoppedEvidence | None, original: StoppedEvidence) -> bool:
    if not isinstance(evidence, StoppedEvidence) or evidence.session_id != original.session_id:
        return False
    try:
        return encode_evidence(evidence) == encode_evidence(original)
    except (SchemaError, AttributeError, TypeError):
        return False


def issue(
    store: ArchiveStore,
    sessions: SessionRegistry,
    device: DevicePort | None,
    session_id: str,
    evidence: StoppedEvidence | None,
) -> IssuedRecord:
    if not isinstance(session_id, str):
        raise IssueError('session id must be a string')
    with sessions.lock:
        existing = store.find_by_session(session_id)
        if existing is not None:
            if not _matches_replay(evidence, existing.report.evidence):
                raise IssueError('submitted evidence differs from the already issued stop')
            return existing
        if device is None:
            raise IssueError('initial issue requires the held device stop')
        try:
            original = require_stopped_evidence(sessions, device, session_id)
            verified = verify_evidence(original, evidence)
            verdict = decide(verified)
            report = Report(uuid4().hex, verdict.evidence.stopped, verdict.outcome)
            return store.publish(report, make_receipt(report))
        except (SessionStateError, EvidenceError, ArchiveConflictError, SchemaError) as exc:
            raise IssueError(str(exc)) from exc
