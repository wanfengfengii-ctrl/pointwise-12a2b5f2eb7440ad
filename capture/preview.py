from capture.session import SessionRegistry, SessionStateError
from device.port import DevicePort, DeviceRejectedError
from device.registers import RegisterSnapshot


def preview(sessions: SessionRegistry, device: DevicePort, session_id: str) -> RegisterSnapshot:
    with sessions.lock:
        session = sessions.require_current(session_id, 'running')
        if device.device_id != session.device_id:
            raise SessionStateError('preview device does not own the session')
        try:
            return device.read_slot()
        except DeviceRejectedError as exc:
            raise SessionStateError(str(exc)) from exc
