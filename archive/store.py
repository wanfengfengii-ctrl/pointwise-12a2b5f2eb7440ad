from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from archive.receipts import Receipt, make_receipt, restore_receipt
from archive.schema import Report, decode_evidence, encode_evidence, initialize_archive
from archive.segment_schema import (
    SegmentReport,
    decode_segment_original,
    encode_segment_original,
    initialize_segment_archive,
)
from quality.limits import LimitOutcome


class ArchiveCommitError(RuntimeError):
    pass


class ArchiveConflictError(ValueError):
    pass


@dataclass(frozen=True)
class IssuedRecord:
    report: Report
    receipt: Receipt


@dataclass(frozen=True)
class SegmentIssuedRecord:
    report: SegmentReport
    receipt: Receipt


class ArchiveStore:
    def __init__(
        self,
        connection: sqlite3.Connection,
        before_commit: Callable[[], None] | None,
        before_custody_commit: Callable[[], None] | None = None,
    ):
        self._connection = connection
        self._before_commit = before_commit
        self._before_custody_commit = before_custody_commit

    @classmethod
    def open(
        cls,
        path: str | Path,
        *,
        before_commit: Callable[[], None] | None = None,
        before_custody_commit: Callable[[], None] | None = None,
    ) -> ArchiveStore:
        connection = sqlite3.connect(path, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys = ON')
            initialize_archive(connection)
            initialize_segment_archive(connection)
        except BaseException:
            connection.close()
            raise
        return cls(connection, before_commit, before_custody_commit)

    def close(self) -> None:
        self._connection.close()

    def publish(self, report: Report, receipt: Receipt) -> IssuedRecord:
        if receipt != make_receipt(report):
            raise ArchiveConflictError('receipt does not match the report')
        encoded = encode_evidence(report.evidence)
        connection = self._connection
        try:
            with connection:
                connection.execute('BEGIN IMMEDIATE')
                existing = self.find_by_session(report.session_id)
                if existing is not None:
                    if encoded != encode_evidence(existing.report.evidence) or report.outcome != existing.report.outcome:
                        raise ArchiveConflictError('session was already issued with different evidence')
                    return existing
                connection.execute(
                    'INSERT INTO reports (report_id, session_id, evidence, outcome) VALUES (?, ?, ?, ?)',
                    (report.report_id, report.session_id, encoded, report.outcome.value),
                )
                connection.execute(
                    'INSERT INTO receipts (report_id, receipt_id) VALUES (?, ?)',
                    (receipt.report_id, receipt.receipt_id),
                )
                if self._before_commit is not None:
                    self._before_commit()
        except sqlite3.Error as exc:
            raise ArchiveCommitError('issue transaction failed') from exc
        return IssuedRecord(report, receipt)

    def _find(self, column: str, key: str) -> IssuedRecord | None:
        row = self._connection.execute(
            'SELECT r.report_id, r.session_id, r.evidence, r.outcome, c.receipt_id '
            'FROM reports AS r JOIN receipts AS c ON c.report_id = r.report_id '
            f'WHERE r.{column} = ?',
            (key,),
        ).fetchone()
        if row is None:
            return None
        report = Report(
            row['report_id'], decode_evidence(row['session_id'], row['evidence']), LimitOutcome(row['outcome'])
        )
        receipt = restore_receipt(row['receipt_id'], report.report_id, report.session_id)
        return IssuedRecord(report, receipt)

    def find_by_session(self, session_id: str) -> IssuedRecord | None:
        return self._find('session_id', session_id)

    def find_by_report_id(self, report_id: str) -> IssuedRecord | None:
        return self._find('report_id', report_id)

    def commit_custody(self, report: SegmentReport) -> None:
        """Persist the capture-side segment original before any report is visible."""
        encoded = encode_segment_original(report.original)
        connection = self._connection
        try:
            with connection:
                connection.execute('BEGIN IMMEDIATE')
                row = connection.execute(
                    'SELECT original FROM segment_custody '
                    'WHERE session_id = ? AND request_id = ?',
                    (report.session_id, report.original.request_id),
                ).fetchone()
                if row is not None:
                    if row['original'] != encoded:
                        raise ArchiveConflictError(
                            'custody already holds a different segment original'
                        )
                    return
                connection.execute(
                    'INSERT INTO segment_custody (session_id, request_id, report_id, original) '
                    'VALUES (?, ?, ?, ?)',
                    (report.session_id, report.original.request_id, report.report_id, encoded),
                )
                if self._before_custody_commit is not None:
                    self._before_custody_commit()
        except sqlite3.Error as exc:
            raise ArchiveCommitError('custody transaction failed') from exc

    def find_custody(
        self, session_id: str, request_id: str
    ) -> tuple[str, SegmentReport] | None:
        row = self._connection.execute(
            'SELECT report_id, original FROM segment_custody '
            'WHERE session_id = ? AND request_id = ?',
            (session_id, request_id),
        ).fetchone()
        if row is None:
            return None
        original = decode_segment_original(row['original'])
        return row['report_id'], SegmentReport(row['report_id'], original, None)

    def publish_segment(
        self, report: SegmentReport, receipt: Receipt
    ) -> SegmentIssuedRecord:
        if receipt != make_receipt(report):
            raise ArchiveConflictError('receipt does not match the segment report')
        encoded = encode_segment_original(report.original)
        connection = self._connection
        try:
            with connection:
                connection.execute('BEGIN IMMEDIATE')
                existing = self.find_segment_by_request(
                    report.session_id, report.original.request_id
                )
                if existing is not None:
                    return existing
                custody = self.find_custody(
                    report.session_id, report.original.request_id
                )
                if custody is None:
                    raise ArchiveConflictError(
                        'segment issue requires a committed custody original'
                    )
                custody_report_id, custody_report = custody
                if (
                    custody_report_id != report.report_id
                    or encode_segment_original(custody_report.original) != encoded
                ):
                    raise ArchiveConflictError(
                        'segment issue must use the same committed custody original'
                    )
                connection.execute(
                    'INSERT INTO segment_reports '
                    '(report_id, session_id, request_id, original, outcome) '
                    'VALUES (?, ?, ?, ?, ?)',
                    (
                        report.report_id, report.session_id, report.original.request_id,
                        encoded, report.outcome.value,
                    ),
                )
                connection.execute(
                    'INSERT INTO segment_receipts (report_id, receipt_id) VALUES (?, ?)',
                    (receipt.report_id, receipt.receipt_id),
                )
                if self._before_commit is not None:
                    self._before_commit()
        except sqlite3.Error as exc:
            raise ArchiveCommitError('segment issue transaction failed') from exc
        return SegmentIssuedRecord(report, receipt)

    def find_segment_by_request(
        self, session_id: str, request_id: str
    ) -> SegmentIssuedRecord | None:
        row = self._connection.execute(
            'SELECT r.report_id, r.original, r.outcome, c.receipt_id '
            'FROM segment_reports AS r '
            'JOIN segment_receipts AS c ON c.report_id = r.report_id '
            'WHERE r.session_id = ? AND r.request_id = ?',
            (session_id, request_id),
        ).fetchone()
        if row is None:
            return None
        report = SegmentReport(
            row['report_id'],
            decode_segment_original(row['original']),
            LimitOutcome(row['outcome']),
        )
        receipt = restore_receipt(row['receipt_id'], report.report_id, report.session_id)
        return SegmentIssuedRecord(report, receipt)

    def find_segment_by_report_id(self, report_id: str) -> SegmentIssuedRecord | None:
        row = self._connection.execute(
            'SELECT r.report_id, r.original, r.outcome, c.receipt_id '
            'FROM segment_reports AS r '
            'JOIN segment_receipts AS c ON c.report_id = r.report_id '
            'WHERE r.report_id = ?',
            (report_id,),
        ).fetchone()
        if row is None:
            return None
        report = SegmentReport(
            row['report_id'],
            decode_segment_original(row['original']),
            LimitOutcome(row['outcome']),
        )
        receipt = restore_receipt(row['receipt_id'], report.report_id, report.session_id)
        return SegmentIssuedRecord(report, receipt)
