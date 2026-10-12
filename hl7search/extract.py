"""Pull medically relevant fields out of a parsed HL7 v2 message.

Search handles message types by the segments they carry, not by a fixed list
of trigger events:

- Every stored message is indexed under each patient identifier. Those ids
  come from PID-3 (the patient identifier list). A legacy id in PID-2 is
  indexed too. Keywords are matched against the raw message text, so they
  work the same way for every message type.
- ORM (order) messages contribute ORC and OBR. An OBX under that order is a
  result attached to the order. The sample calcium score is this shape:
  ORM^O01 with one OBX.
- ORU (observation result) messages are the usual results feed. Each OBR
  starts a group and the following OBX rows belong to it.
- ADT (admit, discharge, transfer) messages contribute demographics and the
  PV1 visit. They normally have no order and no result.
- Any other message type uses the same segment rules. If it has a patient id
  it is still stored and keyword-searchable. ACK messages and anything else
  without a patient id are acknowledged by the server and not stored.
"""

from hl7search.models import ClinicalRecord, Observation, Order, Visit
from hl7search.parse import Delimiters, Message, Segment, component, repetitions, unescape


def extract(message: Message) -> ClinicalRecord:
    """Build a clinical record from ``message``."""

    delimiters = message.delimiters
    header = message.segment("MSH")
    if header is None:
        raise ValueError("parsed message is missing MSH")

    message_type = _text(component(header.get(9), delimiters.component, 1), delimiters)
    trigger = _text(component(header.get(9), delimiters.component, 2), delimiters)
    patient_ids, name, birth_date, sex = _patient(message.segment("PID"), delimiters)

    orders: list[Order] = []
    pending_control = ""
    pending_placer = ""
    pending_filler = ""
    visit: Visit | None = None
    open_observations: list[Observation] = []
    current: dict[str, str] | None = None

    def close_order() -> None:
        nonlocal current, open_observations
        if current is None:
            return
        orders.append(
            Order(
                control=current["control"],
                placer_order=current["placer_order"],
                accession=current["accession"],
                procedure_code=current["procedure_code"],
                procedure_text=current["procedure_text"],
                observation_time=current["observation_time"],
                report_time=current["report_time"],
                observations=tuple(open_observations),
            )
        )
        current = None
        open_observations = []

    for segment in message.segments:
        if segment.name == "ORC":
            pending_control = _text(segment.get(1), delimiters)
            pending_placer = _identifier(segment.get(2), delimiters)
            pending_filler = _identifier(segment.get(3), delimiters)
        elif segment.name == "OBR":
            close_order()
            procedure = segment.get(4)
            current = {
                "control": pending_control,
                "placer_order": _identifier(segment.get(2), delimiters) or pending_placer,
                "accession": _identifier(segment.get(3), delimiters) or pending_filler,
                "procedure_code": _text(component(procedure, delimiters.component, 1), delimiters),
                "procedure_text": _text(component(procedure, delimiters.component, 2), delimiters),
                "observation_time": _text(segment.get(7), delimiters),
                "report_time": _text(segment.get(22), delimiters),
            }
            open_observations = []
        elif segment.name == "OBX":
            if current is None:
                current = {
                    "control": pending_control,
                    "placer_order": pending_placer,
                    "accession": pending_filler,
                    "procedure_code": "",
                    "procedure_text": "",
                    "observation_time": "",
                    "report_time": "",
                }
                open_observations = []
            open_observations.append(_observation(segment, delimiters))
        elif segment.name == "PV1" and visit is None:
            visit = _visit(segment, delimiters)

    close_order()

    return ClinicalRecord(
        patient_ids=patient_ids,
        patient_name=name,
        birth_date=birth_date,
        sex=sex,
        message_type=message_type,
        trigger_event=trigger,
        message_datetime=_text(header.get(7), delimiters),
        control_id=_text(header.get(10), delimiters),
        sending_app=_text(header.get(3), delimiters),
        sending_facility=_text(header.get(4), delimiters),
        category=_category(message_type, orders, visit),
        orders=tuple(orders),
        visit=visit,
        raw=message.raw,
    )


def _patient(
    pid: Segment | None, delimiters: Delimiters
) -> tuple[tuple[str, ...], str, str, str]:
    if pid is None:
        return (), "", "", ""

    ids: list[str] = []
    legacy = _identifier(pid.get(2), delimiters)
    if legacy:
        ids.append(legacy)
    for repetition in repetitions(pid.get(3), delimiters.repetition):
        identifier = _identifier(repetition, delimiters)
        if identifier and identifier not in ids:
            ids.append(identifier)
    name = _person_name(pid.get(5), delimiters)
    birth_date = _text(pid.get(7), delimiters)
    sex = _text(pid.get(8), delimiters)
    return tuple(ids), name, birth_date, sex


def _observation(segment: Segment, delimiters: Delimiters) -> Observation:
    identifier_field = segment.get(3)
    return Observation(
        set_id=_text(segment.get(1), delimiters),
        value_type=_text(segment.get(2), delimiters),
        identifier=_text(component(identifier_field, delimiters.component, 1), delimiters),
        text=_text(component(identifier_field, delimiters.component, 2), delimiters),
        coding_system=_text(component(identifier_field, delimiters.component, 3), delimiters),
        value=_text(segment.get(5), delimiters),
        units=_text(component(segment.get(6), delimiters.component, 1), delimiters),
        status=_text(segment.get(11), delimiters),
    )


def _visit(segment: Segment, delimiters: Delimiters) -> Visit:
    location = segment.get(3)
    parts = [
        _text(component(location, delimiters.component, index), delimiters)
        for index in (1, 2, 3)
    ]
    return Visit(
        patient_class=_text(segment.get(2), delimiters),
        location="-".join(part for part in parts if part),
        attending=_clinician(segment.get(7), delimiters),
    )


def _category(message_type: str, orders: list[Order], visit: Visit | None) -> str:
    if any(order.observations for order in orders):
        return "result"
    if orders or message_type == "ORM":
        return "order"
    if visit is not None or message_type == "ADT":
        return "visit"
    return "message"


def _identifier(field: str, delimiters: Delimiters) -> str:
    """First component of an identifier field, which is the id itself."""

    return _text(component(field, delimiters.component, 1), delimiters).strip()


def _person_name(field: str, delimiters: Delimiters) -> str:
    """Format an XPN name. A single component is kept as written."""

    parts = [_text(part, delimiters) for part in field.split(delimiters.component)] if field else []
    if len(parts) <= 1:
        return parts[0] if parts else ""
    family = parts[0]
    given = parts[1] if len(parts) > 1 else ""
    middle = parts[2] if len(parts) > 2 else ""
    suffix = parts[3] if len(parts) > 3 else ""
    prefix = parts[4] if len(parts) > 4 else ""
    return " ".join(part for part in (prefix, given, middle, family, suffix) if part)


def _clinician(field: str, delimiters: Delimiters) -> str:
    """Format the first XCN repetition as ``given family``, or the id."""

    reps = repetitions(field, delimiters.repetition)
    if not reps:
        return ""
    parts = [_text(part, delimiters) for part in reps[0].split(delimiters.component)]
    identifier = parts[0] if parts else ""
    family = parts[1] if len(parts) > 1 else ""
    given = parts[2] if len(parts) > 2 else ""
    name = " ".join(part for part in (given, family) if part)
    return name or identifier


def _text(value: str, delimiters: Delimiters) -> str:
    return unescape(value, delimiters)
