"""iAP2 link layer.

Packet format, verified byte-for-byte against a link-synchronisation packet
emitted by a FLIR One:

    ff 5a  00 1a  80  74  00  00  99   <payload>  53
    |      |      |   |   |   |   |               |
    SOP    length |   seq ack |   header checksum payload checksum
                  control     session id

Both checksums are the two's complement of the byte sum: the header checksum
covers the eight bytes before it, the payload checksum covers the payload.
Lengths and multi-byte fields are big-endian.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

SOP = b"\xff\x5a"
HEADER_LEN = 9  # SOP(2) + length(2) + control(1) + seq(1) + ack(1) + session(1) + checksum(1)

# Control byte flags.
SYN = 0x80
ACK = 0x40
EAK = 0x20
RST = 0x10
SLP = 0x08

SESSION_CONTROL = 0x00
SESSION_FILE_TRANSFER = 0x01
SESSION_EXTERNAL_ACCESSORY = 0x02

SESSION_NAMES = {
    SESSION_CONTROL: "Control",
    SESSION_FILE_TRANSFER: "File Transfer",
    SESSION_EXTERNAL_ACCESSORY: "External Accessory",
}


def checksum(data: bytes) -> int:
    """Two's complement of the byte sum, as used for both iAP2 checksums."""
    return (-sum(data)) & 0xFF


class LinkError(ValueError):
    pass


@dataclass(frozen=True)
class Packet:
    control: int
    seq: int
    ack: int
    session: int
    payload: bytes = b""

    @property
    def is_syn(self) -> bool:
        return bool(self.control & SYN)

    @property
    def is_ack(self) -> bool:
        return bool(self.control & ACK)

    def flags(self) -> str:
        names = [
            n
            for n, bit in (("SYN", SYN), ("ACK", ACK), ("EAK", EAK), ("RST", RST), ("SLP", SLP))
            if self.control & bit
        ]
        return "|".join(names) or "-"

    def encode(self) -> bytes:
        total = HEADER_LEN + len(self.payload) + (1 if self.payload else 0)
        header = SOP + struct.pack(">HBBBB", total, self.control, self.seq, self.ack, self.session)
        out = bytearray(header)
        out.append(checksum(header))
        if self.payload:
            out += self.payload
            out.append(checksum(self.payload))
        return bytes(out)

    def __str__(self) -> str:
        return (
            f"Packet({self.flags()} seq={self.seq:#04x} ack={self.ack:#04x} "
            f"session={self.session} payload={len(self.payload)}B)"
        )


def decode(data: bytes) -> Packet:
    """Parse one packet, validating both checksums."""
    if len(data) < HEADER_LEN:
        raise LinkError(f"short packet: {len(data)} bytes")
    if not data.startswith(SOP):
        raise LinkError(f"bad start of packet: {data[:2].hex()}")
    total, control, seq, ack, session = struct.unpack_from(">HBBBB", data, 2)
    if checksum(data[:8]) != data[8]:
        raise LinkError(f"header checksum {data[8]:#04x} != {checksum(data[:8]):#04x}")
    if total > len(data):
        raise LinkError(f"length field {total} exceeds {len(data)} bytes received")
    payload = b""
    if total > HEADER_LEN:
        payload = data[HEADER_LEN : total - 1]
        if checksum(payload) != data[total - 1]:
            raise LinkError(f"payload checksum {data[total - 1]:#04x} != {checksum(payload):#04x}")
    return Packet(control=control, seq=seq, ack=ack, session=session, payload=payload)


def split_stream(data: bytes) -> list[bytes]:
    """Split a bulk read into individual packets on the SOP marker."""
    packets, pos = [], data.find(SOP)
    while pos >= 0 and pos + 4 <= len(data):
        total = struct.unpack_from(">H", data, pos + 2)[0]
        if total < HEADER_LEN or pos + total > len(data):
            break
        packets.append(data[pos : pos + total])
        pos = data.find(SOP, pos + total)
    return packets


@dataclass
class SessionSpec:
    identifier: int
    kind: int
    version: int = 1

    def __str__(self) -> str:
        return f"id={self.identifier} {SESSION_NAMES.get(self.kind, '?')} v{self.version}"


@dataclass
class LinkSync:
    """The link synchronisation payload both sides exchange."""

    version: int = 1
    max_outstanding_packets: int = 127
    max_packet_length: int = 65535
    retransmit_timeout_ms: int = 2000
    cumulative_ack_timeout_ms: int = 50
    max_retransmissions: int = 30
    max_cumulative_acks: int = 1
    sessions: list[SessionSpec] = field(default_factory=list)

    def encode(self) -> bytes:
        out = bytearray(
            struct.pack(
                ">BBHHHBB",
                self.version,
                self.max_outstanding_packets,
                self.max_packet_length,
                self.retransmit_timeout_ms,
                self.cumulative_ack_timeout_ms,
                self.max_retransmissions,
                self.max_cumulative_acks,
            )
        )
        for session in self.sessions:
            out += bytes([session.identifier, session.kind, session.version])
        return bytes(out)

    @classmethod
    def decode(cls, payload: bytes) -> LinkSync:
        if len(payload) < 10:
            raise LinkError(f"link sync payload too short: {len(payload)} bytes")
        (version, max_out, max_len, retransmit, cum_ack, max_retry, max_cum) = struct.unpack_from(
            ">BBHHHBB", payload, 0
        )
        sessions = [
            SessionSpec(payload[i], payload[i + 1], payload[i + 2])
            for i in range(10, len(payload) - 2, 3)
        ]
        return cls(version, max_out, max_len, retransmit, cum_ack, max_retry, max_cum, sessions)

    def session_for(self, kind: int) -> SessionSpec | None:
        return next((s for s in self.sessions if s.kind == kind), None)

    def __str__(self) -> str:
        return (
            f"LinkSync(v{self.version} maxOut={self.max_outstanding_packets} "
            f"maxLen={self.max_packet_length} rt={self.retransmit_timeout_ms}ms "
            f"cumAck={self.cumulative_ack_timeout_ms}ms retries={self.max_retransmissions} "
            f"sessions=[{', '.join(str(s) for s in self.sessions)}])"
        )
