"""Build HL7 ACK messages for MLLP replies."""

import uuid
from datetime import datetime

from hl7search.parse import Message, component

OUR_APP = "HL7SEARCH"
OUR_FACILITY = "BH"


def build_ack(message: Message, code: str, text: str = "") -> str:
    """Acknowledge a parsed message, echoing its delimiters and control id."""

    header = message.segment("MSH")
    if header is None:
        return build_reject_ack("missing MSH")

    delimiters = message.delimiters
    trigger = component(header.get(9), delimiters.component, 2)
    message_type = f"ACK{delimiters.component}{trigger}" if trigger else "ACK"
    encoding = (
        f"{delimiters.component}{delimiters.repetition}"
        f"{delimiters.escape}{delimiters.subcomponent}"
    )
    fields = [
        "MSH",
        encoding,
        OUR_APP,
        OUR_FACILITY,
        header.get(3),
        header.get(4),
        _now(),
        "",
        message_type,
        new_control_id(),
        header.get(11) or "P",
        header.get(12) or "2.8",
    ]
    msh = delimiters.field.join(fields)
    msa_fields = ["MSA", code, header.get(10)]
    if text:
        msa_fields.append(_sanitize(text, delimiters.field + encoding))
    msa = delimiters.field.join(msa_fields)
    return f"{msh}\r{msa}\r"


def build_reject_ack(text: str) -> str:
    """ACK an unparseable payload. The original control id is unknown."""

    encoding = r"^~\&"
    fields = [
        "MSH",
        encoding,
        OUR_APP,
        OUR_FACILITY,
        "",
        "",
        _now(),
        "",
        "ACK",
        new_control_id(),
        "P",
        "2.8",
    ]
    msh = "|".join(fields)
    msa = "|".join(["MSA", "AE", "", _sanitize(text, "|" + encoding)])
    return f"{msh}\r{msa}\r"


def new_control_id() -> str:
    return uuid.uuid4().hex[:16]


def _now() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def _sanitize(text: str, reserved: str) -> str:
    cleaned = text.replace("\r", " ").replace("\n", " ")
    for char in reserved:
        cleaned = cleaned.replace(char, " ")
    return cleaned[:200]
