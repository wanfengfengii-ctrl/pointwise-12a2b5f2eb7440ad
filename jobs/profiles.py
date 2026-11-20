from dataclasses import dataclass


@dataclass(frozen=True)
class ChannelProfile:
    channel: str
    label: str


class ChannelProfiles:
    def __init__(self) -> None:
        self._profiles: dict[str, ChannelProfile] = {}

    def add(self, profile: ChannelProfile) -> None:
        channel = profile.channel
        if not isinstance(channel, str) or not channel or channel.strip() != channel:
            raise ValueError('channel must be a nonempty canonical name')
        if not isinstance(profile.label, str) or not profile.label.strip():
            raise ValueError('channel requires a measurement point label')
        if channel in self._profiles:
            raise ValueError(f'channel already registered: {channel}')
        self._profiles[channel] = profile

    def require(self, channel: str) -> ChannelProfile:
        if not isinstance(channel, str) or channel not in self._profiles:
            raise KeyError(channel)
        return self._profiles[channel]
