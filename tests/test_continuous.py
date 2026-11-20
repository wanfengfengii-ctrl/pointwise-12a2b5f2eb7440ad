import json
import sqlite3
from contextlib import closing

from archive.readback import ReportLookupError, read
from capture.control import CutStatus, cut, start
from capture.segments import RequestPhase
from capture.session import SessionStateError
from capture.stop import stop
from device.events import ControlEvent
from device.playback import load_recording
from device.port import DevicePort, DeviceRejectedError
from quality.limits import LimitOutcome
from tests.support import BenchCase, CHANNEL, RECORDINGS


class ContinuousCase(BenchCase):
    def start_continuous(self, trial_id=None, limit=9):
        trial_id = trial_id or f'continuous-{self._testMethodName}'
        self.register(trial_id, limit)
        return start(
            self.catalog, self.sessions, self.device, trial_id, continuous=True
        )

    def open_archive_with_faults(self, *, custody_fault=None, issue_fault=None):
        self.store.close()
        store = self.open_archive(
            before_commit=issue_fault,
            before_custody_commit=custody_fault,
        )
        self.store = store
        return store

    def segment_counts(self):
        with closing(sqlite3.connect(self.path)) as reader:
            custody = reader.execute('SELECT COUNT(*) FROM segment_custody').fetchone()[0]
            reports = reader.execute('SELECT COUNT(*) FROM segment_reports').fetchone()[0]
            receipts = reader.execute('SELECT COUNT(*) FROM segment_receipts').fetchone()[0]
        return custody, reports, receipts

    def cut_actions(self):
        return [event for event in self.device.actions if event.action == 'cut']

    def cut(self, session_id, request_id):
        return cut(self.store, self.sessions, self.device, session_id, request_id)


