"""iAP2 control session messages.

Messages are a 16-bit type-length-value format carried in the payload of a link
packet addressed to the control session:

    40 40 | length(2) | message id(2) | parameters...

Each parameter is itself length(2) | parameter id(2) | data, where the length
counts its own four header bytes.

Message identifiers below are the commonly cited ones. Apple's specification is
under NDA and was not consulted; anything unconfirmed against the hardware is
marked as such in the notes.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

START_OF_MESSAGE = b"\x40\x40"

# Identification
START_IDENTIFICATION = 0x1D00
IDENTIFICATION_INFORMATION = 0x1D01
IDENTIFICATION_ACCEPTED = 0x1D02
IDENTIFICATION_REJECTED = 0x1D03

# Authentication. The accessory proves itself to us, not the other way round,
# so implementing this side needs no Apple-issued material.
REQUEST_AUTH_CERTIFICATE = 0xAA00
AUTH_CERTIFICATE = 0xAA01
REQUEST_AUTH_CHALLENGE_RESPONSE = 0xAA02
AUTH_RESPONSE = 0xAA03
AUTH_FAILED = 0xAA04
AUTH_SUCCEEDED = 0xAA05

# External Accessory sessions. The camera carries its video over one of these,
# not over the vendor bulk endpoints.
START_EA_SESSION = 0xEA00
STOP_EA_SESSION = 0xEA01
REQUEST_APP_LAUNCH = 0xEA02

# EA protocol identifiers the camera advertises, from its IdentificationInformation.
EA_PROTOCOL_CONFIG = 0x00  # com.flir.rosebud.config
EA_PROTOCOL_FILEIO = 0x01  # com.flir.rosebud.fileio
EA_PROTOCOL_FRAME = 0x02  # com.flir.rosebud.frame

MESSAGE_NAMES = {
    START_EA_SESSION: "StartExternalAccessoryProtocolSession",
    STOP_EA_SESSION: "StopExternalAccessoryProtocolSession",
    REQUEST_APP_LAUNCH: "RequestAppLaunch",
    START_IDENTIFICATION: "StartIdentification",
    IDENTIFICATION_INFORMATION: "IdentificationInformation",
    IDENTIFICATION_ACCEPTED: "IdentificationAccepted",
    IDENTIFICATION_REJECTED: "IdentificationRejected",
    REQUEST_AUTH_CERTIFICATE: "RequestAuthenticationCertificate",
    AUTH_CERTIFICATE: "AuthenticationCertificate",
    REQUEST_AUTH_CHALLENGE_RESPONSE: "RequestAuthenticationChallengeResponse",
    AUTH_RESPONSE: "AuthenticationResponse",
    AUTH_FAILED: "AuthenticationFailed",
    AUTH_SUCCEEDED: "AuthenticationSucceeded",
}


class ControlError(ValueError):
    pass


@dataclass(frozen=True)
class Parameter:
    identifier: int
    data: bytes = b""

    def encode(self) -> bytes:
        return struct.pack(">HH", len(self.data) + 4, self.identifier) + self.data

    def __str__(self) -> str:
        preview = self.data[:32].hex(" ")
        text = self.data.split(b"\x00", 1)[0].decode("utf-8", "ignore")
        readable = f'  "{text}"' if text.isprintable() and len(text) > 1 else ""
        return f"param {self.identifier:#06x} ({len(self.data)}B) {preview}{readable}"


@dataclass
class Message:
    identifier: int
    parameters: list[Parameter] = field(default_factory=list)

    @property
    def name(self) -> str:
        return MESSAGE_NAMES.get(self.identifier, f"Unknown({self.identifier:#06x})")

    def encode(self) -> bytes:
        body = b"".join(p.encode() for p in self.parameters)
        return START_OF_MESSAGE + struct.pack(">HH", len(body) + 6, self.identifier) + body

    def parameter(self, identifier: int) -> bytes | None:
        return next((p.data for p in self.parameters if p.identifier == identifier), None)

    def __str__(self) -> str:
        return f"{self.name} ({len(self.parameters)} params)"


def start_ea_session(protocol_id: int, session_id: int) -> Message:
    """Ask the accessory to open an External Accessory protocol session."""
    return Message(
        START_EA_SESSION,
        [
            Parameter(0x0000, bytes([protocol_id])),
            Parameter(0x0001, struct.pack(">H", session_id)),
        ],
    )


def decode(payload: bytes) -> Message:
    """Parse one control-session message."""
    if len(payload) < 6:
        raise ControlError(f"short control message: {len(payload)} bytes")
    if not payload.startswith(START_OF_MESSAGE):
        raise ControlError(f"bad start of message: {payload[:2].hex()}")
    length, identifier = struct.unpack_from(">HH", payload, 2)
    if length > len(payload):
        raise ControlError(f"length {length} exceeds {len(payload)} bytes")

    parameters, pos = [], 6
    while pos + 4 <= length:
        param_len, param_id = struct.unpack_from(">HH", payload, pos)
        if param_len < 4 or pos + param_len > length:
            raise ControlError(f"bad parameter length {param_len} at offset {pos}")
        parameters.append(Parameter(param_id, payload[pos + 4 : pos + param_len]))
        pos += param_len
    return Message(identifier, parameters)
