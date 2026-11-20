from dataclasses import dataclass

from capture.session import Session, SessionRegistry, SessionStateError
from device.port import DevicePort, DeviceRejectedError, StoppedSlot
from jobs.enrollment import FrozenTrial


@dataclass(frozen=True)
class StoppedEvidence:
    session_id: str
    trial: FrozenTrial
    original: StoppedSlot


def _require_bound_device(session: Session, device: DevicePort) -> None:
    if device.device_id != session.device_id:
        raise SessionStateError('device does not own the session')


def _bind_original(session: Session, original: StoppedSlot) -> StoppedEvidence:
    if (
        original.device_id != session.device_id
        or original.start.action != 'start'
        or original.stop.action != 'stop'
        or original.start.channel != session.trial.channel
        or original.stop.channel != session.trial.channel
        or original.start.sequence != session.start_sequence
        or original.start.last_sample_no != session.start_watermark
        or original.stop.sequence != session.stop_sequence
        or original.stop.last_sample_no != session.stop_watermark
        or original.stop.slot != session.stop_slot
    ):
        raise SessionStateError('held meter slot does not match this session and stop')
    return StoppedEvidence(session.session_id, session.trial, original)


def stop(sessions: SessionRegistry, device: DevicePort, session_id: str) -> StoppedEvidence:
    with sessions.lock:
        session = sessions.require_current(session_id, 'running')
        _require_bound_device(session, device)
        try:
            original = device.stop_sampling()
        except DeviceRejectedError as exc:
            raise SessionStateError(str(exc)) from exc
        event = original.stop
        stopped = sessions.record_stopped(session_id, event.sequence, event.last_sample_no, event.slot)
        return _bind_original(stopped, original)


def require_stopped_evidence(
    sessions: SessionRegistry, device: DevicePort, session_id: str
) -> StoppedEvidence:
    with sessions.lock:
        session = sessions.require_current(session_id, 'stopped')
        _require_bound_device(session, device)
        try:
            original = device.read_stopped_slot()
        except DeviceRejectedError as exc:
            raise SessionStateError(str(exc)) from exc
        return _bind_original(session, original)
