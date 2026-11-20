from archive.readback import find_committed_issue
from archive.store import ArchiveStore
from capture.session import SessionRegistry, SessionStateError
from capture.stop import require_stopped_evidence
from device.port import DevicePort, DeviceRejectedError


class ResetError(SessionStateError):
    pass


def reset(sessions: SessionRegistry, device: DevicePort, store: ArchiveStore, session_id: str) -> None:
    with sessions.lock:
        try:
            evidence = require_stopped_evidence(sessions, device, session_id)
        except SessionStateError as exc:
            raise ResetError(str(exc)) from exc
        issued = find_committed_issue(store, session_id)
        if issued is None or issued.report.evidence != evidence:
            raise ResetError('release requires a committed issue for this stop')
        try:
            device.release_stopped_slot(evidence.original)
        except DeviceRejectedError as exc:
            raise ResetError(str(exc)) from exc
        sessions.record_released(session_id)