class ContinuousFlowTests(ContinuousCase):
    def test_hand_table_from_recording(self):
        session_id = self.start_continuous()
        steps = load_recording(RECORDINGS / 'continuous.json')
        for step in steps[:3]:
            self.device.sample(step.channel, step.value)
        result_a = self.cut(session_id, 'A')
        self.assertEqual(result_a.status, CutStatus.ISSUED)
        for step in steps[3:]:
            self.device.sample(step.channel, step.value)
        result_b = self.cut(session_id, 'B')
        result_c = self.cut(session_id, 'C')
        self.assertEqual(result_b.status, CutStatus.ISSUED)
        self.assertEqual(result_c.status, CutStatus.ISSUED)

        record_a = result_a.issued
        report_a = record_a.report
        self.assertEqual(
            (report_a.session_id, report_a.original.request_id, report_a.original.channel),
            (session_id, 'A', CHANNEL),
        )
        self.assertEqual(report_a.original.device_id, self.device.device_id)
        self.assertEqual(report_a.original.limit, 9)
        boundary_a = report_a.original.boundary
        self.assertIsInstance(boundary_a, ControlEvent)
        self.assertEqual(boundary_a.action, 'cut')
        self.assertEqual(boundary_a.last_sample_no, 3)
        meter_a = report_a.original.snapshot
        self.assertEqual(
            (meter_a.count, meter_a.first_sample_no, meter_a.last_sample_no), (3, 1, 3)
        )
        self.assertEqual((meter_a.peak, meter_a.peak_sample_no), (9, 2))
        self.assertEqual(meter_a.peak_sample.value, 9)
        self.assertEqual(report_a.outcome, LimitOutcome.PASS)

        report_b = result_b.issued.report
        self.assertEqual(report_b.original.previous, boundary_a)
        self.assertEqual(report_b.original.boundary.last_sample_no, 5)
        meter_b = report_b.original.snapshot
        self.assertEqual((meter_b.count, meter_b.first_sample_no, meter_b.last_sample_no), (2, 4, 5))
        self.assertEqual((meter_b.peak, meter_b.peak_sample_no), (12, 4))
        self.assertEqual(report_b.outcome, LimitOutcome.FAIL)

        report_c = result_c.issued.report
        self.assertEqual(report_c.original.boundary.last_sample_no, 5)
        self.assertEqual(report_c.original.boundary.sequence, report_b.original.boundary.sequence + 1)
        meter_c = report_c.original.snapshot
        self.assertEqual(meter_c.count, 0)
        self.assertEqual(
            (meter_c.first_sample_no, meter_c.last_sample_no, meter_c.peak,
             meter_c.peak_sample_no, meter_c.peak_sample),
            (None, None, None, None, None),
        )
        self.assertEqual(report_c.outcome, LimitOutcome.NO_DATA)

        ids = {record_a.report.report_id, result_b.issued.report.report_id,
               result_c.issued.report.report_id}
        self.assertEqual(len(ids), 3)
        for record in (record_a, result_b.issued, result_c.issued):
            self.assertEqual(record.receipt.receipt_id, f'receipt-{record.report.report_id}')
            self.assertEqual(record.receipt.report_id, record.report.report_id)
            self.assertEqual(record.receipt.session_id, session_id)
        self.assertEqual(self.segment_counts(), (3, 3, 3))
        self.assertEqual([event.last_sample_no for event in self.cut_actions()], [3, 5, 5])
        self.assertEqual([event.sequence for event in self.cut_actions()],
                         [boundary_a.sequence, report_b.original.boundary.sequence,
                          report_c.original.boundary.sequence])
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'v8.continuous_hand_table',
            'input': {'readings': [4, 9, 9, 12, 5], 'requests': ['A@3', 'B@5', 'C@5']},
            'expected': {
                'A': {'count': 3, 'first': 1, 'last': 3, 'peak': 9, 'peak_number': 2, 'outcome': 'pass'},
                'B': {'count': 2, 'first': 4, 'last': 5, 'peak': 12, 'peak_number': 4, 'outcome': 'fail'},
                'C': {'count': 0, 'outcome': 'no_data'},
            },
            'actual': {
                'A': {'count': meter_a.count, 'peak': meter_a.peak, 'peak_number': meter_a.peak_sample_no,
                      'outcome': report_a.outcome.value},
                'B': {'count': meter_b.count, 'peak': meter_b.peak, 'peak_number': meter_b.peak_sample_no,
                      'outcome': report_b.outcome.value},
                'C': {'count': meter_c.count, 'outcome': report_c.outcome.value},
            },
            'code_paths': ['device/port.py', 'capture/control.py', 'capture/segments.py',
                           'archive/store.py', 'archive/segment_schema.py', 'archive/readback.py'],
            'oracle': '以设备实际切段水位分段；同水位的 B、C 由控制事件序区分；同值峰取首次，空切无数据。',
        }, ensure_ascii=False, sort_keys=True))

    def test_empty_cut_issuance(self):
        session_id = self.start_continuous()
        result = self.cut(session_id, 'empty')
        self.assertEqual(result.status, CutStatus.ISSUED)
        report = result.issued.report
        self.assertEqual(report.outcome, LimitOutcome.NO_DATA)
        self.assertEqual(report.original.boundary.action, 'cut')
        self.assertEqual(report.original.boundary.last_sample_no, 0)
        self.assertEqual(report.original.previous.last_sample_no, 0)
        snapshot = report.original.snapshot
        self.assertEqual(snapshot.count, 0)
        self.assertEqual(
            (snapshot.first_sample_no, snapshot.last_sample_no, snapshot.peak,
             snapshot.peak_sample_no, snapshot.peak_sample),
            (None, None, None, None, None),
        )

    def test_later_segment_keeps_own_peak(self):
        session_id = self.start_continuous()
        self.feed(4, 9)
        first = self.cut(session_id, 'first').issued.report
        self.assertEqual((first.original.snapshot.peak, first.original.snapshot.peak_sample_no), (9, 2))
        self.feed(9, 3)
        second = self.cut(session_id, 'second').issued.report
        meter = second.original.snapshot
        self.assertEqual((meter.count, meter.first_sample_no, meter.last_sample_no), (2, 3, 4))
        self.assertEqual((meter.peak, meter.peak_sample_no), (9, 3))
        self.assertGreater(
            meter.peak_sample.sequence, first.original.snapshot.peak_sample.sequence
        )

    def test_sampling_continues_during_cuts(self):
        session_id = self.start_continuous()
        self.feed(1, 2)
        self.cut(session_id, 'A')
        self.assertEqual(self.feed(7), (3,))
        self.cut(session_id, 'B')
        self.assertEqual(self.feed(8), (4,))
        self.assertEqual(self.device.last_sample_no, 4)

    def test_frozen_limit_drives_each_segment_verdict(self):
        session_id = self.start_continuous(limit=0)
        self.feed(0)
        at_limit = self.cut(session_id, 'zero')
        self.assertEqual(at_limit.issued.report.outcome, LimitOutcome.PASS)
        self.feed(1)
        over = self.cut(session_id, 'one')
        self.assertEqual(over.issued.report.outcome, LimitOutcome.FAIL)
        self.assertEqual(over.issued.report.original.limit, 0)
        self.assertEqual(over.issued.report.original.channel, CHANNEL)

    def test_replayed_cut_uses_stable_identity_after_reopen(self):
        session_id = self.start_continuous()
        self.feed(5)
        first = self.cut(session_id, 'A')
        self.device.close()
        self.store.close()
        reopened = self.open_archive()
        again = read(reopened, first.issued.report.report_id)
        self.assertEqual(again, first.issued)
        self.assertEqual(again.receipt, first.issued.receipt)

    def test_non_string_request_id_is_rejected(self):
        session_id = self.start_continuous()
        for bad_request in (None, 7, b'A', ''):
            with self.subTest(request=bad_request):
                with self.assertRaises(SessionStateError):
                    self.cut(session_id, bad_request)
        self.assertEqual(self.cut_actions(), [])

    def test_boundary_events_are_distinct_control_facts(self):
        session_id = self.start_continuous()
        self.feed(1)
        first = self.cut(session_id, 'A').issued.report.original
        self.assertEqual(
            (first.previous.action, first.boundary.action), ('start', 'cut')
        )
        self.assertEqual(first.previous.last_sample_no, 0)
        self.assertEqual(first.boundary.last_sample_no, 1)
        empty = self.cut(session_id, 'B').issued.report.original
        self.assertEqual(empty.previous, first.boundary)
        self.assertEqual(empty.boundary.last_sample_no, 1)
        self.assertNotEqual(empty.boundary.sequence, first.boundary.sequence)
        self.assertGreater(empty.boundary.sequence, first.boundary.sequence)
        self.assertEqual(self.device.last_sample_no, 1)


