import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.issue import issue
from archive.store import ArchiveStore
from capture.control import start
from capture.reset import reset
from capture.session import SessionRegistry
from capture.stop import stop
from device.port import DevicePort
from jobs.catalog import TrialCatalog
from jobs.enrollment import register
from jobs.profiles import ChannelProfile, ChannelProfiles


CHANNEL = 'pump-body'
RECORDINGS = Path(__file__).resolve().parents[1] / 'recordings'


class CommitFault:
    def __init__(self):
        self._armed = False
        self._probe = None

    def arm(self, probe=None):
        if self._armed:
            raise ValueError('a commit fault is already armed')
        self._armed = True
        self._probe = probe

    def __call__(self):
        if not self._armed:
            return
        self._armed = False
        probe, self._probe = self._probe, None
        if probe is not None:
            probe()
        raise sqlite3.OperationalError('injected failure before issue commit')


class BenchCase(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix='pump-bench-test-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.path = self.directory / 'reports.sqlite3'
        self.device = DevicePort()
        self.addCleanup(self.device.close)
        self.catalog = TrialCatalog()
        self.channels = ChannelProfiles()
        self.channels.add(ChannelProfile(CHANNEL, '泵体测点'))
        self.sessions = SessionRegistry()
        self.fault = CommitFault()
        self.store = self.open_archive(before_commit=self.fault)

    def open_archive(self, *, before_commit=None):
        store = ArchiveStore.open(self.path, before_commit=before_commit)
        self.addCleanup(store.close)
        return store

    def register(self, trial_id, limit=9):
        return register(self.catalog, self.channels, trial_id, CHANNEL, limit)

    def start(self, trial_id):
        return start(self.catalog, self.sessions, self.device, trial_id)

    def feed(self, *readings):
        return tuple(self.device.sample(CHANNEL, reading) for reading in readings)

    def stop(self, session_id):
        return stop(self.sessions, self.device, session_id)

    def issue(self, session_id, evidence):
        return issue(self.store, self.sessions, self.device, session_id, evidence)

    def reset(self, session_id):
        reset(self.sessions, self.device, self.store, session_id)

    def complete(self, trial_id, readings=(), limit=9):
        self.register(trial_id, limit)
        session_id = self.start(trial_id)
        self.feed(*readings)
        evidence = self.stop(session_id)
        issued = self.issue(session_id, evidence)
        self.reset(session_id)
        return issued

    def raw_counts(self):
        with closing(sqlite3.connect(self.path)) as reader:
            reports = reader.execute('SELECT COUNT(*) FROM reports').fetchone()[0]
            receipts = reader.execute('SELECT COUNT(*) FROM receipts').fetchone()[0]
        return reports, receipts
