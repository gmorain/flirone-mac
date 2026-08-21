"""Probe output, checked against the descriptor the camera actually returned."""

from __future__ import annotations

import io

import usb.util

from flirone import probe

# Read off the wire with usbmon on Ubuntu 22.04, kernel 5.15, during the
# enumeration loop of section 12:
#   12010002 00000040 cb099619 08010102 0301
CAPTURED = {
    "idVendor": 0x09CB,
    "idProduct": 0x1996,
    "bcdDevice": 0x0108,
    "bNumConfigurations": 1,
}


class FakeEndpoint:
    def __init__(self, address: int, attributes: int, packet: int) -> None:
        self.bEndpointAddress = address
        self.bmAttributes = attributes
        self.wMaxPacketSize = packet


class FakeInterface:
    def __init__(self, number: int, alt: int, endpoints: list[FakeEndpoint]) -> None:
        self.bInterfaceNumber = number
        self.bAlternateSetting = alt
        self.bInterfaceClass = 0xFF  # vendor specific
        self._endpoints = endpoints

    def __iter__(self):
        return iter(self._endpoints)


class FakeConfiguration:
    def __init__(self, value: int, interfaces: list[FakeInterface]) -> None:
        self.bConfigurationValue = value
        self.bNumInterfaces = len({i.bInterfaceNumber for i in interfaces})
        self._interfaces = interfaces

    def __iter__(self):
        return iter(self._interfaces)


class FakeDevice:
    def __init__(self) -> None:
        for name, value in CAPTURED.items():
            setattr(self, name, value)
        self.iManufacturer = 1
        self.iProduct = 2
        self.iSerialNumber = 3
        self.bus = 1
        self.address = 112
        # pyusb reads langids before fetching a string; empty means unreadable.
        self.langids = ()
        bulk = usb.util.ENDPOINT_TYPE_BULK
        self._configurations = [
            FakeConfiguration(
                3,
                [
                    FakeInterface(0, 0, [FakeEndpoint(0x81, bulk, 512)]),
                    FakeInterface(
                        1, 0, [FakeEndpoint(0x02, bulk, 512), FakeEndpoint(0x83, bulk, 512)]
                    ),
                    # 0x85 exists only in alternate setting 1; alt 0 has none.
                    FakeInterface(2, 0, []),
                    FakeInterface(2, 1, [FakeEndpoint(0x85, bulk, 512)]),
                ],
            )
        ]

    def __iter__(self):
        return iter(self._configurations)


def test_bcd_decodes_the_captured_version():
    assert probe._bcd(0x0108) == "1.08"


def test_header_reports_the_captured_identity():
    text = "\n".join(probe.describe(FakeDevice()))
    assert "09cb:1996" in text
    assert "1.08" in text
    assert "bus 1 address 112" in text


def test_single_configuration_is_numbered_three():
    """The camera has one configuration whose value is 3, not three of them."""
    text = "\n".join(probe.describe(FakeDevice()))
    assert "configurations 1" in text
    assert "configuration 3, 3 interfaces" in text


def test_interfaces_are_named_and_endpoints_listed():
    text = "\n".join(probe.describe(FakeDevice()))
    assert "(control)" in text
    assert "(fileio)" in text
    assert "(frame)" in text
    assert "EP 0x85 bulk in" in text
    assert "no endpoints" in text  # frame, alternate setting 0


def test_unreadable_strings_are_flagged_not_omitted():
    # get_string does a control transfer, which fails without device access.
    text = "\n".join(probe.describe(FakeDevice()))
    assert "(unreadable)" in text


def test_strings_are_shown_when_readable(monkeypatch):
    monkeypatch.setattr(
        usb.util,
        "get_string",
        lambda dev, index: {1: "FLIR Systems", 2: "FLIR ONE Camera", 3: "FLIRONEF02F9T00570A"}[
            index
        ],
    )
    text = "\n".join(probe.describe(FakeDevice()))
    assert "FLIR Systems" in text
    assert "FLIRONEF02F9T00570A" in text


def test_missing_camera_exits_one_with_a_useful_message(monkeypatch):
    class NoDevice:
        def wait_for_device(self, timeout_s):
            raise probe.FlirUsbError("No FLIR One found. Power the camera on")

    monkeypatch.setattr(probe, "FlirOneLink", lambda *a, **k: NoDevice())
    out = io.StringIO()
    assert probe.probe(wait_s=0.0, out=out) == 1
    assert "No FLIR One found" in out.getvalue()


def test_missing_libusb_is_distinguished_from_missing_camera(monkeypatch):
    def no_backend(*args, **kwargs):
        raise probe.FlirUsbError("libusb 1.0 not found")

    monkeypatch.setattr(probe, "FlirOneLink", no_backend)
    out = io.StringIO()
    assert probe.probe(wait_s=0.0, out=out) == 2
    assert "libusb" in out.getvalue()


def test_describe_is_read_only(monkeypatch):
    """describe() must not claim interfaces; --open is the only path that does."""
    calls: list[str] = []
    monkeypatch.setattr(usb.util, "claim_interface", lambda *a, **k: calls.append("claim"))
    monkeypatch.setattr(usb.util, "release_interface", lambda *a, **k: calls.append("release"))
    probe.describe(FakeDevice())
    assert calls == []
