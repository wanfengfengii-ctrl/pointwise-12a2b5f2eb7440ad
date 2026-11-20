from dataclasses import dataclass

from archive.schema import Report


@dataclass(frozen=True)
class Receipt:
    receipt_id: str
    report_id: str
    session_id: str


def make_receipt(report: Report) -> Receipt:
    return Receipt(f'receipt-{report.report_id}', report.report_id, report.session_id)


def restore_receipt(receipt_id: str, report_id: str, session_id: str) -> Receipt:
    return Receipt(receipt_id, report_id, session_id)
