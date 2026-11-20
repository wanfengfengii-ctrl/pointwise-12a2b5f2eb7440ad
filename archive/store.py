from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from archive.receipts import Receipt, make_receipt, restore_receipt
from archive.schema import Report, decode_evidence, encode_evidence, initialize_archive
from quality.limits import LimitOutcome


class ArchiveCommitError(RuntimeError):
    pass


class ArchiveConflictError(ValueError):
    pass


@dataclass(frozen=True)
class IssuedRecord:
    report: Report
    receipt: Receipt


class ArchiveStore:
    def __init__(self, connection: sqlite3.Connection, before_commit: Callable[[], None] | None):
        self._connection = connection
        self._before_commit = before_commit

    @classmethod
    def open(
        cls, path: str | Path, *, before_commit: Callable[[], None] | None = None
    ) -> ArchiveStore:
        connection = sqlite3.connect(path, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys = ON')
            initialize_archive(connection)
        except BaseException:
            connection.close()
            raise
        return cls(connection, before_commit)

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
