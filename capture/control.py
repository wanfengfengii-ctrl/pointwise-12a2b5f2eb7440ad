from dataclasses import dataclass
from enum import StrEnum
from uuid import uuid4

from archive.receipts import make_receipt
from archive.segment_schema import SegmentReport
from archive.store import (
    ArchiveCommitError,
    ArchiveConflictError,
    ArchiveStore,
    SegmentIssuedRecord,
)
from capture.segments import ContinuousLedger, RequestPhase
from capture.session import Session, SessionRegistry, SessionStateError
from device.port import DevicePort, DeviceRejectedError
from jobs.catalog import TrialCatalog
from jobs.enrollment import confirm_start, prepare_start
from jobs.validation import RegistrationError
from quality.limits import classify_peak


class StartError(SessionStateError):
    pass


class CutStatus(StrEnum):
    ISSUED = 'issued'
    PENDING = 'pending'
    BUSY = 'busy'


@dataclass(frozen=True)
class CutResult:
    status: CutStatus
    issued: SegmentIssuedRecord | None


def start(
    catalog: TrialCatalog,
    sessions: SessionRegistry,
    device: DevicePort,
    trial_id: str,
    *,
    continuous: bool = False,
) -> str:
    with sessions.lock:
        if sessions.current is not None:
            raise StartError('the trial bench has an active owner')
        try:
            frozen = prepare_start(catalog, trial_id)
            event = device.start_sampling(frozen.channel, continuous=continuous)
        except (RegistrationError, DeviceRejectedError) as exc:
            raise StartError(str(exc)) from exc
        session = Session(
            uuid4().hex, frozen, device.device_id, event.sequence, event.last_sample_no,
            continuous=continuous,
        )
        sessions.record_running(session)
        if continuous:
            if sessions.ledger is None:
                sessions.ledger = ContinuousLedger()
            sessions.ledger.open(session.session_id, event)
        confirm_start(catalog, frozen)
        return session.session_id


def _verify_segment_metrics(session: Session, original) -> None:
    start = original.previous.last_sample_no
    stop = original.boundary.last_sample_no
    if not (0 <= start <= stop):
        raise SessionStateError('invalid segment watermarks')
    snapshot = original.snapshot
    if snapshot.count != stop - start:
        raise SessionStateError('segment meter count disagrees with cut watermarks')
    if snapshot.count == 0:
        if any(
            field is not None
            for field in (
                snapshot.first_sample_no, snapshot.last_sample_no,
                snapshot.peak, snapshot.peak_sample_no, snapshot.peak_sample,
            )
        ):
            raise SessionStateError('an empty segment must have empty sample and peak fields')
        return
    if (snapshot.first_sample_no, snapshot.last_sample_no) != (start + 1, stop):
        raise SessionStateError('segment sample range disagrees with cut watermarks')
    peak = snapshot.peak_sample
    if peak is None or peak.sample_no != snapshot.peak_sample_no or peak.value != snapshot.peak:
        raise SessionStateError('segment peak does not match its original sample event')
    if peak.channel != session.trial.channel:
        raise SessionStateError('segment peak channel differs from the frozen channel')


def cut(
    store: ArchiveStore,
    sessions: SessionRegistry,
    device: DevicePort,
    session_id: str,
    request_id: str,
) -> CutResult:
    if not isinstance(request_id, str) or not request_id:
        raise SessionStateError('cut request id must be a nonempty string')
    if device is None:
        raise SessionStateError('a cut requires the owning device')
    with sessions.lock:
        session = sessions.require(session_id)
        if not session.continuous:
            raise SessionStateError('session is not a continuous sampling session')
        if session.state != 'running':
            raise SessionStateError('continuous session is not running')
        if device.device_id != session.device_id:
            raise SessionStateError('device does not own the continuous session')
        ledger = sessions.ledger
        if ledger is None:
            raise SessionStateError('continuous session has no cut book')
        book = ledger.require(session_id)
        entry = book.get(request_id)
        if entry is not None:
            if entry.phase is RequestPhase.ISSUED:
                record = store.find_segment_by_request(session_id, request_id)
                if record is None:
                    raise SessionStateError('issued cut is missing from the archive')
                return CutResult(CutStatus.ISSUED, record)
            if book.has_in_flight() and book.active() is not entry:
                raise SessionStateError('another cut request is already in progress')
        else:
            if book.has_in_flight():
                return CutResult(CutStatus.BUSY, None)
            try:
                segment = device.cut_sampling()
            except DeviceRejectedError as exc:
                raise SessionStateError(str(exc)) from exc
            entry = book.begin(session, request_id, segment)
            _verify_segment_metrics(session, entry.original)
        original = entry.original
        report_id = entry.report_id or uuid4().hex
        entry.report_id = report_id
        report = SegmentReport(
            report_id, original, classify_peak(original.snapshot.peak, original.limit)
        )
        if entry.phase is RequestPhase.PENDING:
            try:
                store.commit_custody(report)
            except ArchiveCommitError:
                return CutResult(CutStatus.PENDING, None)
            book.mark_custody(entry)
        try:
            record = store.publish_segment(report, make_receipt(report))
        except (ArchiveCommitError, ArchiveConflictError):
            return CutResult(CutStatus.PENDING, None)
        book.mark_issued(entry, report_id)
        return CutResult(CutStatus.ISSUED, record)
