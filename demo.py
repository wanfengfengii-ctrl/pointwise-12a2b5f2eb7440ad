import json
import sqlite3
from contextlib import ExitStack
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.issue import issue
from archive.readback import read
from archive.store import ArchiveStore, IssuedRecord
from capture.control import CutStatus, cut, start
from capture.preview import preview
from capture.reset import reset
from capture.session import SessionRegistry
from capture.stop import stop
from device.playback import load_recording, run_playback
from device.port import DevicePort
from jobs.catalog import TrialCatalog
from jobs.enrollment import register
from jobs.profiles import ChannelProfile, ChannelProfiles


RECORDINGS = Path(__file__).resolve().parent / 'recordings'
CHANNEL = 'pump-body'


def _report_fields(issued: IssuedRecord) -> dict:
    evidence = issued.report.evidence
    meter = evidence.original.snapshot
    return {
        'channel': evidence.trial.channel,
        'limit': evidence.trial.limit,
        'count': meter.count,
        'first': meter.first_sample_no,
        'last': meter.last_sample_no,
        'peak': meter.peak,
        'peak_number': meter.peak_sample_no,
        'outcome': issued.report.outcome.value,
    }


def _record_trial(catalog, profiles, sessions, device, archive, trial_id, recording):
    register(catalog, profiles, trial_id, CHANNEL, 9)
    session_id = start(catalog, sessions, device, trial_id)
    steps = load_recording(RECORDINGS / recording)
    run_playback(device, steps)
    observed = preview(sessions, device, session_id)
    evidence = stop(sessions, device, session_id)
    if observed != evidence.original.snapshot:
        raise AssertionError('unchanged device samples must have identical preview and stop meters')
    issued = issue(archive, sessions, device, session_id, evidence)
    reset(sessions, device, archive, session_id)
    return issued, [step.value for step in steps]


def _print_handover(case_id, inputs, expected, issued, received, oracle):
    actual = _report_fields(issued)
    readback = _report_fields(received)
    if actual != expected or readback != expected or received != issued:
        raise AssertionError(f'fixed report disagrees with the hand check for {case_id}')
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': case_id, 'input': inputs, 'expected': expected, 'actual': actual,
        'downstream_expected': {'report': expected, 'same_receipt': True},
        'downstream_actual': {'report': readback, 'same_receipt': received.receipt == issued.receipt},
        'code_paths': [
            'device/playback.py', 'device/registers.py', 'capture/stop.py', 'quality/decision.py',
            'archive/issue.py', 'archive/store.py', 'archive/readback.py',
        ],
        'oracle': oracle,
    }, ensure_ascii=False, sort_keys=True))
    print(f'{case_id} {received.report.report_id} {received.receipt.receipt_id}')


def _segment_fields(record) -> dict:
    report = record.report
    meter = report.original.snapshot
    return {
        'channel': report.original.channel,
        'request_id': report.original.request_id,
        'boundary_watermark': report.original.boundary.last_sample_no,
        'count': meter.count,
        'first': meter.first_sample_no,
        'last': meter.last_sample_no,
        'peak': meter.peak,
        'peak_number': meter.peak_sample_no,
        'outcome': report.outcome.value,
    }


class _OneShotFault:
    def __init__(self, name, probe=None):
        self.name = name
        self._armed = True
        self._probe = probe

    def __call__(self):
        if not self._armed:
            return
        self._armed = False
        if self._probe is not None:
            self._probe()
        raise sqlite3.OperationalError(f'injected {self.name} failure during the demo')


def _segment_counts(path: Path) -> dict:
    with closing(sqlite3.connect(path)) as reader:
        return {
            'custody': reader.execute('SELECT COUNT(*) FROM segment_custody').fetchone()[0],
            'reports': reader.execute('SELECT COUNT(*) FROM segment_reports').fetchone()[0],
            'receipts': reader.execute('SELECT COUNT(*) FROM segment_receipts').fetchone()[0],
        }


