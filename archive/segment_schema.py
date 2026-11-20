import json
import sqlite3
from dataclasses import dataclass

from archive.schema import (
    SchemaError,
    _decode_control,
    _decode_sample,
    _encode_control,
    _encode_sample,
    decode_measure,
    encode_measure,
)
from capture.segments import SegmentOriginal
from device.registers import RegisterSnapshot
from quality.limits import LimitOutcome


@dataclass(frozen=True)
class SegmentReport:
    report_id: str
    original: SegmentOriginal
    outcome: LimitOutcome

    @property
    def session_id(self) -> str:
        return self.original.session_id


def initialize_segment_archive(connection: sqlite3.Connection) -> None:
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('''
            CREATE TABLE IF NOT EXISTS segment_custody (
                session_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                report_id TEXT UNIQUE NOT NULL,
                original TEXT NOT NULL,
                PRIMARY KEY (session_id, request_id)
            )
        ''')
        connection.execute('''
            CREATE TABLE IF NOT EXISTS segment_reports (
                report_id TEXT PRIMARY KEY NOT NULL,
                session_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                original TEXT NOT NULL,
                outcome TEXT NOT NULL CHECK (outcome IN ('pass', 'fail', 'no_data')),
                UNIQUE (session_id, request_id)
            )
        ''')
        connection.execute('''
            CREATE TABLE IF NOT EXISTS segment_receipts (
                report_id TEXT PRIMARY KEY NOT NULL
                    REFERENCES segment_reports(report_id),
                receipt_id TEXT UNIQUE NOT NULL
            )
        ''')


def encode_segment_original(original: SegmentOriginal) -> str:
    snapshot = original.snapshot
    document = {
        'session_id': original.session_id,
        'device_id': original.device_id,
        'channel': original.channel,
        'limit': encode_measure(original.limit),
        'request_id': original.request_id,
        'previous': _encode_control(original.previous),
        'boundary': _encode_control(original.boundary),
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


def decode_segment_original(encoded: str) -> SegmentOriginal:
    try:
        data = json.loads(encoded)
        meter = data['meter']
        snapshot = RegisterSnapshot(
            _required(meter['count']),
            decode_measure(meter['first']),
            decode_measure(meter['last']),
            decode_measure(meter['peak']),
            decode_measure(meter['peak_number']),
            _decode_sample(meter['peak_sample']),
        )
        original = SegmentOriginal(
            data['session_id'],
            data['device_id'],
            data['channel'],
            _required(data['limit']),
            data['request_id'],
            _decode_control(data['previous']),
            _decode_control(data['boundary']),
            snapshot,
        )
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SchemaError('malformed archived segment evidence') from exc
    return original


def _required(encoded: str) -> int:
    decoded = decode_measure(encoded)
    if decoded is None:
        raise SchemaError('missing required integer in archived segment evidence')
    return decoded