class ContinuousFaultTests(ContinuousCase):
    def test_custody_failure_keeps_original_and_continues(self):
        session_id = self.start_continuous()
        self.feed(4, 9, 9)
        store = self.open_archive_with_faults(custody_fault=self.fault)
        self.fault.arm(lambda: self.feed(12, 5))
        result = self.cut(session_id, 'A')
        self.assertEqual(result.status, CutStatus.PENDING)
        self.assertIsNone(result.issued)
        self.assertEqual(self.segment_counts(), (0, 0, 0))
        self.assertEqual(self.device.last_sample_no, 5)

        retried = self.cut(session_id, 'A')
        self.assertEqual(retried.status, CutStatus.ISSUED)
        report = retried.issued.report
        self.assertEqual(report.original.boundary.last_sample_no, 3)
        meter = report.original.snapshot
        self.assertEqual((meter.count, meter.first_sample_no, meter.last_sample_no), (3, 1, 3))
        self.assertEqual((meter.peak, meter.peak_sample_no), (9, 2))

        next_cut = self.cut(session_id, 'B')
        self.assertEqual(next_cut.status, CutStatus.ISSUED)
        next_meter = next_cut.issued.report.original.snapshot
        self.assertEqual((next_meter.count, next_meter.first_sample_no, next_meter.last_sample_no), (2, 4, 5))
        self.assertEqual((next_meter.peak, next_meter.peak_sample_no), (12, 4))
        self.assertEqual(self.segment_counts(), (2, 2, 2))

    def test_issue_failure_leaves_committed_custody(self):
        session_id = self.start_continuous()
        self.feed(4, 9, 9)
        during = []
        self.open_archive_with_faults(
            issue_fault=_FaultOnce(lambda: during.append(self.segment_counts()))
        )
        result = self.cut(session_id, 'A')
        self.assertEqual(result.status, CutStatus.PENDING)
        self.assertEqual(during, [(1, 0, 0)])
        self.assertEqual(self.segment_counts(), (1, 0, 0))

        retried = self.cut(session_id, 'A')
        self.assertEqual(retried.status, CutStatus.ISSUED)
        custody_report, custody_document = self.store.find_custody(session_id, 'A')
        self.assertEqual(custody_report, retried.issued.report.report_id)
        self.assertEqual(
            custody_document.original, retried.issued.report.original
        )
        self.assertEqual(self.segment_counts(), (1, 1, 1))

    def test_issue_failure_keeps_custody_phase_for_retry(self):
        session_id = self.start_continuous()
        self.feed(3)
        self.open_archive_with_faults(issue_fault=_FaultOnce())
        self.assertEqual(self.cut(session_id, 'A').status, CutStatus.PENDING)
        entry = self.sessions.ledger.require(session_id).get('A')
        self.assertEqual(entry.phase, RequestPhase.CUSTODY)
        busy = self.cut(session_id, 'B')
        self.assertEqual(busy.status, CutStatus.BUSY)
        retried = self.cut(session_id, 'A')
        self.assertEqual(retried.status, CutStatus.ISSUED)
        self.assertEqual(entry.phase, RequestPhase.ISSUED)

    def test_busy_new_id_does_not_cut_or_occupy(self):
        session_id = self.start_continuous()
        self.feed(4)
        self.open_archive_with_faults(custody_fault=self.fault)
        self.fault.arm()
        pending = self.cut(session_id, 'A')
        self.assertEqual(pending.status, CutStatus.PENDING)
        cuts_before = len(self.cut_actions())

        busy = self.cut(session_id, 'B')
        self.assertEqual(busy.status, CutStatus.BUSY)
        self.assertIsNone(busy.issued)
        self.assertEqual(len(self.cut_actions()), cuts_before)
        book = self.sessions.ledger.require(session_id)
        self.assertIsNone(book.get('B'))

        retried = self.cut(session_id, 'A')
        self.assertEqual(retried.status, CutStatus.ISSUED)
        follow_up = self.cut(session_id, 'B')
        self.assertEqual(follow_up.status, CutStatus.ISSUED)
        self.assertEqual(follow_up.issued.report.original.request_id, 'B')

    def test_issued_request_replays_without_new_cut(self):
        session_id = self.start_continuous()
        self.feed(4, 9)
        first = self.cut(session_id, 'A')
        cuts_before = len(self.cut_actions())
        again = self.cut(session_id, 'A')
        self.assertEqual(again.status, CutStatus.ISSUED)
        self.assertEqual(again.issued, first.issued)
        self.assertEqual(len(self.cut_actions()), cuts_before)

    def test_retry_uses_same_report_identity(self):
        session_id = self.start_continuous()
        self.feed(3)
        self.open_archive_with_faults(custody_fault=self.fault)
        self.fault.arm()
        self.assertEqual(self.cut(session_id, 'A').status, CutStatus.PENDING)
        entry = self.sessions.ledger.require(session_id).get('A')
        self.assertEqual(entry.phase, RequestPhase.PENDING)
        issued = self.cut(session_id, 'A')
        self.assertEqual(entry.phase, RequestPhase.ISSUED)
        self.assertEqual(issued.issued.report.report_id, entry.report_id)
        self.assertEqual(entry.report_id is not None, True)


