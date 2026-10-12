"""HL7 v2 messages used by the sender and the tests.

The ORM sample is the message from the interview prompt. ``load_messages``
reads the same kind of file from a folder on disk.
"""

from pathlib import Path

SAMPLE_ORM = (
    r"MSH|^~\&|BUNKERHILL|BH|||20240509144522||ORM^O01|"
    "AccessionNumber|P|2.8\r"
    r"PID|1||PatientID^^^MC^MC||PatientName||PatientBirthDate|" "\r"
    r"ORC|XO||||||||" "\r"
    r"OBR|1||AccessionNumber|IMG200^CT CHEST WITHOUT IV CONTRAST"
    "||||||||||||||||||20240509144522\r"
    r"OBX|1|NM|513230^Bunkerhill - Coronary Calcium Scoring AI"
    r"^Estimated calcium score||26"
)

SAMPLE_ORU = (
    "MSH|^~\\&|LAB|HOSP|||20240510120000||ORU^R01|MSG0002|P|2.5\r"
    "PID|1||PatientID^^^MC^MC||Doe^Jane||19800101|F\r"
    "OBR|1||ACC2|2093-3^Cholesterol|||20240510115900\r"
    "OBX|1|NM|2093-3^Cholesterol||180|mg/dL|0-200|N|||F\r"
    "OBX|2|NM|2085-9^HDL||55|mg/dL|||||F\r"
    "OBR|2||ACC3|718-7^Hemoglobin|||20240510115930\r"
    "OBX|1|NM|718-7^Hemoglobin||13.5|g/dL|||||F\r"
)

SAMPLE_ADT = (
    "MSH|^~\\&|REG|HOSP|||20240509100000||ADT^A01|MSG0003|P|2.5\r"
    "PID|1||PatientID^^^MC^MC||Doe^Jane||19800101|F\r"
    "PV1|1|I|ICU^101^A||||1234^Smith^John\r"
)

SAMPLE_OTHER_PATIENT = (
    "MSH|^~\\&|LAB|HOSP|||20240601090000||ORU^R01|MSG9000|P|2.5\r"
    "PID|1||SomeoneElse^^^MC^MC||Roe^Richard||19700101|M\r"
    "OBR|1||ACC9|999^Calcium follow-up\r"
    "OBX|1|NM|999^Calcium||4|mg/dL|||||F\r"
)

MESSAGE_SUFFIXES = {".hl7", ".txt", ".er7"}
_BATCH_SEGMENTS = {"FHS", "BHS", "BTS", "FTS"}


def load_messages(directory: Path) -> list[tuple[Path, str]]:
    """Return ``(path, message)`` for every HL7 message under ``directory``.

    Each ``.hl7``, ``.txt``, or ``.er7`` file is one message, or several
    messages one after another. Segment breaks may be ``\\r``, ``\\n``, or
    ``\\r\\n``. Batch envelopes (FHS/BHS/BTS/FTS) are left out.
    """

    if not directory.exists():
        raise FileNotFoundError(directory)
    if not directory.is_dir():
        raise NotADirectoryError(directory)

    found: list[tuple[Path, str]] = []
    paths = sorted(path for path in directory.rglob("*") if path.is_file())
    for path in paths:
        if path.name.startswith(".") or path.suffix.lower() not in MESSAGE_SUFFIXES:
            continue
        for message in split_messages(_read_text(path)):
            found.append((path, message))
    return found


def split_messages(text: str) -> list[str]:
    """Split file text into HL7 messages, each starting with MSH."""

    if "\x0b" in text:
        messages: list[str] = []
        for chunk in text.split("\x0b"):
            payload = chunk.split("\x1c", 1)[0]
            messages.extend(_split_er7(payload))
        return messages
    return _split_er7(text)


def _split_er7(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    groups: list[list[str]] = []
    current: list[str] = []
    for line in normalized.split("\n"):
        segment = line.strip().strip("\x1c")
        if segment == "" or segment[:3] in _BATCH_SEGMENTS:
            continue
        if segment.startswith("MSH") and current:
            groups.append(current)
            current = [segment]
            continue
        current.append(segment)
    if current:
        groups.append(current)
    return ["\r".join(segments) for segments in groups if segments[0].startswith("MSH")]


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")
