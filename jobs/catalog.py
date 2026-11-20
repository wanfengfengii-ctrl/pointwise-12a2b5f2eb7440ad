from dataclasses import dataclass


@dataclass(frozen=True)
class Trial:
    trial_id: str
    channel: str
    limit: int


class TrialCatalog:
    def __init__(self) -> None:
        self._trials: dict[str, Trial] = {}
        self._started: set[str] = set()

    def get(self, trial_id: str) -> Trial | None:
        return self._trials.get(trial_id)

    def add(self, trial: Trial) -> None:
        if trial.trial_id in self._trials:
            raise ValueError(f'trial already registered: {trial.trial_id}')
        self._trials[trial.trial_id] = trial

    def has_started(self, trial_id: str) -> bool:
        return trial_id in self._started

    def mark_started(self, trial_id: str) -> None:
        if trial_id not in self._trials or trial_id in self._started:
            raise ValueError(f'trial unavailable for start: {trial_id}')
        self._started.add(trial_id)