def _run_continuous_demo(base: Path) -> list:
    continuous_path = base / 'continuous.sqlite3'
    fault_path = base / 'continuous-fault.sqlite3'
    catalog, profiles = TrialCatalog(), ChannelProfiles()
    profiles.add(ChannelProfile(CHANNEL, '泵体测点'))
    steps = load_recording(RECORDINGS / 'continuous.json')
    printed = []

    with ExitStack() as resources:
        device = DevicePort()
        resources.callback(device.close)
        sessions = SessionRegistry()
        archive = ArchiveStore.open(continuous_path)
        resources.callback(archive.close)
        register(catalog, profiles, 'continuous-trial', CHANNEL, 9)
        session_id = start(
            catalog, sessions, device, 'continuous-trial', continuous=True
        )
        for step in steps[:3]:
            device.sample(step.channel, step.value)
        a = cut(archive, sessions, device, session_id, 'A')
        for step in steps[3:]:
            device.sample(step.channel, step.value)
        b = cut(archive, sessions, device, session_id, 'B')
        c = cut(archive, sessions, device, session_id, 'C')
        expected_a = {
            'channel': CHANNEL, 'request_id': 'A', 'boundary_watermark': 3,
            'count': 3, 'first': 1, 'last': 3, 'peak': 9, 'peak_number': 2, 'outcome': 'pass',
        }
        expected_b = {
            'channel': CHANNEL, 'request_id': 'B', 'boundary_watermark': 5,
            'count': 2, 'first': 4, 'last': 5, 'peak': 12, 'peak_number': 4, 'outcome': 'fail',
        }
        expected_c = {
            'channel': CHANNEL, 'request_id': 'C', 'boundary_watermark': 5,
            'count': 0, 'first': None, 'last': None, 'peak': None,
            'peak_number': None, 'outcome': 'no_data',
        }
        for result, expected, oracle in (
            (a, expected_a, '首段接纳开测水位之后至 3 号水位的样本，同值峰 9 取首次 #2，等于限值通过。'),
            (b, expected_b, '后段从 4 号样本开始，峰值 12 在 #4，超过冻结限值 9，裁决 fail。'),
            (c, expected_c, '同水位空切，独立控制事件与报告，计数为零，裁决 no_data。'),
        ):
            actual = _segment_fields(result.issued)
            if result.status != CutStatus.ISSUED or actual != expected:
                raise AssertionError('continuous hand table disagrees with the expected segment')
            printed.append('POINTWISE_SCENARIO ' + json.dumps({
                'case_id': f'v8.segment.{expected["request_id"]}',
                'input': {'recording': 'continuous.json', 'request': expected['request_id']},
                'expected': expected,
                'actual': actual,
                'downstream_expected': expected,
                'downstream_actual': actual,
                'code_paths': ['device/port.py', 'capture/control.py', 'archive/store.py'],
                'oracle': oracle,
            }, ensure_ascii=False, sort_keys=True))
        if device.last_sample_no != 5:
            raise AssertionError('sampling must keep running across the cuts')
        device.close()

    with ExitStack() as resources:
        reopened = ArchiveStore.open(continuous_path)
        resources.callback(reopened.close)
        for record in (a.issued, b.issued, c.issued):
            received = read(reopened, record.report.report_id)
            if received != record:
                raise AssertionError('reopened archive must return the same segment report and receipt')
        reopened_a = read(reopened, a.issued.report.report_id)
        printed.append('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'v8.reopen_readback',
            'input': {'closed_source': True, 'report_id': a.issued.report.report_id},
            'expected': _segment_fields(a.issued),
            'actual': _segment_fields(reopened_a),
            'downstream_expected': {'same_receipt': True},
            'downstream_actual': {'same_receipt': reopened_a.receipt == a.issued.receipt},
            'code_paths': ['archive/readback.py', 'archive/segment_schema.py'],
            'oracle': '夜班停源后重开归档，凭原报告号领取同一份报告和回执。',
        }, ensure_ascii=False, sort_keys=True))
        printed.append(
            f'v8.after_source_closed A {reopened_a.report.report_id} {reopened_a.receipt.receipt_id}'
        )

    fault_catalog, fault_profiles = TrialCatalog(), ChannelProfiles()
    fault_profiles.add(ChannelProfile(CHANNEL, '泵体测点'))
    with ExitStack() as resources:
        fault_device = DevicePort()
        resources.callback(fault_device.close)
        fault_sessions = SessionRegistry()
        custody_fault = _OneShotFault('custody')
        fault_archive = ArchiveStore.open(
            fault_path, before_custody_commit=custody_fault
        )
        resources.callback(fault_archive.close)
        register(fault_catalog, fault_profiles, 'fault-trial', CHANNEL, 9)
        fault_session = start(
            fault_catalog, fault_sessions, fault_device, 'fault-trial', continuous=True
        )
        for step in steps[:3]:
            fault_device.sample(step.channel, step.value)
        first_try = cut(fault_archive, fault_sessions, fault_device, fault_session, 'A')
        if first_try.status != CutStatus.PENDING or first_try.issued is not None:
            raise AssertionError('a failed custody commit must return pending without issuance')
        if _segment_counts(fault_path) != {'custody': 0, 'reports': 0, 'receipts': 0}:
            raise AssertionError('failed custody must leave no archived rows')
        busy = cut(fault_archive, fault_sessions, fault_device, fault_session, 'B')
        if busy.status != CutStatus.BUSY or busy.issued is not None:
            raise AssertionError('a new request id must be busy while A is in progress')
        for step in steps[3:]:
            fault_device.sample(step.channel, step.value)
        retried = cut(fault_archive, fault_sessions, fault_device, fault_session, 'A')
        if retried.status != CutStatus.ISSUED:
            raise AssertionError('the same request id must continue along the original cut point')
        if retried.issued.report.original.boundary.last_sample_no != 3:
            raise AssertionError('retry must reuse the original boundary instead of a new cut')
        follow_up = cut(fault_archive, fault_sessions, fault_device, fault_session, 'B')
        if follow_up.status != CutStatus.ISSUED:
            raise AssertionError('after A is issued the new request must be accepted')
        follow_meter = follow_up.issued.report.original.snapshot
        if (follow_meter.count, follow_meter.peak, follow_meter.peak_sample_no) != (2, 12, 4):
            raise AssertionError('later segment must preserve its own peak after fault continuation')
        printed.append('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'v8.fault_continuation',
            'input': {'failed_commit': 'custody', 'pending_request': 'A', 'busy_request': 'B'},
            'expected': {
                'first_status': 'pending',
                'busy_status': 'busy',
                'retry_boundary_watermark': 3,
                'retry_outcome': 'pass',
                'follow_up': {'request_id': 'B', 'count': 2, 'peak': 12, 'peak_number': 4},
                'counts_after_recovery': {'custody': 2, 'reports': 2, 'receipts': 2},
            },
            'actual': {
                'first_status': first_try.status.value,
                'busy_status': busy.status.value,
                'retry_boundary_watermark': retried.issued.report.original.boundary.last_sample_no,
                'retry_outcome': retried.issued.report.outcome.value,
                'follow_up': {
                    'request_id': 'B', 'count': follow_meter.count,
                    'peak': follow_meter.peak, 'peak_number': follow_meter.peak_sample_no,
                },
                'counts_after_recovery': _segment_counts(fault_path),
            },
            'code_paths': ['capture/control.py', 'capture/segments.py', 'archive/store.py'],
            'oracle': '接管提交失败不发布，采样继续；同号沿原切点续办，在办新号返回忙，恢复后后段仍保 12 的峰。',
        }, ensure_ascii=False, sort_keys=True))

        issue_catalog, issue_profiles = TrialCatalog(), ChannelProfiles()
        issue_profiles.add(ChannelProfile(CHANNEL, '泵体测点'))
        issue_device = DevicePort()
        resources.callback(issue_device.close)
        issue_sessions = SessionRegistry()
        issue_fault = _OneShotFault(
            'issue', lambda: None
        )
        issue_archive = ArchiveStore.open(
            base / 'continuous-issue-fault.sqlite3', before_commit=issue_fault
        )
        resources.callback(issue_archive.close)
        register(issue_catalog, issue_profiles, 'issue-fault-trial', CHANNEL, 9)
        issue_session = start(
            issue_catalog, issue_sessions, issue_device, 'issue-fault-trial', continuous=True
        )
        issue_device.sample(CHANNEL, 7)
        failed = cut(issue_archive, issue_sessions, issue_device, issue_session, 'Q')
        if failed.status != CutStatus.PENDING:
            raise AssertionError('a failed issue commit must return pending')
        issue_fault_path = base / 'continuous-issue-fault.sqlite3'
        custody_rows = _segment_counts(issue_fault_path)
        if custody_rows != {'custody': 1, 'reports': 0, 'receipts': 0}:
            raise AssertionError('issue failure must leave the committed custody original')
        recovered = cut(issue_archive, issue_sessions, issue_device, issue_session, 'Q')
        if recovered.status != CutStatus.ISSUED or recovered.issued.report.original.boundary.last_sample_no != 1:
            raise AssertionError('the same request must reissue from the committed custody original')
        printed.append('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'v8.issue_fault_reissue',
            'input': {'failed_commit': 'issue', 'request': 'Q'},
            'expected': {'first_status': 'pending', 'custody_only': {'custody': 1, 'reports': 0, 'receipts': 0}},
            'actual': {
                'first_status': failed.status.value,
                'custody_only': custody_rows,
                'retry_status': recovered.status.value,
                'same_report_id': recovered.issued.report.report_id,
            },
            'code_paths': ['archive/store.py', 'capture/control.py'],
            'oracle': '签发失败留下已提交的接管证据，同号续签使用同一份原件并保持报告号。',
        }, ensure_ascii=False, sort_keys=True))
    return printed


