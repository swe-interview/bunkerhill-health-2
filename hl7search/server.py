"""TCP server that accepts MLLP-framed HL7 v2 messages and stores them."""

import asyncio
import logging

from hl7search.ack import build_ack, build_reject_ack
from hl7search.extract import extract
from hl7search.framing import IncompleteFrame, MllpSession, wrap_frame
from hl7search.parse import ParseError, parse_message
from hl7search.store import Store

logger = logging.getLogger(__name__)


async def start_server(host: str, port: int, store: Store) -> asyncio.Server:
    """Start accepting connections. The caller owns ``store``."""

    async def handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await handle_connection(reader, writer, store)

    return await asyncio.start_server(handler, host, port)


async def serve(host: str, port: int, db_path: str) -> None:
    """Listen until cancelled, persisting messages to ``db_path``."""

    store = Store(db_path)
    server = await start_server(host, port, store)
    for sock in server.sockets or ():
        bound_host, bound_port = sock.getsockname()[:2]
        logger.info("listening on %s:%s", bound_host, bound_port)
    logger.info("database %s", store.path)
    try:
        async with server:
            await server.serve_forever()
    finally:
        store.close()


async def handle_connection(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, store: Store
) -> None:
    """Read frames until the peer closes, and ACK each one."""

    peer = writer.get_extra_info("peername")
    session = MllpSession(reader)
    try:
        while True:
            try:
                payload = await session.read_frame()
            except IncompleteFrame as exc:
                logger.warning("incomplete frame from %s: %s", peer, exc)
                return
            if payload is None:
                return
            reply = accept_payload(payload, store)
            writer.write(wrap_frame(reply))
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        logger.info("connection closed by %s", peer)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass


def accept_payload(payload: bytes, store: Store) -> str:
    """Parse, store, and return the ACK text for one payload."""

    try:
        text = payload.decode("utf-8")
        message = parse_message(text)
        record = extract(message)
    except UnicodeDecodeError as exc:
        logger.info("rejected message: %s", exc)
        return build_reject_ack("message is not valid UTF-8")
    except ParseError as exc:
        logger.info("rejected message: %s", exc)
        return build_reject_ack(str(exc))

    if not record.patient_ids:
        logger.info(
            "accepted %s^%s with no patient id; not stored",
            record.message_type,
            record.trigger_event,
        )
        return build_ack(message, "AA")

    stored = store.insert(record)
    kind = "stored" if stored else "duplicate"
    logger.info(
        "%s %s^%s patient=%s control=%s",
        kind,
        record.message_type,
        record.trigger_event,
        ",".join(record.patient_ids),
        record.control_id,
    )
    return build_ack(message, "AA")
