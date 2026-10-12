"""Parser, search, and MLLP server tests."""

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from hl7search.__main__ import _stream, main
from hl7search.extract import extract
from hl7search.framing import MllpSession, wrap_frame
from hl7search.parse import ParseError, parse_message
from hl7search.report import format_report
from hl7search.samples import (
    SAMPLE_ADT,
    SAMPLE_ORM,
    SAMPLE_ORU,
    SAMPLE_OTHER_PATIENT,
    load_messages,
    split_messages,
)
from hl7search.server import accept_payload, start_server
from hl7search.store import Store


class ParseTests(unittest.TestCase):
    def test_sample_order_extracts_calcium_score(self) -> None:
        record = extract(parse_message(SAMPLE_ORM))

        self.assertEqual(record.patient_ids, ("PatientID",))
        self.assertEqual(record.patient_name, "PatientName")
        self.assertEqual(record.birth_date, "PatientBirthDate")
        self.assertEqual(record.message_type, "ORM")
        self.assertEqual(record.trigger_event, "O01")
        self.assertEqual(record.control_id, "AccessionNumber")
        self.assertEqual(record.category, "result")
        self.assertEqual(len(record.orders), 1)

        order = record.orders[0]
        self.assertEqual(order.control, "XO")
        self.assertEqual(order.accession, "AccessionNumber")
        self.assertEqual(order.procedure_code, "IMG200")
        self.assertEqual(order.procedure_text, "CT CHEST WITHOUT IV CONTRAST")
        self.assertEqual(order.observation_time, "")
        self.assertEqual(order.report_time, "20240509144522")
        self.assertEqual(len(order.observations), 1)

        observation = order.observations[0]
        self.assertEqual(observation.value_type, "NM")
        self.assertEqual(observation.identifier, "513230")
        self.assertEqual(observation.text, "Bunkerhill - Coronary Calcium Scoring AI")
        self.assertEqual(observation.coding_system, "Estimated calcium score")
        self.assertEqual(observation.value, "26")

    def test_oru_groups_observations_under_each_obr(self) -> None:
        record = extract(parse_message(SAMPLE_ORU))

        self.assertEqual(record.message_type, "ORU")
        self.assertEqual(record.category, "result")
        self.assertEqual(record.patient_name, "Jane Doe")
        self.assertEqual(len(record.orders), 2)
        self.assertEqual(
            [obs.identifier for obs in record.orders[0].observations],
            ["2093-3", "2085-9"],
        )
        cholesterol = record.orders[0].observations[0]
        self.assertEqual(cholesterol.value, "180")
        self.assertEqual(cholesterol.units, "mg/dL")
        self.assertEqual(cholesterol.status, "F")
        self.assertEqual(record.orders[1].accession, "ACC3")
        self.assertEqual(record.orders[1].observations[0].value, "13.5")

    def test_adt_extracts_visit(self) -> None:
        record = extract(parse_message(SAMPLE_ADT))

        self.assertEqual(record.message_type, "ADT")
        self.assertEqual(record.trigger_event, "A01")
        self.assertEqual(record.category, "visit")
        self.assertEqual(record.orders, ())
        self.assertIsNotNone(record.visit)
        assert record.visit is not None
        self.assertEqual(record.visit.patient_class, "I")
        self.assertEqual(record.visit.location, "ICU-101-A")
        self.assertEqual(record.visit.attending, "John Smith")

    def test_repeated_and_legacy_patient_ids(self) -> None:
        raw = (
            "MSH|^~\\&|APP|FAC|||20240101120000||ADT^A08|C1|P|2.3\r"
            "PID|1|LEGACY|AAA^^^MC~BBB^^^MC||Only^Legacy||19700101|\r"
        )
        record = extract(parse_message(raw))
        self.assertEqual(record.patient_ids, ("LEGACY", "AAA", "BBB"))

    def test_newline_separated_segments(self) -> None:
        raw = SAMPLE_ADT.replace("\r", "\n")
        record = extract(parse_message(raw))
        self.assertEqual(record.patient_ids, ("PatientID",))

    def test_custom_field_separator(self) -> None:
        raw = "MSH#^~\\&#APP#FAC###20240101120000##ADT^A01#C9#P#2.5\rPID#1##ZZZ\r"
        record = extract(parse_message(raw))
        self.assertEqual(record.patient_ids, ("ZZZ",))
        self.assertEqual(record.message_type, "ADT")

    def test_unescape_subcomponent_escape(self) -> None:
        raw = (
            "MSH|^~\\&|APP|FAC|||20240101120000||ORU^R01|C2|P|2.5\r"
            "PID|1||P1\r"
            r"OBX|1|ST|1^Note||pain \T\ swelling" "\r"
        )
        observation = extract(parse_message(raw)).orders[0].observations[0]
        self.assertEqual(observation.value, "pain & swelling")

    def test_rejects_message_without_msh(self) -> None:
        with self.assertRaises(ParseError):
            parse_message("PID|1||PatientID\r")


class SearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "messages.db")
        for raw in (SAMPLE_ADT, SAMPLE_ORM, SAMPLE_ORU, SAMPLE_OTHER_PATIENT):
            self.assertTrue(self.store.insert(extract(parse_message(raw))))

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_patient_returns_messages_in_time_order(self) -> None:
        rows = self.store.search("PatientID")
        self.assertEqual(
            [row["message_type"] for row in rows],
            ["ADT", "ORM", "ORU"],
        )

    def test_keywords_are_case_insensitive_and_combined_with_and(self) -> None:
        matched = self.store.search("PatientID", ["CALCIUM", "chest"])
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["control_id"], "AccessionNumber")
        self.assertEqual(self.store.search("PatientID", ["calcium", "mri"]), [])

        labs = self.store.search("PatientID", ["cholesterol", "hdl"])
        self.assertEqual([row["control_id"] for row in labs], ["MSG0002"])
        self.assertEqual(self.store.search("PatientID", ["cholesterol", "calcium"]), [])

    def test_keyword_does_not_cross_patients(self) -> None:
        own = self.store.search("PatientID", ["calcium"])
        other = self.store.search("SomeoneElse", ["calcium"])
        self.assertEqual([row["control_id"] for row in own], ["AccessionNumber"])
        self.assertEqual([row["control_id"] for row in other], ["MSG9000"])

    def test_unknown_patient(self) -> None:
        self.assertEqual(self.store.search("Nobody"), [])

    def test_duplicate_control_id_is_not_stored_twice(self) -> None:
        again = extract(parse_message(SAMPLE_ORM))
        self.assertFalse(self.store.insert(again))
        self.assertEqual(len(self.store.search("PatientID")), 3)

    def test_report_shows_extracted_fields(self) -> None:
        rows = self.store.search("PatientID")
        calcium = self.store.search("PatientID", ["calcium"])
        report = format_report("PatientID", ["calcium"], calcium)
        self.assertIn("IMG200", report)
        self.assertIn("CT CHEST WITHOUT IV CONTRAST", report)
        self.assertIn("= 26", report)
        self.assertIn("Estimated calcium score", report)

        visit = format_report("PatientID", [], [rows[0]])
        self.assertIn("Jane Doe", visit)
        self.assertIn("ICU-101-A", visit)
        self.assertIn("John Smith", visit)


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_mllp_round_trip(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "messages.db")
            server = await start_server("127.0.0.1", 0, store)
            sockets = server.sockets or ()
            port = sockets[0].getsockname()[1]
            try:
                ack = await _send(port, b"junk" + wrap_frame(SAMPLE_ORM))
                self.assertIn("MSA|AA|AccessionNumber", ack)
                self.assertIn("ACK^O01", ack)

                ack = await _send(port, wrap_frame(SAMPLE_ADT) + wrap_frame(SAMPLE_ORU))
                self.assertEqual(ack.count("MSA|AA|"), 2)

                ack = await _send(port, wrap_frame("this is not hl7"))
                self.assertIn("MSA|AE|", ack)

                ack = await _send(port, wrap_frame(SAMPLE_ORM))
                self.assertIn("MSA|AA|AccessionNumber", ack)
            finally:
                server.close()
                await server.wait_closed()

            rows = store.search("PatientID", ["calcium"])
            self.assertEqual(len(rows), 1)
            orders = rows[0]["orders"]
            assert isinstance(orders, list)
            observations = orders[0]["observations"]
            self.assertEqual(observations[0]["value"], "26")
            self.assertEqual(len(store.search("PatientID")), 3)
            store.close()

    async def test_stream_sends_a_folder_on_one_connection(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            root.mkdir()
            (root / "adt.hl7").write_text(SAMPLE_ADT.replace("\r", "\n"))
            (root / "bad.hl7").write_text("MSH|x\n")
            (root / "orm.hl7").write_text(SAMPLE_ORM)
            store = Store(Path(tmp) / "messages.db")
            server = await start_server("127.0.0.1", 0, store)
            port = (server.sockets or ())[0].getsockname()[1]
            try:
                accepted, rejected = await _stream(
                    "127.0.0.1", port, load_messages(root)
                )
            finally:
                server.close()
                await server.wait_closed()

            self.assertEqual((accepted, rejected), (2, 1))
            self.assertEqual(len(store.search("PatientID")), 2)
            store.close()

    def test_accept_payload_skips_message_without_patient(self) -> None:
        raw = "MSH|^~\\&|APP|FAC|||20240101120000||ACK^O01|ACK1|P|2.5\rMSA|AA|MSG1\r"
        with TemporaryDirectory() as tmp:
            with Store(Path(tmp) / "messages.db") as store:
                ack = accept_payload(raw.encode(), store)
                self.assertIn("MSA|AA|ACK1", ack)
                self.assertEqual(store.search("PatientID"), [])


async def _send(port: int, frame: bytes) -> str:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(frame)
        await writer.drain()
        session = MllpSession(reader)
        pieces: list[bytes] = []
        while True:
            try:
                payload = await asyncio.wait_for(session.read_frame(), timeout=2)
            except TimeoutError:
                break
            if payload is None:
                break
            pieces.append(payload)
            if len(pieces) >= frame.count(b"\x0b"):
                break
        return b"\n".join(pieces).decode()
    finally:
        writer.close()
        await writer.wait_closed()


class LoadTests(unittest.TestCase):
    def test_loads_nested_files_and_splits_on_msh(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "adt.hl7").write_text(SAMPLE_ADT.replace("\r", "\n"))
            (root / "both.txt").write_text(
                "FHS|^~\\&|\rBHS|^~\\&|\r"
                + SAMPLE_ORM
                + "\r"
                + SAMPLE_ORU
                + "\rBTS|1\rFTS|1\r"
            )
            nested = root / "nested"
            nested.mkdir()
            (nested / "other.er7").write_bytes(
                b"\xef\xbb\xbf" + SAMPLE_OTHER_PATIENT.encode()
            )
            (root / "skip.md").write_text(SAMPLE_ADT)
            (root / ".hidden.hl7").write_text(SAMPLE_ADT)
            loaded = load_messages(root)

        names = [path.name for path, _ in loaded]
        self.assertEqual(names, ["adt.hl7", "both.txt", "both.txt", "other.er7"])
        self.assertNotIn("FHS", loaded[1][1])
        self.assertIn("AccessionNumber", loaded[1][1])
        self.assertIn("MSG0002", loaded[2][1])
        self.assertIn("SomeoneElse", loaded[3][1])

    def test_mllp_dump_is_unwrapped(self) -> None:
        dump = (wrap_frame(SAMPLE_ADT) + wrap_frame(SAMPLE_ORM)).decode()
        messages = split_messages(dump)
        self.assertEqual(len(messages), 2)
        self.assertIn("MSG0003", messages[0])
        self.assertIn("AccessionNumber", messages[1])

    def test_missing_or_empty_folder_exits(self) -> None:
        with self.assertRaises(SystemExit) as missing:
            main(["stream", "--dir", str(Path("/tmp") / "hl7search-missing-data")])
        self.assertEqual(missing.exception.code, 1)

        with TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as empty:
                main(["stream", "--dir", tmp])
            self.assertEqual(empty.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
