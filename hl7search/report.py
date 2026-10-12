"""Plain-text view of search results."""

from typing import Any


def format_hl7_datetime(value: str) -> str:
    """Render an HL7 timestamp. Non-numeric values are returned unchanged."""

    if len(value) >= 14 and value[:14].isdigit():
        return (
            f"{value[0:4]}-{value[4:6]}-{value[6:8]} "
            f"{value[8:10]}:{value[10:12]}:{value[12:14]}"
        )
    if len(value) >= 8 and value[:8].isdigit():
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    return value


def format_report(
    patient_id: str, keywords: list[str], records: list[dict[str, Any]]
) -> str:
    """Format search hits for a person reading the terminal."""

    count = len(records)
    noun = "message" if count == 1 else "messages"
    header = f"{count} {noun} for patient {patient_id}"
    if keywords:
        quoted = ", ".join(repr(keyword) for keyword in keywords)
        header += f" matching {quoted}"
    if not records:
        return header + "\n"

    lines = [header, ""]
    for index, record in enumerate(records, start=1):
        lines.extend(_format_record(index, record))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _format_record(index: int, record: dict[str, Any]) -> list[str]:
    trigger = record["trigger_event"]
    label = record["message_type"] + (f"^{trigger}" if trigger else "")
    when = format_hl7_datetime(str(record["message_datetime"]))
    control_id = record["control_id"] or "-"
    lines = [f"{index}. {record['category']}  {label}  {when}  id {control_id}"]

    demographic = str(record["patient_name"])
    if record["birth_date"]:
        demographic += f"  DOB {format_hl7_datetime(str(record['birth_date']))}"
    if record["sex"]:
        demographic += f"  sex {record['sex']}"
    if demographic:
        lines.append(f"   {demographic}")

    visit = record.get("visit")
    if visit:
        detail = "  ".join(
            part
            for part in (
                str(visit["patient_class"]),
                str(visit["location"]),
                f"attending {visit['attending']}" if visit["attending"] else "",
            )
            if part
        )
        if detail:
            lines.append(f"   Visit: {detail}")

    for order in record["orders"]:
        control = order["control"] or "-"
        accession = order["accession"] or "-"
        lines.append(f"   Order {control}  accession {accession}")
        procedure = "  ".join(
            part
            for part in (order["procedure_code"], order["procedure_text"])
            if part
        )
        if procedure:
            lines.append(f"   Procedure: {procedure}")
        for observation in order["observations"]:
            lines.append(f"   {_format_observation(observation)}")
    return lines


def _format_observation(observation: dict[str, Any]) -> str:
    name = observation["text"] or observation["identifier"] or "observation"
    code = observation["identifier"]
    prefix = f"{code}  {name}" if code and code != name else name
    value = observation["value"]
    units = observation["units"]
    shown = f"{value} {units}".strip() if units else value
    line = f"Result: {observation['value_type']}  {prefix} = {shown}".rstrip()
    if observation["coding_system"]:
        line += f"  [{observation['coding_system']}]"
    if observation["status"]:
        line += f"  status {observation['status']}"
    return line