class ContinuousGuardTests(ContinuousCase):
    def test_cut_guards(self):
        self.register('stop-trial')
        stop_session = start(self.catalog, self.sessions, self.device, 'stop-trial')
        self.feed(2)
        with self.assertRaises(SessionStateError):
            self.cut(stop_session, 'A')
        with self.assertRaises(SessionStateError):
            self.cut('unknown-session', 'A')
        other_device = DevicePort()
        self.addCleanup(other_device.close)
        with self.assertRaises(SessionStateError):
            cut(self.store, self.sessions, other_device, stop_session, 'A')
        for bad_request in ('', None):
            with self.subTest(request=bad_request):
                with self.assertRaises(SessionStateError):
                    self.cut(stop_session, bad_request)
        with self.assertRaises(SessionStateError):
            cut(self.store, self.sessions, None, stop_session, 'A')
        self.assertEqual(self.cut_actions(), [])

        evidence = stop(self.sessions, self.device, stop_session)
        self.issue(stop_session, evidence)
        self.reset(stop_session)
        continuous_session = self.start_continuous()
        with self.assertRaises(SessionStateError):
            cut(self.store, self.sessions, other_device, continuous_session, 'A')
        for bad_request in ('', None):
            with self.subTest(continuous_request=bad_request):
                with self.assertRaises(SessionStateError):
                    self.cut(continuous_session, bad_request)
        with self.assertRaises(SessionStateError):
            cut(self.store, self.sessions, None, continuous_session, 'A')
        self.assertEqual(self.cut_actions(), [])

    def test_stop_entry_rejects_continuous_session(self):
        session_id = self.start_continuous()
        self.feed(5)
        with self.assertRaises(SessionStateError):
            stop(self.sessions, self.device, session_id)
        self.assertEqual(self.sessions.require(session_id).state, 'running')
        self.assertEqual(self.feed(6), (2,))

    def test_device_cut_primitive(self):
        with self.assertRaises(DeviceRejectedError):
            self.device.cut_sampling()
        self.device.start_sampling(CHANNEL)
        self.feed(4, 9)
        with self.assertRaises(DeviceRejectedError):
            self.device.cut_sampling()
        self.device.stop_sampling()
        self.device.release_stopped_slot(self.device.read_stopped_slot())

        start_event = self.device.start_sampling(CHANNEL, continuous=True)
        self.feed(4, 9, 9)
        segment = self.device.cut_sampling()
        self.assertEqual(segment.boundary.action, 'cut')
        self.assertGreater(segment.boundary.sequence, start_event.sequence)
        self.assertEqual(
            [
                event.action
                for event in self.device.actions
                if event.action in ('start', 'cut')
            ],
            ['start', 'start', 'cut'],
        )
        self.assertEqual(segment.boundary.last_sample_no, 5)
        self.assertEqual((segment.snapshot.count, segment.snapshot.peak_sample_no), (3, 4))
        self.assertEqual(self.device.last_sample_no, 5)
        self.assertEqual(self.device.read_slot().count, 3)
        self.assertEqual(self.feed(12), (6,))
        next_segment = self.device.cut_sampling()
        self.assertEqual(next_segment.boundary.last_sample_no, 6)
        self.assertEqual(next_segment.snapshot.count, 1)
        self.assertEqual(next_segment.snapshot.first_sample_no, 6)
        self.assertEqual(self.device.read_slot().count, 4)


