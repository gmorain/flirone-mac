import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import usb.core, usb.util
from flirone import protocol as proto
from flirone.usb_link import make_backend

TYPES = {0: "CONTROL", 1: "ISOCHRONOUS", 2: "BULK", 3: "INTERRUPT"}
b = make_backend()
dev = None
t = time.monotonic() + 20
while time.monotonic() < t:
    dev = usb.core.find(idVendor=proto.VENDOR_ID, idProduct=proto.PRODUCT_ID, backend=b)
    if dev: break
    time.sleep(0.01)
if not dev:
    print("no device"); raise SystemExit(1)

for cfg in dev:
    print(f"config {cfg.bConfigurationValue}  maxpower={cfg.bMaxPower*2}mA  attrs={cfg.bmAttributes:#04x}")
    for intf in cfg:
        print(f"  iface {intf.bInterfaceNumber} alt {intf.bAlternateSetting}")
        for ep in intf:
            et = TYPES.get(ep.bmAttributes & 0x3, "?")
            print(f"    ep {ep.bEndpointAddress:#04x} {et:11s} maxpacket={ep.wMaxPacketSize} interval={ep.bInterval}")
