"""Split an HL7 v2 message into segments, fields, and components.

Delimiters are not hardcoded. MSH-1 is the field separator and MSH-2 announces
the component, repetition, escape, and subcomponent characters. The usual set
is ``|``, then ``^~\\&``.
"""

from dataclasses import dataclass


class ParseError(Exception):
    """The payload is not a readable HL7 v2 message."""


@dataclass(frozen=True)
class Delimiters:
    field: str
    component: str
    repetition: str
    escape: str
    subcomponent: str


@dataclass(frozen=True)
class Segment:
    """One segment. ``get`` is 1-based, matching HL7 field numbers."""

    name: str
    fields: tuple[str, ...]

    def get(self, index: int) -> str:
        if index < 1 or index > len(self.fields):
            return ""
        return self.fields[index - 1]


@dataclass(frozen=True)
class Message:
    delimiters: Delimiters
    segments: tuple[Segment, ...]
    raw: str

    def segment(self, name: str) -> Segment | None:
        for segment in self.segments:
            if segment.name == name:
                return segment
        return None


def parse_message(raw: str) -> Message:
    """Parse ``raw`` into segments using the delimiters declared by MSH."""

    text = raw.replace("\r\n", "\r").replace("\n", "\r").strip("\r")
    segment_text = [part for part in text.split("\r") if part != ""]
    if not segment_text or not segment_text[0].startswith("MSH"):
        raise ParseError("message does not start with MSH")

    header = segment_text[0]
    if len(header) < 8:
        raise ParseError("MSH is too short to declare delimiters")

    field_sep = header[3]
    header_fields = header.split(field_sep)
    encoding = header_fields[1] if len(header_fields) > 1 else ""
    if len(encoding) < 4:
        raise ParseError("MSH-2 must declare four encoding characters")

    delimiters = Delimiters(
        field=field_sep,
        component=encoding[0],
        repetition=encoding[1],
        escape=encoding[2],
        subcomponent=encoding[3],
    )
    chars = (
        delimiters.field,
        delimiters.component,
        delimiters.repetition,
        delimiters.escape,
        delimiters.subcomponent,
    )
    if len(set(chars)) != 5:
        raise ParseError("field and encoding characters must be distinct")

    segments: list[Segment] = []
    for index, text_segment in enumerate(segment_text):
        parts = text_segment.split(field_sep)
        name = parts[0]
        if len(name) != 3 or not name.isalnum() or not name.isupper():
            raise ParseError(f"invalid segment name {name!r}")
        if index == 0:
            # MSH-1 is the separator itself, which split() does not yield.
            fields = (field_sep, *parts[1:])
        else:
            fields = tuple(parts[1:])
        segments.append(Segment(name=name, fields=fields))

    return Message(delimiters=delimiters, segments=tuple(segments), raw=raw)


def repetitions(field: str, delimiter: str) -> list[str]:
    if field == "":
        return []
    return field.split(delimiter)


def component(field: str, delimiter: str, index: int) -> str:
    """Return the 1-based component of ``field``."""

    if field == "" or index < 1:
        return ""
    parts = field.split(delimiter)
    if index > len(parts):
        return ""
    return parts[index - 1]


def unescape(value: str, delimiters: Delimiters) -> str:
    """Replace HL7 escape sequences such as ``\\T\\`` with the real character."""

    escape = delimiters.escape
    if escape not in value:
        return value

    simple = {
        "F": delimiters.field,
        "S": delimiters.component,
        "T": delimiters.subcomponent,
        "R": delimiters.repetition,
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
