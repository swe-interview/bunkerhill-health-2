"""Query the message store by patient id and optional keywords."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from hl7search.store import Store


def search_patient(
    db_path: str | Path,
    patient_id: str,
    keywords: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Return stored messages for ``patient_id``.

    ``keywords`` are optional case-insensitive substrings. When several are
    given, a message is returned only if every keyword occurs in its raw text.
    """

    with Store(db_path, readonly=True) as store:
        return store.search(patient_id, keywords or ())
