"""iAP2 framing, checked against a packet the camera actually sent."""

from __future__ import annotations

import pytest

from flirone.iap2 import control
from flirone.iap2.link import ACK, SYN, LinkError, LinkSync, Packet, checksum, decode, split_stream

# Captured from a FLIR One Gen 2 during link synchronisation.
REAL_SYN = bytes.fromhex("ff5a001a8074000099017fffff07d000321e0101000102020153")


def test_real_packet_decodes():
    packet = decode(REAL_SYN)
    assert packet.is_syn and not packet.is_ack
    assert packet.seq == 0x74
    assert packet.session == 0
    assert len(packet.payload) == 16


def test_real_packet_round_trips_byte_for_byte():
    assert decode(REAL_SYN).encode() == REAL_SYN


def test_real_link_sync_fields():
    sync = LinkSync.decode(decode(REAL_SYN).payload)
    assert sync.version == 1
    assert sync.max_outstanding_packets == 127
    assert sync.max_packet_length == 65535
    assert sync.retransmit_timeout_ms == 2000
    assert [s.kind for s in sync.sessions] == [0x00, 0x02]
    assert sync.session_for(0x02).identifier == 2
    assert sync.encode() == decode(REAL_SYN).payload


def test_checksum_is_twos_complement():
    assert checksum(REAL_SYN[:8]) == REAL_SYN[8]
    assert (sum(REAL_SYN[:8]) + REAL_SYN[8]) % 256 == 0


@pytest.mark.parametrize("index", [8, 25])
def test_corrupt_checksum_is_rejected(index):
    corrupted = bytearray(REAL_SYN)
    corrupted[index] ^= 0xFF
    with pytest.raises(LinkError):
        decode(bytes(corrupted))


def test_bad_start_of_packet_is_rejected():
    with pytest.raises(LinkError):
        decode(b"\x00\x00" + REAL_SYN[2:])


def test_empty_packet_round_trips():
    packet = Packet(ACK, 0x05, 0x56, 0)
    assert decode(packet.encode()) == packet


def test_split_stream_finds_both_packets():
    stream = REAL_SYN + Packet(SYN | ACK, 1, 2, 0).encode()
    assert len(split_stream(stream)) == 2


def test_control_message_round_trip():
    message = control.Message(
        control.START_EA_SESSION,
        [control.Parameter(0x0000, b"\x02"), control.Parameter(0x0001, b"\x00\x01")],
    )
    decoded = control.decode(message.encode())
    assert decoded.identifier == control.START_EA_SESSION
    assert decoded.parameter(0x0000) == b"\x02"
    assert decoded.parameter(0x0001) == b"\x00\x01"


def test_start_ea_session_encodes_as_observed():
    # The exact bytes sent to the camera, verified against its capability list.
    expected = "40400011ea00000500000200060001 0001".replace(" ", "")
    assert control.start_ea_session(2, 1).encode().hex() == expected
