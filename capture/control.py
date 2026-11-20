from uuid import uuid4

from capture.session import Session, SessionRegistry, SessionStateError
from device.port import DevicePort, DeviceRejectedError
from jobs.catalog import TrialCatalog
from jobs.enrollment import confirm_start, prepare_start
from jobs.validation import RegistrationError


class StartError(SessionStateError):
    pass


def start(
    catalog: TrialCatalog, sessions: SessionRegistry, device: DevicePort, trial_id: str
) -> str:
    with sessions.lock:
        if sessions.current is not None:
            raise StartError('the trial bench has an active owner')
        try:
            frozen = prepare_start(catalog, trial_id)
            event = device.start_sampling(frozen.channel)
        except (RegistrationError, DeviceRejectedError) as exc:
            raise StartError(str(exc)) from exc
        session = Session(
            uuid4().hex, frozen, device.device_id, event.sequence, event.last_sample_no
        )
        sessions.record_running(session)
        confirm_start(catalog, frozen)
        return session.session_id