def main() -> None:
    peak_expected = {
        'channel': CHANNEL, 'limit': 9, 'count': 3, 'first': 1, 'last': 3,
        'peak': 9, 'peak_number': 2, 'outcome': 'pass',
    }
    empty_expected = {
        'channel': CHANNEL, 'limit': 9, 'count': 0, 'first': None, 'last': None,
        'peak': None, 'peak_number': None, 'outcome': 'no_data',
    }
    with TemporaryDirectory(prefix='pump-bench-demo-') as directory:
        path = Path(directory) / 'handover.sqlite3'
        catalog, profiles, sessions = TrialCatalog(), ChannelProfiles(), SessionRegistry()
        profiles.add(ChannelProfile(CHANNEL, '泵体测点'))
        with ExitStack() as resources:
            device = DevicePort()
            resources.callback(device.close)
            archive = ArchiveStore.open(path)
            resources.callback(archive.close)
            peak, peak_input = _record_trial(catalog, profiles, sessions, device, archive, 'day-1', 'peak.json')
            empty, empty_input = _record_trial(catalog, profiles, sessions, device, archive, 'day-2', 'empty.json')
        with ExitStack() as resources:
            reopened = ArchiveStore.open(path)
            resources.callback(reopened.close)
            peak_again = read(reopened, peak.report.report_id)
            empty_again = read(reopened, empty.report.report_id)
        _print_handover(
            'v5', {'readings': peak_input, 'limit': 9}, peak_expected, peak, peak_again,
            '三条读数的最大值为 9，第二条首次到达 9，等于限值通过。',
        )
        _print_handover(
            'v7', {'readings': empty_input, 'limit': 9}, empty_expected, empty, empty_again,
            '没有样本，因此计数为零、首末号和峰值字段为空、裁决为无数据。',
        )
        for line in _run_continuous_demo(Path(directory)):
            print(line)


if __name__ == '__main__':
    main()
