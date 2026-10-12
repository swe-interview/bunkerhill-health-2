"""Clinical records extracted from HL7 v2 messages."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Observation:
    """One OBX row: a single reported value."""

    set_id: str
    value_type: str
    identifier: str
    text: str
    coding_system: str
    value: str
    units: str
    status: str


@dataclass(frozen=True)
class Order:
    """An OBR, with the ORC that preceded it and any OBX rows under it."""

    control: str
    placer_order: str
    accession: str
    procedure_code: str
    procedure_text: str
    observation_time: str
    report_time: str
    observations: tuple[Observation, ...]


@dataclass(frozen=True)
class Visit:
    """The PV1 segment on an ADT (or any other) message."""

    patient_class: str
    location: str
    attending: str


@dataclass(frozen=True)
class ClinicalRecord:
    """Medically relevant fields pulled from one HL7 message.

    ``category`` is how search presents the message:

    - ``result`` when the message carries OBX values (typical of ORU, and of an
      ORM that includes a result, like the sample calcium score)
    - ``order`` when it has an OBR but no results
    - ``visit`` for ADT and any other message whose clinical content is PV1
    - ``message`` when nothing structured was present beyond demographics
    """

    patient_ids: tuple[str, ...]
    patient_name: str
    birth_date: str
    sex: str
    message_type: str
    trigger_event: str
    message_datetime: str
    control_id: str
    sending_app: str
    sending_facility: str
    category: str
    orders: tuple[Order, ...]
    visit: Visit | None
    raw: str
