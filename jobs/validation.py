from jobs.catalog import TrialCatalog
from jobs.profiles import ChannelProfile, ChannelProfiles


class RegistrationError(ValueError):
    pass


def validate_limit(limit: int) -> int:
    if type(limit) is not int or limit < 0:
        raise RegistrationError('limit must be a nonnegative integer in µm/s')
    return limit


def validate_registration(
    catalog: TrialCatalog,
    profiles: ChannelProfiles,
    trial_id: str,
    channel: str,
    limit: int,
) -> ChannelProfile:
    if not isinstance(trial_id, str) or not trial_id.strip():
        raise RegistrationError('trial id must be a nonempty string')
    if catalog.get(trial_id) is not None:
        raise RegistrationError(f'trial already registered: {trial_id}')
    try:
        profile = profiles.require(channel)
    except KeyError as exc:
        raise RegistrationError(f'unknown measurement channel: {channel!r}') from exc
    validate_limit(limit)
    return profile
