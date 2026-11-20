from archive.readback import ReportLookupError, read
from device.playback import load_recording, run_playback
from device.port import DeviceRejectedError
from quality.limits import LimitOutcome
from tests.support import BenchCase, CHANNEL, RECORDINGS


class ReadTests(BenchCase):
    def close_and_reopen(self):
        self.device.close()
        self.store.close()
        return self.open_archive()

    def test_reopen_without_source(self):
        self.register('empty', 9)
        session_id = self.start('empty')
        played = run_playback(self.device, load_recording(RECORDINGS / 'empty.json'))
        self.assertEqual(played.events, ())
        issued = self.issue(session_id, self.stop(session_id))
        self.reset(session_id)
        reopened = self.close_and_reopen()
        with self.assertRaises(DeviceRejectedError):
            self.device.read_slot()
        recovered = read(reopened, issued.report.report_id)
        self.assertEqual(recovered, issued)
        facts = recovered.report.evidence
        self.assertEqual((facts.trial.trial_id, facts.trial.channel, facts.trial.limit), ('empty', CHANNEL, 9))
        self.assertEqual(facts.original.stop.last_sample_no, 0)
        meter = facts.original.snapshot
        self.assertEqual(meter.count, 0)
        self.assertEqual((meter.first_sample_no, meter.last_sample_no, meter.peak, meter.peak_sample_no), (None,) * 4)
        self.assertEqual(recovered.report.outcome, LimitOutcome.NO_DATA)
        self.assertEqual(recovered.receipt.report_id, recovered.report.report_id)
        self.assertEqual(recovered.receipt.session_id, session_id)

    def test_fixed_evidence(self):
        first = self.complete('day', (4, 9, 9), 9)
        second = self.complete('night', (21, 2), 20)
        reopened = self.close_and_reopen()
        previous = read(reopened, first.report.report_id)
        latest = read(reopened, second.report.report_id)
        self.assertEqual(previous, first)
        self.assertEqual(latest, second)
        first_meter = previous.report.evidence.original.snapshot
        second_meter = latest.report.evidence.original.snapshot
        self.assertEqual((first_meter.first_sample_no, first_meter.last_sample_no, first_meter.peak), (1, 3, 9))
        self.assertEqual((first_meter.peak_sample.sample_no, first_meter.peak_sample.value), (2, 9))
        self.assertEqual((second_meter.first_sample_no, second_meter.last_sample_no, second_meter.peak), (4, 5, 21))
        self.assertEqual(previous.report.outcome, LimitOutcome.PASS)
        self.assertEqual(latest.report.outcome, LimitOutcome.FAIL)

    def test_zero_peak(self):
        zero = self.complete('zero', (0,), 0)
        empty = self.complete('missing', (), 0)
        reopened = self.close_and_reopen()
        zero_again = read(reopened, zero.report.report_id)
        empty_again = read(reopened, empty.report.report_id)
        self.assertEqual(zero_again, zero)
        self.assertEqual(empty_again, empty)
        zero_meter = zero_again.report.evidence.original.snapshot
        empty_meter = empty_again.report.evidence.original.snapshot
        self.assertEqual((zero_meter.count, zero_meter.peak, zero_meter.peak_sample_no), (1, 0, 1))
        self.assertEqual((empty_meter.count, empty_meter.peak, empty_meter.peak_sample_no), (0, None, None))
        self.assertEqual(zero_again.report.outcome, LimitOutcome.PASS)
        self.assertEqual(empty_again.report.outcome, LimitOutcome.NO_DATA)

    def test_large_integer(self):
        reading = (1 << 85) + 17
        limit = (1 << 85) + 16
        issued = self.complete('wide', (reading - 2, reading), limit)
        report = read(self.close_and_reopen(), issued.report.report_id).report
        self.assertEqual(report.evidence.trial.limit, limit)
        self.assertEqual(report.evidence.original.snapshot.peak, reading)
        self.assertEqual(report.evidence.original.snapshot.peak_sample.value, reading)
        self.assertEqual(report.evidence.original.snapshot.peak_sample_no, 2)
        self.assertEqual(report.outcome, LimitOutcome.FAIL)

    def test_initialize_again(self):
        recording = self.directory / 'operator-recording.json'
        recording.write_bytes((RECORDINGS / 'peak.json').read_bytes())
        self.register('recorded-original')
        original_session = self.start('recorded-original')
        run_playback(self.device, load_recording(recording))
        original = self.issue(original_session, self.stop(original_session))
        self.reset(original_session)
        changed_bytes = b'[{"action":"sample","channel":"pump-body","value":13}]\n'
        recording.write_bytes(changed_bytes)
        self.store.close()
        self.store = self.open_archive()
        self.assertEqual(recording.read_bytes(), changed_bytes)
        self.assertEqual(read(self.store, original.report.report_id), original)
        self.assertEqual(self.raw_counts(), (1, 1))
        self.register('recorded-later')
        later_session = self.start('recorded-later')
        run_playback(self.device, load_recording(recording))
        later = self.issue(later_session, self.stop(later_session))
        self.reset(later_session)
        original_again = read(self.store, original.report.report_id)
        self.assertEqual(original_again.report.evidence.original.snapshot.peak, 9)
        self.assertEqual(original_again.receipt, original.receipt)
        self.assertEqual(later.report.evidence.original.snapshot.peak, 13)
        self.assertEqual(later.report.evidence.original.snapshot.peak_sample_no, 4)
        self.assertEqual(self.raw_counts(), (2, 2))
        self.assertEqual(recording.read_bytes(), changed_bytes)

    def test_unknown_report(self):
        issued = self.complete('known', (5,))
        reopened = self.close_and_reopen()
        unknown = 'unknown-report'
        with self.assertRaises(ReportLookupError) as missing:
            read(reopened, unknown)
        self.assertEqual(missing.exception.args, (unknown,))
        self.assertEqual(read(reopened, issued.report.report_id), issued)
