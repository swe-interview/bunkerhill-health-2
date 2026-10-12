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

from typing import Any

from hl7search.models import ClinicalRecord, Observation, Order, Visit
from hl7search.parse import (
    Message,
    component_text,
    field_text,
    repetition_component,
    repetitions,
    unescape,
)


def extract(message: Message) -> ClinicalRecord:
    """Build a clinical record from ``message``."""

    encoding = message.hl7.encoding_chars
    header = message.hl7.msh
    if not header:
        raise ValueError("parsed message is missing MSH")

    message_type = _text(component_text(header.msh_9, 1), encoding)
    trigger = _text(component_text(header.msh_9, 2), encoding)
    patient_ids, name, birth_date, sex = _patient(message.hl7.pid, encoding)

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

    for segment in message.hl7.children:
        if segment.name == "ORC":
            pending_control = _text(field_text(segment.orc_1), encoding)
            pending_placer = _identifier(segment.orc_2, encoding)
            pending_filler = _identifier(segment.orc_3, encoding)
        elif segment.name == "OBR":
            close_order()
            procedure = segment.obr_4
            current = {
                "control": pending_control,
                "placer_order": _identifier(segment.obr_2, encoding) or pending_placer,
                "accession": _identifier(segment.obr_3, encoding) or pending_filler,
                "procedure_code": _text(component_text(procedure, 1), encoding),
                "procedure_text": _text(component_text(procedure, 2), encoding),
                "observation_time": _text(field_text(segment.obr_7), encoding),
                "report_time": _text(field_text(segment.obr_22), encoding),
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
            open_observations.append(_observation(segment, encoding))
        elif segment.name == "PV1" and visit is None:
            visit = _visit(segment, encoding)

    close_order()

    return ClinicalRecord(
        patient_ids=patient_ids,
        patient_name=name,
        birth_date=birth_date,
        sex=sex,
        message_type=message_type,
        trigger_event=trigger,
        message_datetime=_text(field_text(header.msh_7), encoding),
        control_id=_text(field_text(header.msh_10), encoding),
        sending_app=_text(field_text(header.msh_3), encoding),
        sending_facility=_text(field_text(header.msh_4), encoding),
        category=_category(message_type, orders, visit),
        orders=tuple(orders),
        visit=visit,
        raw=message.raw,
    )


def _patient(
    pid: Any, encoding: dict[str, str]
) -> tuple[tuple[str, ...], str, str, str]:
    if not pid:
        return (), "", "", ""

    ids: list[str] = []
    legacy = _identifier(pid.pid_2, encoding)
    if legacy:
        ids.append(legacy)
    for repetition in repetitions(pid.pid_3):
        identifier = _text(repetition_component(repetition, 1), encoding).strip()
        if identifier and identifier not in ids:
            ids.append(identifier)
    name = _person_name(pid.pid_5, encoding)
    birth_date = _text(field_text(pid.pid_7), encoding)
    sex = _text(field_text(pid.pid_8), encoding)
    return tuple(ids), name, birth_date, sex


def _observation(segment: Any, encoding: dict[str, str]) -> Observation:
    identifier_field = segment.obx_3
    return Observation(
        set_id=_text(field_text(segment.obx_1), encoding),
        value_type=_text(field_text(segment.obx_2), encoding),
        identifier=_text(component_text(identifier_field, 1), encoding),
        text=_text(component_text(identifier_field, 2), encoding),
        coding_system=_text(component_text(identifier_field, 3), encoding),
        value=_text(field_text(segment.obx_5), encoding),
        units=_text(component_text(segment.obx_6, 1), encoding),
        status=_text(field_text(segment.obx_11), encoding),
    )


def _visit(segment: Any, encoding: dict[str, str]) -> Visit:
    location = segment.pv1_3
    parts = [_text(component_text(location, index), encoding) for index in (1, 2, 3)]
    return Visit(
        patient_class=_text(field_text(segment.pv1_2), encoding),
        location="-".join(part for part in parts if part),
        attending=_clinician(segment.pv1_7, encoding),
    )


def _category(message_type: str, orders: list[Order], visit: Visit | None) -> str:
    if any(order.observations for order in orders):
        return "result"
    if orders or message_type == "ORM":
        return "order"
    if visit is not None or message_type == "ADT":
        return "visit"
    return "message"


def _identifier(field: Any, encoding: dict[str, str]) -> str:
    """First component of an identifier field, which is the id itself."""

    return _text(component_text(field, 1), encoding).strip()


def _person_name(field: Any, encoding: dict[str, str]) -> str:
    """Format an XPN name. A single component is kept as written."""

    parts = [_text(component_text(field, index), encoding) for index in range(1, 6)]
    if not any(parts[1:]):
        return parts[0]
    family, given, middle, suffix, prefix = parts
    return " ".join(part for part in (prefix, given, middle, family, suffix) if part)


def _clinician(field: Any, encoding: dict[str, str]) -> str:
    """Format the first XCN repetition as ``given family``, or the id."""

    reps = repetitions(field)
    if not reps:
        return ""
    identifier = _text(repetition_component(reps[0], 1), encoding)
    family = _text(repetition_component(reps[0], 2), encoding)
    given = _text(repetition_component(reps[0], 3), encoding)
    name = " ".join(part for part in (given, family) if part)
    return name or identifier


def _text(value: str, encoding: dict[str, str]) -> str:
    return unescape(value, encoding)
