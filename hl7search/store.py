"""SQLite data layer for accepted HL7 messages.

The server writes here. Search opens the same file read-only. WAL mode lets
a search run while the server is ingesting.
"""

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from hl7search.models import ClinicalRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL,
    control_id TEXT NOT NULL,
    sending_app TEXT NOT NULL,
    sending_facility TEXT NOT NULL,
    message_type TEXT NOT NULL,
    trigger_event TEXT NOT NULL,
    message_datetime TEXT NOT NULL,
    raw TEXT NOT NULL,
    summary_json TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS messages_dedup
ON messages (sending_app, sending_facility, control_id)
WHERE control_id != '';

CREATE TABLE IF NOT EXISTS patient_ids (
    message_id INTEGER NOT NULL REFERENCES messages(id),
    patient_id TEXT NOT NULL,
    PRIMARY KEY (message_id, patient_id)
);

CREATE INDEX IF NOT EXISTS patient_ids_by_patient
ON patient_ids (patient_id);
"""


class Store:
    """Persisted HL7 messages, indexed by patient identifier."""

    def __init__(self, path: str | Path, *, readonly: bool = False) -> None:
        self.path = Path(path)
        self._readonly = readonly
        if readonly:
            if not self.path.exists():
                raise FileNotFoundError(self.path)
            uri = f"{self.path.resolve().as_uri()}?mode=ro"
            self._conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        else:
            self._conn = sqlite3.connect(self.path, timeout=5.0)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(_SCHEMA)
        self._conn.execute("PRAGMA busy_timeout=5000")

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    def insert(self, record: ClinicalRecord) -> bool:
        """Store ``record``. Return False when this control id was already stored."""

        if self._readonly:
            raise RuntimeError("store is read-only")
        if not record.patient_ids:
            raise ValueError("refusing to store a message with no patient id")

        payload = asdict(record)
        raw = payload.pop("raw")
        summary = json.dumps(payload)
        received_at = datetime.now(timezone.utc).isoformat()

        try:
            with self._conn:
                cursor = self._conn.execute(
                    """
                    INSERT INTO messages (
                        received_at, control_id, sending_app, sending_facility,
                        message_type, trigger_event, message_datetime, raw, summary_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        received_at,
                        record.control_id,
                        record.sending_app,
                        record.sending_facility,
                        record.message_type,
                        record.trigger_event,
                        record.message_datetime,
                        raw,
                        summary,
                    ),
                )
                message_id = cursor.lastrowid
                self._conn.executemany(
                    "INSERT INTO patient_ids (message_id, patient_id) VALUES (?, ?)",
                    [(message_id, patient_id) for patient_id in record.patient_ids],
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def search(self, patient_id: str, keywords: Sequence[str] = ()) -> list[dict[str, object]]:
        """Return messages for ``patient_id``.

        Every non-empty keyword must occur in the raw message. Matching is
        case-insensitive. Keywords are combined with AND.
        """

        clauses = ["p.patient_id = ?"]
        params: list[object] = [patient_id]
        for keyword in keywords:
            cleaned = keyword.strip()
            if not cleaned:
                continue
            clauses.append("instr(lower(m.raw), lower(?)) > 0")
            params.append(cleaned)

        sql = f"""
            SELECT m.received_at, m.summary_json, m.raw
            FROM messages m
            JOIN patient_ids p ON p.message_id = m.id
            WHERE {" AND ".join(clauses)}
            ORDER BY m.message_datetime, m.id
        """
        results: list[dict[str, object]] = []
        for received_at, summary_json, raw in self._conn.execute(sql, params):
            item = json.loads(summary_json)
            item["received_at"] = received_at
            item["raw"] = raw
            results.append(item)
        return results
