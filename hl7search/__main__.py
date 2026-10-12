"""Command line for the HL7 ingest server and the patient search tool."""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from hl7search.framing import MllpSession, wrap_frame
from hl7search.parse import ParseError, field_text, parse_message
from hl7search.report import format_report
from hl7search.samples import SAMPLE_ORM, load_messages
from hl7search.search import search_patient
from hl7search.server import serve

logger = logging.getLogger(__name__)

DEFAULT_DB = "hl7_messages.db"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 2575
DEFAULT_DATA = Path("data")
_ACCEPT_CODES = {"AA", "CA"}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="hl7search",
        description="Accept HL7 v2 messages over MLLP and search them by patient.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser(
        "serve", help="listen for MLLP-framed HL7 v2 messages and store them"
    )
    serve_parser.add_argument("--host", default=DEFAULT_HOST)
    serve_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve_parser.add_argument("--db", default=DEFAULT_DB, help="SQLite database path")

    search_parser = subparsers.add_parser(
        "search", help="search stored messages for one patient"
    )
    search_parser.add_argument("--patient-id", required=True, help="PID-3 identifier")
    search_parser.add_argument(
        "--keyword",
        action="append",
        default=[],
        help="case-insensitive substring of the raw message; repeat to require all",
    )
    search_parser.add_argument("--db", default=DEFAULT_DB, help="SQLite database path")
    search_parser.add_argument(
        "--json", action="store_true", help="print JSON instead of a text report"
    )

    send_parser = subparsers.add_parser("send", help="send one HL7 message over MLLP")
    send_parser.add_argument("--host", default=DEFAULT_HOST)
    send_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    send_parser.add_argument(
        "--file",
        type=Path,
        help="HL7 message file; omit to send the sample order",
    )

    stream_parser = subparsers.add_parser(
        "stream", help="send every HL7 file in a folder to the server"
    )
    stream_parser.add_argument("--host", default=DEFAULT_HOST)
    stream_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    stream_parser.add_argument(
        "--dir",
        type=Path,
        default=DEFAULT_DATA,
        help="folder of .hl7, .txt, or .er7 messages (default: data/)",
    )

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    if args.command == "serve":
        try:
            asyncio.run(serve(args.host, args.port, args.db))
        except KeyboardInterrupt:
            return
        return

    if args.command == "search":
        _search(args.db, args.patient_id, args.keyword, args.json)
        return

    if args.command == "stream":
        _stream_folder(args.host, args.port, args.dir)
        return

    payload = args.file.read_text() if args.file else SAMPLE_ORM
    ack = asyncio.run(_transmit(args.host, args.port, payload))
    text = ack.replace("\r\n", "\n").replace("\r", "\n")
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


def _search(db_path: str, patient_id: str, keywords: list[str], as_json: bool) -> None:
    try:
        records = search_patient(db_path, patient_id, keywords)
    except FileNotFoundError:
        sys.stderr.write(f"No database at {db_path}. Start the server first.\n")
        raise SystemExit(1) from None
    if as_json:
        json.dump(records, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return
    sys.stdout.write(format_report(patient_id, keywords, records))


def _stream_folder(host: str, port: int, directory: Path) -> None:
    try:
        messages = load_messages(directory)
    except (FileNotFoundError, NotADirectoryError):
        sys.stderr.write(f"No folder at {directory}. Put HL7 files in data/.\n")
        raise SystemExit(1) from None
    if not messages:
        sys.stderr.write(f"No .hl7, .txt, or .er7 messages in {directory}.\n")
        raise SystemExit(1)

    try:
        accepted, rejected = asyncio.run(_stream(host, port, messages))
    except ConnectionRefusedError:
        sys.stderr.write(
            f"Could not connect to {host}:{port}. "
            "Start the server with `hl7search serve` first.\n"
        )
        raise SystemExit(1) from None
    except ConnectionError as exc:
        sys.stderr.write(f"{exc}\n")
        raise SystemExit(1) from None

    sys.stdout.write(
        f"sent {accepted + rejected} messages from {directory}: "
        f"{accepted} accepted, {rejected} rejected\n"
    )
    if accepted == 0:
        raise SystemExit(1)


async def _stream(
    host: str, port: int, messages: list[tuple[Path, str]]
) -> tuple[int, int]:
    """Send each message on one MLLP connection and wait for its ACK."""

    reader, writer = await asyncio.open_connection(host, port)
    session = MllpSession(reader)
    accepted = 0
    rejected = 0
    total = len(messages)
    try:
        for index, (path, payload) in enumerate(messages, start=1):
            writer.write(wrap_frame(payload))
            await writer.drain()
            frame = await session.read_frame()
            if frame is None:
                raise ConnectionError(f"server closed without an ACK for {path}")
            ack = frame.decode("utf-8", errors="replace")
            if _ack_code(ack) in _ACCEPT_CODES:
                accepted += 1
                logger.info("accepted %s (%s/%s)", path, index, total)
            else:
                rejected += 1
                logger.warning("rejected %s (%s/%s)", path, index, total)
    finally:
        writer.close()
        await writer.wait_closed()
    return accepted, rejected


def _ack_code(ack: str) -> str:
    try:
        message = parse_message(ack)
    except ParseError:
        return ""
    msa = message.hl7.msa
    if not msa:
        return ""
    return field_text(msa.msa_1)


async def _transmit(host: str, port: int, payload: str) -> str:
    reader, writer = await asyncio.open_connection(host, port)
    try:
        writer.write(wrap_frame(payload))
        await writer.drain()
        frame = await MllpSession(reader).read_frame()
        if frame is None:
            raise ConnectionError("server closed without an ACK")
        return frame.decode("utf-8", errors="replace")
    finally:
        writer.close()
        await writer.wait_closed()


if __name__ == "__main__":
    main()