class ContinuousReadbackTests(ContinuousCase):
    def test_reopen_archive_reads_same_report_and_receipt(self):
        session_id = self.start_continuous()
        self.feed(4, 9, 9)
        a = self.cut(session_id, 'A').issued
        self.feed(12, 5)
        b = self.cut(session_id, 'B').issued
        c = self.cut(session_id, 'C').issued
        self.device.close()
        self.store.close()
        reopened = self.open_archive()

        for record in (a, b, c):
            received = read(reopened, record.report.report_id)
            self.assertEqual(received, record)
            report = received.report
            self.assertEqual(
                (report.report_id, report.session_id, report.original.channel,
                 report.original.request_id, report.original.device_id),
                (record.report.report_id, session_id, CHANNEL,
                 record.report.original.request_id, self.device.device_id),
            )
            self.assertIsInstance(report.original.boundary, ControlEvent)
            self.assertEqual(report.original.snapshot, record.report.original.snapshot)
            self.assertEqual(report.outcome, record.report.outcome)
            self.assertEqual(received.receipt, record.receipt)

        with self.assertRaises(ReportLookupError) as missing:
            read(reopened, 'missing-report')
        self.assertEqual(missing.exception.args, ('missing-report',))

    def test_legacy_and_segment_records_coexist(self):
        legacy = self.complete('legacy', (4, 9, 9), 9)
        session_id = self.start_continuous()
        self.feed(21)
        segment = self.cut(session_id, 'A').issued
        legacy_again = read(self.store, legacy.report.report_id)
        segment_again = read(self.store, segment.report.report_id)
        self.assertEqual(legacy_again, legacy)
        self.assertEqual(segment_again, segment)
        self.assertEqual(legacy_again.report.evidence.trial.trial_id, 'legacy')
        self.assertEqual(segment_again.report.original.request_id, 'A')


class _FaultOnce:
    def __init__(self, probe=None):
        self._armed = True
        self._probe = probe

    def __call__(self):
        if not self._armed:
            return
        self._armed = False
        if self._probe is not None:
            self._probe()
        raise sqlite3.OperationalError('injected failure before segment commit')
