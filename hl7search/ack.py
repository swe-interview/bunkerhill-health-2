"""Build HL7 ACK messages for MLLP replies."""

import uuid

from hl7apy.consts import VALIDATION_LEVEL
from hl7apy.core import Message as Hl7Message

from hl7search.parse import Message, component_text, field_text

OUR_APP = "HL7SEARCH"
OUR_FACILITY = "BH"

# hl7apy adds a truncation character on 2.7+ messages it creates itself.
# Replies to unparseable input use the four characters every peer accepts.
STANDARD_ENCODING = {
    "FIELD": "|",
    "COMPONENT": "^",
    "REPETITION": "~",
    "ESCAPE": "\\",
    "SUBCOMPONENT": "&",
    "GROUP": "\r",
    "SEGMENT": "\r",
}


def build_ack(message: Message, code: str, text: str = "") -> str:
    """Acknowledge a parsed message, echoing its delimiters and control id."""

    source = message.hl7
    header = source.msh
    if not header:
        return build_reject_ack("missing MSH")

    encoding = dict(source.encoding_chars)
    version = field_text(header.msh_12) or source.version or "2.8"
    trigger = component_text(header.msh_9, 2)
    separator = encoding["COMPONENT"]
    message_type = f"ACK{separator}{trigger}" if trigger else "ACK"

    ack = Hl7Message(
        "ACK", version=source.version or version, validation_level=VALIDATION_LEVEL.TOLERANT
    )
    ack.encoding_chars = encoding
    ack.msh.msh_3 = OUR_APP
    ack.msh.msh_4 = OUR_FACILITY
    ack.msh.msh_5 = field_text(header.msh_3)
    ack.msh.msh_6 = field_text(header.msh_4)
    ack.msh.msh_9 = message_type
    ack.msh.msh_10 = new_control_id()
    ack.msh.msh_11 = field_text(header.msh_11) or "P"
    ack.msh.msh_12 = version
    ack.add_segment("MSA")
    ack.msa.msa_1 = code
    ack.msa.msa_2 = field_text(header.msh_10)
    if text:
        ack.msa.msa_3 = _sanitize(text)
    return _render(ack)


def build_reject_ack(text: str) -> str:
    """ACK an unparseable payload. The original control id is unknown."""

    ack = Hl7Message("ACK", version="2.8", validation_level=VALIDATION_LEVEL.TOLERANT)
    ack.encoding_chars = dict(STANDARD_ENCODING)
    ack.msh.msh_3 = OUR_APP
    ack.msh.msh_4 = OUR_FACILITY
    ack.msh.msh_9 = "ACK"
    ack.msh.msh_10 = new_control_id()
    ack.msh.msh_11 = "P"
    ack.msh.msh_12 = "2.8"
    ack.add_segment("MSA")
    ack.msa.msa_1 = "AE"
    ack.msa.msa_3 = _sanitize(text)
    return _render(ack)


def new_control_id() -> str:
    return uuid.uuid4().hex[:16]


def _render(message: Hl7Message) -> str:
    text = message.to_er7()
    if not text.endswith("\r"):
        text += "\r"
    return text


def _sanitize(text: str) -> str:
    return text.replace("\r", " ").replace("\n", " ")[:200]
