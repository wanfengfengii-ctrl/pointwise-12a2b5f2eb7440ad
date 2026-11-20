from dataclasses import dataclass

from jobs.catalog import Trial, TrialCatalog
from jobs.profiles import ChannelProfiles
from jobs.validation import RegistrationError, validate_registration


@dataclass(frozen=True)
class FrozenTrial:
    trial_id: str
    channel: str
    limit: int


def register(
    catalog: TrialCatalog,
    profiles: ChannelProfiles,
    trial_id: str,
    channel: str,
    limit: int,
) -> Trial:
    profile = validate_registration(catalog, profiles, trial_id, channel, limit)
    trial = Trial(trial_id, profile.channel, limit)
    catalog.add(trial)
    return trial


def prepare_start(catalog: TrialCatalog, trial_id: str) -> FrozenTrial:
    if not isinstance(trial_id, str):
        raise RegistrationError('trial id must be a string')
    trial = catalog.get(trial_id)
    if trial is None:
        raise RegistrationError(f'unknown trial: {trial_id}')
    if catalog.has_started(trial_id):
        raise RegistrationError(f'trial already started: {trial_id}')
    return FrozenTrial(trial.trial_id, trial.channel, trial.limit)


def confirm_start(catalog: TrialCatalog, frozen: FrozenTrial) -> None:
    if prepare_start(catalog, frozen.trial_id) != frozen:
        raise RegistrationError('confirmed trial differs from the prepared trial')
    catalog.mark_started(frozen.trial_id)
