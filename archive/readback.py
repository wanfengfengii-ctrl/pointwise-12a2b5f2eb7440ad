from archive.store import ArchiveStore, IssuedRecord


class ReportLookupError(KeyError):
    pass


def read(store: ArchiveStore, report_id: str) -> IssuedRecord:
    if not isinstance(report_id, str):
        raise ReportLookupError(report_id)
    issued = store.find_by_report_id(report_id)
    if issued is None:
        raise ReportLookupError(report_id)
    return issued


def find_committed_issue(store: ArchiveStore, session_id: str) -> IssuedRecord | None:
    return store.find_by_session(session_id)
