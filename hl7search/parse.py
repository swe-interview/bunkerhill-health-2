"""Parse an HL7 v2 message with hl7apy.

hl7apy reads the delimiters from MSH and builds segments, fields, repetitions,
and components. Validation stays tolerant so a message that is not a perfect
example of its trigger event still parses. Groups stay flattened so later
segments remain in wire order: an OBX stays after the OBR it belongs to.

hl7apy leaves escape sequences such as ``\\T\\`` in field text. ``unescape``
turns those into the characters they stand for.
"""

from dataclasses import dataclass
from typing import Any

from hl7apy.consts import VALIDATION_LEVEL
from hl7apy.core import Message as Hl7Message
from hl7apy.exceptions import HL7apyException
from hl7apy.parser import parse_message as parse_er7


class ParseError(Exception):
    """The payload is not a readable HL7 v2 message."""


@dataclass(frozen=True)
class Message:
    """A parsed message plus the payload it came from."""

    hl7: Hl7Message
    raw: str


def parse_message(raw: str) -> Message:
    """Parse ``raw`` into an hl7apy message.

    Segment breaks may be ``\\r`` or ``\\n``. The stored ``raw`` is the
    original text, which is what keyword search scans.
    """

    text = raw.replace("\r\n", "\r").replace("\n", "\r").strip("\r")
    try:
        parsed = parse_er7(
            text,
            validation_level=VALIDATION_LEVEL.TOLERANT,
            find_groups=False,
        )
    except HL7apyException as exc:
        raise ParseError(str(exc)) from exc
    return Message(hl7=parsed, raw=raw)


def field_text(field: Any) -> str:
    """ER7 text of ``field``, with repetitions joined. Empty if it is absent."""

    reps = repetitions(field)
    if not reps:
        return ""
    if len(reps) == 1:
        return reps[0].to_er7()
    separator = reps[0].encoding_chars["REPETITION"]
    return separator.join(repetition.to_er7() for repetition in reps)


def component_text(field: Any, index: int) -> str:
    """ER7 text of the 1-based component in the first repetition of ``field``."""

    reps = repetitions(field)
    if not reps or index < 1:
        return ""
    return _component_of(reps[0], index)


def repetition_component(repetition: Any, index: int) -> str:
    """ER7 text of the 1-based component inside one field repetition."""

    return _component_of(repetition, index)


def repetitions(field: Any) -> list[Any]:
    """Each repetition of ``field``. Empty when the field is absent."""

    if not field:
        return []
    return list(field)


def unescape(value: str, encoding: dict[str, str]) -> str:
    """Replace HL7 escape sequences such as ``\\T\\`` with the real character."""

    escape = encoding["ESCAPE"]
    if escape not in value:
        return value

    simple = {
        "F": encoding["FIELD"],
        "S": encoding["COMPONENT"],
        "T": encoding["SUBCOMPONENT"],
        "R": encoding["REPETITION"],
        "E": escape,
    }
    pieces: list[str] = []
    cursor = 0
    while cursor < len(value):
        if value[cursor] != escape:
            pieces.append(value[cursor])
            cursor += 1
            continue
        closing = value.find(escape, cursor + 1)
        if closing == -1:
            pieces.append(value[cursor:])
            break
        token = value[cursor + 1 : closing]
        if token in simple:
            pieces.append(simple[token])
        elif token == ".br":
            pieces.append("\n")
        elif token.startswith("X"):
            try:
                pieces.append(bytes.fromhex(token[1:]).decode("ascii"))
            except ValueError:
                pieces.append(value[cursor : closing + 1])
        else:
            pieces.append(value[cursor : closing + 1])
        cursor = closing + 1
    return "".join(pieces)


def _component_of(repetition: Any, index: int) -> str:
    children = list(repetition.children)
    if len(children) == 1 and "_" not in children[0].name:
        return children[0].to_er7() if index == 1 else ""
    for child in children:
        suffix = child.name.rsplit("_", 1)[-1]
        if suffix == str(index):
            return child.to_er7()
    return ""
