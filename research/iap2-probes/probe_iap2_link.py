"""Answer the camera's iAP2 SYN and see whether the link establishes."""
from __future__ import annotations
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import usb.core, usb.util
from flirone.iap2.link import ACK, SOP, SYN, LinkError, LinkSync, Packet, decode, split_stream
from flirone.usb_link import make_backend

B = make_backend()
IAP1_HELLO = bytes.fromhex("ff550200ee10")


def pounce(t=30.0):
    end = time.monotonic() + t
    while time.monotonic() < end:
        d = usb.core.find(idVendor=0x09CB, idProduct=0x1996, backend=B)
        if d is not None:
            return d
        time.sleep(0.001)
    return None


def run(attempt: int) -> bool:
    dev = pounce()
    if dev is None:
        print("  no device")
        return False
    t0 = time.monotonic()
    usb.util.claim_interface(dev, 0)

    def now():
        return time.monotonic() - t0

    state = "waiting"
    our_seq = 0x00
    established = False
    sent_syn_ack = False

    while now() < 12.0:
        try:
            data = bytes(dev.read(0x81, 4096, 50))
        except usb.core.USBTimeoutError:
            continue
        except usb.core.USBError as e:
            print(f"  t+{now():5.2f}  device gone ({e})")
            break
        if not data:
            continue

        # The camera opens in iAP1 and only moves to iAP2 once we write to it.
        if data == IAP1_HELLO:
            if state == "waiting":
                dev.write(0x02, IAP1_HELLO, 200)
                state = "nudged"
                print(f"  t+{now():5.2f}  iAP1 hello -> nudged")
            continue

        for raw in split_stream(data):
            try:
                pkt = decode(raw)
            except LinkError as exc:
                print(f"  t+{now():5.2f}  undecodable: {exc}  {raw[:24].hex()}")
                continue
            print(f"  t+{now():5.2f}  RX {pkt}")

            if pkt.is_syn and not pkt.is_ack and not sent_syn_ack:
                sync = LinkSync.decode(pkt.payload)
                print(f"           their sync: {sync}")
                # Agree to exactly what the camera proposed.
                reply = Packet(
                    control=SYN | ACK, seq=our_seq, ack=pkt.seq,
                    session=0, payload=sync.encode(),
                )
                dev.write(0x02, reply.encode(), 500)
                sent_syn_ack = True
                print(f"  t+{now():5.2f}  TX {reply}  (SYN|ACK, agreeing to their parameters)")
            elif pkt.is_ack and sent_syn_ack and not established:
                established = True
                print(f"  t+{now():5.2f}  *** LINK ESTABLISHED ***")
            elif pkt.payload:
                print(f"           payload: {pkt.payload[:64].hex(' ')}")

    print(f"  survived {now():.2f}s  link_established={established}")
    try:
        usb.util.dispose_resources(dev)
    except Exception:
        pass
    return established


for attempt in range(1, 4):
    print(f"=== attempt {attempt} ===")
    if run(attempt):
        print(">>> handshake progressed")
        break
