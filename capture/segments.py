from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from capture.session import Session, SessionStateError
from device.events import ControlEvent
from device.port import SegmentCut
from device.registers import RegisterSnapshot


class RequestPhase(StrEnum):
    PENDING = 'pending'
    CUSTODY = 'custody'
    ISSUED = 'issued'


@dataclass(frozen=True)
class SegmentOriginal:
    """The capture-side original of one segment, held until durable custody."""

    session_id: str
    device_id: str
    channel: str
    limit: int
    request_id: str
    previous: ControlEvent
    boundary: ControlEvent
    snapshot: RegisterSnapshot


class RequestEntry:
    def __init__(self, request_id: str, original: SegmentOriginal) -> None:
        self.request_id = request_id
        self.original = original
        self.phase = RequestPhase.PENDING
        self.report_id: str | None = None

    @property
    def in_flight(self) -> bool:
        return self.phase in (RequestPhase.PENDING, RequestPhase.CUSTODY)


class ContinuousBook:
    """In-process book for the cut requests of one continuous session."""

    def __init__(self, session_id: str, start: ControlEvent) -> None:
        self.session_id = session_id
        self._previous = start
        self._requests: dict[str, RequestEntry] = {}
        self._active: RequestEntry | None = None

    def begin(self, session: Session, request_id: str, cut: SegmentCut) -> RequestEntry:
        if self._active is not None and self._active.in_flight:
            raise SessionStateError('a cut request is already in progress')
        if request_id in self._requests:
            raise SessionStateError('the request id already belongs to this session')
        original = SegmentOriginal(
            session.session_id,
            session.device_id,
            session.trial.channel,
            session.trial.limit,
            request_id,
            self._previous,
            cut.boundary,
            cut.snapshot,
        )
        entry = RequestEntry(request_id, original)
        self._requests[request_id] = entry
        self._active = entry
        return entry

    def get(self, request_id: str) -> RequestEntry | None:
        return self._requests.get(request_id)

    def has_in_flight(self) -> bool:
        return self._active is not None and self._active.in_flight

    def active(self) -> RequestEntry | None:
        return self._active

    def mark_custody(self, entry: RequestEntry) -> None:
        entry.phase = RequestPhase.CUSTODY
        self._previous = entry.original.boundary

    def mark_issued(self, entry: RequestEntry, report_id: str) -> None:
        entry.phase = RequestPhase.ISSUED
        entry.report_id = report_id
        self._previous = entry.original.boundary
        if self._active is entry:
            self._active = None


class ContinuousLedger:
    def __init__(self) -> None:
        self._books: dict[str, ContinuousBook] = {}

    def open(self, session_id: str, start: ControlEvent) -> ContinuousBook:
        if session_id in self._books:
            raise SessionStateError('continuous book already exists for this session')
        book = ContinuousBook(session_id, start)
        self._books[session_id] = book
        return book

    def require(self, session_id: str) -> ContinuousBook:
        book = self._books.get(session_id)
        if book is None:
            raise SessionStateError('session has no continuous cut book')
        return book
