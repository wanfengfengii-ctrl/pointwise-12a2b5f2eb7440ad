import json
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

from archive.issue import issue
from archive.readback import read
from archive.store import ArchiveStore, IssuedRecord
from capture.control import start
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


if __name__ == '__main__':
    main()
