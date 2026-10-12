"""MLLP framing for HL7 v2 over TCP.

A frame is ``0x0B`` + payload + ``0x1C 0x0D``. Bytes before the start block
are ignored so a connection can resynchronize. One connection may carry
several frames.
"""

import asyncio

MLLP_START = b"\x0b"
MLLP_END = b"\x1c\x0d"
_MAX_FRAME = 8 * 1024 * 1024


class IncompleteFrame(Exception):
    """The peer closed the socket in the middle of a frame."""


def wrap_frame(payload: str) -> bytes:
    """Wrap an HL7 message in an MLLP frame."""

    return MLLP_START + payload.encode("utf-8") + MLLP_END


class MllpSession:
    """Read MLLP frames from an asyncio stream."""

    def __init__(self, reader: asyncio.StreamReader) -> None:
        self._reader = reader
        self._buffer = bytearray()

    async def read_frame(self) -> bytes | None:
        """Return the next payload, or ``None`` when the peer closes cleanly."""

        while True:
            start = self._buffer.find(MLLP_START)
            if start == -1:
                chunk = await self._reader.read(4096)
                if not chunk:
                    if self._buffer:
                        raise IncompleteFrame("connection closed before an MLLP start block")
                    return None
                self._buffer.extend(chunk)
                if len(self._buffer) > _MAX_FRAME:
                    self._buffer.clear()
                continue

            if start:
                del self._buffer[:start]
            end = self._buffer.find(MLLP_END)
            if end == -1:
                chunk = await self._reader.read(4096)
                if not chunk:
                    raise IncompleteFrame("connection closed inside an MLLP frame")
                self._buffer.extend(chunk)
                if len(self._buffer) > _MAX_FRAME:
                    raise IncompleteFrame("MLLP frame exceeds 8MiB")
                continue

            payload = bytes(self._buffer[1:end])
            del self._buffer[: end + len(MLLP_END)]
            return payload
