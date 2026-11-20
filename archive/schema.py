import json
import re
import sqlite3
from dataclasses import dataclass

from capture.stop import StoppedEvidence
from device.events import ControlEvent, SampleEvent
from device.port import StoppedSlot
from device.registers import RegisterSnapshot
from jobs.enrollment import FrozenTrial
from quality.limits import LimitOutcome


class SchemaError(ValueError):
    pass


@dataclass(frozen=True)
class Report:
    report_id: str
    evidence: StoppedEvidence
    outcome: LimitOutcome

    @property
    def session_id(self) -> str:
        return self.evidence.session_id


def initialize_archive(connection: sqlite3.Connection) -> None:
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('''
            CREATE TABLE IF NOT EXISTS reports (
                report_id TEXT PRIMARY KEY NOT NULL,
                session_id TEXT UNIQUE NOT NULL,
                evidence TEXT NOT NULL,
                outcome TEXT NOT NULL CHECK (outcome IN ('pass', 'fail', 'no_data'))
            )
        ''')
        connection.execute('''
            CREATE TABLE IF NOT EXISTS receipts (
                report_id TEXT PRIMARY KEY NOT NULL REFERENCES reports(report_id),
                receipt_id TEXT UNIQUE NOT NULL
            )
        ''')


def encode_measure(value: int | None) -> str | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise SchemaError('measurement encoding requires a nonnegative integer or null')
    return format(value, 'x')


def decode_measure(encoded: str | None) -> int | None:
    if encoded is None:
        return None
    if not isinstance(encoded, str) or re.fullmatch(r'0|[1-9a-f][0-9a-f]*', encoded) is None:
        raise SchemaError('invalid encoded measurement')
    return int(encoded, 16)


def _decode_required(encoded: str) -> int:
    decoded = decode_measure(encoded)
    if decoded is None:
        raise SchemaError('missing required integer in archived evidence')
    return decoded


def _encode_control(event: ControlEvent) -> dict:
    return {
        'sequence': encode_measure(event.sequence), 'action': event.action,
        'watermark': encode_measure(event.last_sample_no), 'channel': event.channel,
        'slot': encode_measure(event.slot), 'previous_slot': encode_measure(event.previous_slot),
    }


def _decode_control(data: dict) -> ControlEvent:
    return ControlEvent(
        _decode_required(data['sequence']), data['action'], _decode_required(data['watermark']),
        data['channel'], decode_measure(data['slot']), decode_measure(data['previous_slot']),
    )


def _encode_sample(event: SampleEvent | None) -> dict | None:
    if event is None:
        return None
    return {
        'sequence': encode_measure(event.sequence), 'number': encode_measure(event.sample_no),
        'channel': event.channel, 'value': encode_measure(event.value),
    }


def _decode_sample(data: dict | None) -> SampleEvent | None:
    if data is None:
        return None
    return SampleEvent(
        _decode_required(data['sequence']), _decode_required(data['number']),
        data['channel'], _decode_required(data['value']),
    )


def encode_evidence(evidence: StoppedEvidence) -> str:
    original = evidence.original
    snapshot = original.snapshot
    document = {
        'trial_id': evidence.trial.trial_id,
        'channel': evidence.trial.channel,
        'limit': encode_measure(evidence.trial.limit),
        'device_id': original.device_id,
        'start': _encode_control(original.start),
        'stop': _encode_control(original.stop),
        'meter': {
            'count': encode_measure(snapshot.count),
            'first': encode_measure(snapshot.first_sample_no),
            'last': encode_measure(snapshot.last_sample_no),
            'peak': encode_measure(snapshot.peak),
            'peak_number': encode_measure(snapshot.peak_sample_no),
            'peak_sample': _encode_sample(snapshot.peak_sample),
        },
    }
    return json.dumps(document, sort_keys=True, separators=(',', ':'))


def decode_evidence(session_id: str, encoded: str) -> StoppedEvidence:
    try:
        data = json.loads(encoded)
        trial = FrozenTrial(data['trial_id'], data['channel'], _decode_required(data['limit']))
        meter = data['meter']
        snapshot = RegisterSnapshot(
            _decode_required(meter['count']), decode_measure(meter['first']),
            decode_measure(meter['last']), decode_measure(meter['peak']),
            decode_measure(meter['peak_number']), _decode_sample(meter['peak_sample']),
        )
        original = StoppedSlot(
            data['device_id'], _decode_control(data['start']), _decode_control(data['stop']), snapshot
        )
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SchemaError('malformed archived stop evidence') from exc
    return StoppedEvidence(session_id, trial, original)
