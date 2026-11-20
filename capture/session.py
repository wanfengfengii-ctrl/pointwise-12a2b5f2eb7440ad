from dataclasses import dataclass, replace
from threading import RLock
from typing import Literal

from jobs.enrollment import FrozenTrial


class SessionStateError(RuntimeError):
    pass


@dataclass(frozen=True)
class Session:
    session_id: str
    trial: FrozenTrial
    device_id: str
    start_sequence: int
    start_watermark: int
    state: Literal['running', 'stopped', 'released'] = 'running'
    stop_sequence: int | None = None
    stop_watermark: int | None = None
    stop_slot: int | None = None


class SessionRegistry:
    def __init__(self) -> None:
        self.lock = RLock()
        self._sessions: dict[str, Session] = {}
        self._current_id: str | None = None

    def require(self, session_id: str) -> Session:
        with self.lock:
            if not isinstance(session_id, str) or session_id not in self._sessions:
                raise SessionStateError(f'unknown session: {session_id!r}')
            return self._sessions[session_id]

    @property
    def current(self) -> Session | None:
        with self.lock:
            if self._current_id is None:
                return None
            return self._sessions[self._current_id]

    def require_current(self, session_id: str, state: str) -> Session:
        with self.lock:
            session = self.require(session_id)
            if session_id != self._current_id or session.state != state:
                raise SessionStateError(f'session is not the current {state} session: {session_id}')
            return session

    def record_running(self, session: Session) -> None:
        with self.lock:
            if self._current_id is not None or session.session_id in self._sessions:
                raise SessionStateError('session registry already has an owner or this identity')
            if session.state != 'running':
                raise SessionStateError('new session must be running')
            self._sessions[session.session_id] = session
            self._current_id = session.session_id

    def record_stopped(
        self, session_id: str, sequence: int, watermark: int, slot: int
    ) -> Session:
        with self.lock:
            session = self.require_current(session_id, 'running')
            stopped = replace(
                session, state='stopped', stop_sequence=sequence,
                stop_watermark=watermark, stop_slot=slot,
            )
            self._sessions[session_id] = stopped
            return stopped

    def record_released(self, session_id: str) -> None:
        with self.lock:
            session = self.require_current(session_id, 'stopped')
            self._sessions[session_id] = replace(session, state='released')
            self._current_id = None
