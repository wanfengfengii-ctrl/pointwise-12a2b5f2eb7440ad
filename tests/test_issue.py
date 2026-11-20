import json
from dataclasses import replace

from archive.issue import IssueError, issue
from archive.readback import find_committed_issue, read
from archive.store import ArchiveCommitError
from capture.preview import preview
from capture.reset import ResetError
from capture.session import SessionRegistry, SessionStateError
from capture.stop import require_stopped_evidence
from device.port import DeviceRejectedError
from quality.limits import LimitOutcome
from tests.support import BenchCase


class IssueTests(BenchCase):
    def test_running_cannot_issue(self):
        self.register('running')
        session_id = self.start('running')
        self.feed(5)
        with self.assertRaises(IssueError):
            self.issue(session_id, None)
        self.assertEqual(self.raw_counts(), (0, 0))
        self.assertIsNone(find_committed_issue(self.store, session_id))
        self.assertEqual(self.sessions.require(session_id).state, 'running')
        self.feed(6)
        self.assertEqual(preview(self.sessions, self.device, session_id).count, 2)

    def test_stop_guard(self):
        self.register('stop-guard')
        session_id = self.start('stop-guard')
        self.feed(4)
        with self.assertRaises(SessionStateError):
            self.stop('unknown')
        self.assertEqual(preview(self.sessions, self.device, session_id).count, 1)
        evidence = self.stop(session_id)
        with self.assertRaises(SessionStateError):
            self.stop(session_id)
        with self.assertRaises(DeviceRejectedError):
            self.device.reset_idle_slot(evidence.original.stop.slot)
        self.assertEqual(require_stopped_evidence(self.sessions, self.device, session_id), evidence)
        self.assertEqual([entry.action for entry in self.device.actions], ['start', 'stop'])
        self.assertEqual(self.issue(session_id, evidence).report.evidence, evidence)

    def test_peak_at_limit(self):
        self.register('equal', 9)
        session_id = self.start('equal')
        self.feed(4, 9, 9)
        evidence = self.stop(session_id)
        issued = self.issue(session_id, evidence)
        meter = issued.report.evidence.original.snapshot
        self.assertEqual((meter.count, meter.first_sample_no, meter.last_sample_no), (3, 1, 3))
        self.assertEqual((meter.peak, meter.peak_sample_no), (9, 2))
        self.assertEqual(issued.report.evidence.trial.limit, 9)
        self.assertEqual(issued.report.outcome, LimitOutcome.PASS)
        self.assertEqual(self.issue(session_id, evidence), issued)

    def test_foreign_stop(self):
        issued_first = self.complete('A')
        self.register('B')
        second = self.start('B')
        genuine = self.stop(second)
        foreign = issued_first.report.evidence.original
        self.assertEqual(foreign.snapshot, genuine.original.snapshot)
        self.assertEqual(foreign.stop.last_sample_no, genuine.original.stop.last_sample_no)
        self.assertNotEqual(foreign.stop.sequence, genuine.original.stop.sequence)
        with self.assertRaises(IssueError):
            self.issue(second, replace(genuine, original=foreign))
        self.assertIsNone(find_committed_issue(self.store, second))
        self.assertEqual(self.raw_counts(), (1, 1))
        self.assertEqual(self.issue(second, genuine).report.evidence.original, genuine.original)

    def test_metric_checks(self):
        self.register('meter-checks')
        session_id = self.start('meter-checks')
        with self.device.subscribe() as samples:
            self.feed(4, 9, 9)
            later_peak = samples.drain()[-1]
        evidence = self.stop(session_id)
        snapshot = evidence.original.snapshot
        alterations = {
            'count': replace(snapshot, count=2),
            'first': replace(snapshot, first_sample_no=2),
            'last': replace(snapshot, last_sample_no=4),
            'later_peak': replace(snapshot, peak_sample_no=3, peak_sample=later_peak),
            'peak_value': replace(snapshot, peak=8),
            'missing_witness': replace(snapshot, peak_sample=None),
        }
        for name, altered in alterations.items():
            with self.subTest(name=name):
                with self.assertRaises(IssueError):
                    self.issue(session_id, replace(evidence, original=replace(evidence.original, snapshot=altered)))
                self.assertEqual(self.raw_counts(), (0, 0))
        self.assertEqual(self.issue(session_id, evidence).report.evidence, evidence)

    def test_frozen_limit(self):
        self.register('frozen', 9)
        active = self.start('frozen')
        self.register('lower-limit', 8)
        self.feed(9)
        evidence = self.stop(active)
        with self.assertRaises(IssueError):
            self.issue(active, replace(evidence, trial=replace(evidence.trial, limit=8)))
        first = self.issue(active, evidence)
        self.assertEqual(first.report.outcome, LimitOutcome.PASS)
        self.assertEqual(first.report.evidence.trial.limit, 9)
        self.reset(active)
        second = self.start('lower-limit')
        self.feed(9)
        next_issue = self.issue(second, self.stop(second))
        self.assertEqual(next_issue.report.outcome, LimitOutcome.FAIL)
        self.assertEqual(next_issue.report.evidence.trial.limit, 8)
        self.assertEqual(read(self.store, first.report.report_id), first)

    def test_issue_replay(self):
        self.register('replay')
        session_id = self.start('replay')
        self.feed(7)
        evidence = self.stop(session_id)
        first = self.issue(session_id, evidence)
        self.assertEqual(self.issue(session_id, evidence), first)
        self.reset(session_id)
        self.assertEqual(self.issue(session_id, evidence), first)
        self.device.close()
        self.store.close()
        reopened = self.open_archive()
        self.assertEqual(issue(reopened, SessionRegistry(), None, session_id, evidence), first)
        changed = replace(evidence, trial=replace(evidence.trial, limit=12))
        with self.assertRaises(IssueError):
            issue(reopened, SessionRegistry(), None, session_id, changed)
        self.assertEqual(read(reopened, first.report.report_id), first)
        self.assertEqual(self.raw_counts(), (1, 1))

    def test_commit_failure(self):
        self.register('rollback')
        self.register('next')
        session_id = self.start('rollback')
        self.feed(4, 9, 9)
        evidence = self.stop(session_id)
        self.fault.arm()
        with self.assertRaises(ArchiveCommitError):
            self.issue(session_id, evidence)
        self.assertEqual(self.raw_counts(), (0, 0))
        with self.assertRaises(ResetError):
            self.reset(session_id)
        self.assertEqual(require_stopped_evidence(self.sessions, self.device, session_id), evidence)
        issued = self.issue(session_id, evidence)
        self.assertEqual(issued.report.evidence, evidence)
        self.reset(session_id)
        next_session = self.start('next')
        meter = preview(self.sessions, self.device, next_session)
        self.assertEqual((meter.count, meter.peak), (0, None))

    def test_atomic_visibility(self):
        previous = self.complete('previous')
        self.register('atomic')
        session_id = self.start('atomic')
        self.feed(12)
        evidence = self.stop(session_id)
        during = []
        self.fault.arm(lambda: during.append(self.raw_counts()))
        with self.assertRaises(ArchiveCommitError):
            self.issue(session_id, evidence)
        after_failure = self.raw_counts()
        self.assertEqual(during, [(1, 1)])
        self.assertEqual(after_failure, (1, 1))
        self.assertIsNone(find_committed_issue(self.store, session_id))
        self.assertEqual(read(self.store, previous.report.report_id), previous)
        retried = self.issue(session_id, evidence)
        after_retry = self.raw_counts()
        self.assertEqual(after_retry, (2, 2))
        self.assertEqual(read(self.store, retried.report.report_id), retried)
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'v6.atomic_visibility',
            'input': {'prior_issued_pairs': 1, 'new_readings': [12], 'fail_before_commit': True},
            'expected': {'during': [1, 1], 'after_failure': [1, 1]},
            'actual': {'during': during[0], 'after_failure': after_failure},
            'downstream_expected': {'after_retry': [2, 2]},
            'downstream_actual': {'after_retry': after_retry},
            'code_paths': ['archive/issue.py', 'archive/store.py', 'archive/readback.py'],
            'oracle': '独立连接分别计数报告与回执。单次失败不新增行，重投只新增一对。',
        }, sort_keys=True))

    def test_reset_guard(self):
        earlier = self.complete('earlier', (2,))
        self.register('current')
        self.register('future')
        current = self.start('current')
        self.feed(10)
        original = self.stop(current)
        self.fault.arm()
        with self.assertRaises(ArchiveCommitError):
            self.issue(current, original)
        with self.assertRaises(ResetError):
            self.reset(earlier.report.session_id)
        with self.assertRaises(ResetError):
            self.reset(current)
        with self.assertRaises(Exception) as refusal:
            self.start('future')
        from capture.control import StartError
        self.assertIsInstance(refusal.exception, StartError)
        self.assertEqual(self.sessions.current.session_id, current)
        self.assertEqual(require_stopped_evidence(self.sessions, self.device, current), original)
        self.assertEqual(read(self.store, earlier.report.report_id), earlier)
        self.issue(current, original)
        self.reset(current)
        self.assertIsNone(self.sessions.current)

    def test_reuse_device(self):
        issued = self.complete('used', (9, 4))
        self.assertEqual(issued.report.evidence.original.snapshot.last_sample_no, 2)
        self.assertEqual(self.sessions.require(issued.report.session_id).state, 'released')
        from capture.control import StartError
        with self.assertRaises(StartError):
            self.start('used')
        self.register('unused')
        next_session = self.start('unused')
        self.assertEqual(preview(self.sessions, self.device, next_session).count, 0)
        self.assertEqual(self.feed(0), (3,))
        snapshot = self.stop(next_session).original.snapshot
        self.assertEqual((snapshot.count, snapshot.first_sample_no, snapshot.peak_sample_no), (1, 3, 3))
