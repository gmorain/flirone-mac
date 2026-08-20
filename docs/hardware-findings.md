# FLIR One on macOS: hardware findings

Measured on macOS 26.6.2, Apple Silicon, libusb 1.0, against a FLIR One with a
Lightning connector via a USB-C adapter.

## 1. The Lightning unit enumerates as a normal USB device

`idVendor 0x09CB`, `idProduct 0x1996`, "FLIR ONE Camera", manufacturer "FLIR
Systems", `bcdDevice 0x0108`. Identical identity to the Android/USB-C variant.
It has a **single** configuration, numbered 3, so no configuration switch is
needed despite what the reference driver implies.

```
config 3
  iface 0 alt 0   ep 0x81 BULK in    ep 0x02 BULK out
  iface 1 alt 0   (no endpoints)
  iface 1 alt 1   ep 0x83 BULK in    ep 0x04 BULK out
  iface 2 alt 0   (no endpoints)
  iface 2 alt 1   ep 0x85 BULK in    ep 0x06 BULK out
```

All endpoints are bulk, 512-byte packets. No isochronous bandwidth to reserve.

## 2. libusb issue #729 is a host-side ordering bug, and it is fixable

[libusb#729](https://github.com/libusb/libusb/issues/729) reports that on macOS
every bulk read of endpoint `0x85` fails, and was closed unresolved. The cause is
visible in the descriptor table above: **`0x85` only exists in alternate setting
1**. Alt 0 has no endpoints at all.

The reference Linux driver starts the stream with a raw control transfer
`bmRequestType=0x01, bRequest=0x0B`. That is the standard SET_INTERFACE request.
Linux passes it to the device, and because Linux resolves endpoints from the
descriptor rather than from kernel pipe objects, reads then work. macOS/IOKit
tracks alternate settings itself and builds its pipe objects on
`SetAlternateInterface`, so the raw transfer changes the *device* while leaving
the *host* on alt 0. The endpoint being read from does not exist host-side, and
every transfer times out with `0xE0004051` (`kIOUSBTransactionTimeout`).

The fix is to use `libusb_set_interface_alt_setting()` instead of the raw
control transfer. Two further details matter:

- **Claim all three interfaces.** With only interface 2 claimed,
  `SetAlternateInterface` fails with `kIOReturnNotResponding`.
- **Drive both interfaces to alt 0 first.** Going straight to alt 1 also fails
  with `kIOReturnNotResponding`. Syncing IOKit's view to idle first succeeds.

Working sequence, confirmed:

```
claim 0, 1, 2
set_alt(2, 0)  ->  set_alt(1, 0)  ->  set_alt(1, 1)  ->  set_alt(2, 1)
```

libusb then reports the pipe correctly:

```
get_endpoints: interface: 2 pipe 1: dir: 1 number: 5
ep_to_pipeRef: pipe 1 on interface 2 matches
```

This is implemented in `src/flirone/usb_link.py`. Anyone hitting #729 on macOS
should start there.

## 3. This Lightning unit does not stream

With the host side fully correct, the camera still sends zero bytes on `0x85`.
What it does instead, reproducibly:

- Emits exactly one message on `0x81`, repeatedly and invariantly:
  `ff 55 02 00 ee 10`. Fourteen captures, no variation.
- Never answers a `cc`-framed JSON command on `0x02` (`openFile`, `readFile`),
  which the Android variant does answer.
- Detaches from the bus **4.69 s** after enumeration, every time, then
  re-enumerates about 0.9 s later. The period is identical whether or not any
  software touches the device, so it is not provoked by our init.

Note the framing mismatch: the documented command channel uses `cc`-framed JSON,
this unit speaks a `ff 55` framing. That is consistent with a device sitting in a
minimal pre-handshake state rather than a running camera.

## 4. Confirmed: the camera is running Apple's accessory handshake

The charge test settled the power question first. After charging, the camera
still died at 4.58-4.70s across six attempts, statistically identical to the
pre-charge runs. Power is not the variable.

The 6-byte message then decoded. `FF 55` is the iAP1 start-of-packet, and the
iAP1 checksum rule validates exactly:

```
ff 55 | 02 | 00 ee | 10
SOP     len  payload  checksum
(0x02 + 0x00 + 0xEE + 0x10) mod 256 = 0     valid iAP1 frame
```

Lingo `0x00` is General Lingo. Command `0xEE` was not identified from available
sources and is not claimed here.

Sending bytes back proved the channel is interactive. Echoing the camera's own
packet to endpoint 0x02 made it reply with a completely different, 26-byte
message:

```
ff 5a 00 1a 80 74 00 00 99 01 7f ff ff 07 d0 00 32 1e 01 01 00 01 02 02 01 53
```

`FF 5A` is the **iAP2** start-of-packet. The length field `0x001A` is 26, which
matches the message exactly, and the control byte `0x80` has the SYN bit set:
a link-synchronization packet. The camera negotiated itself up from iAP1 to
iAP2 in response to a single write.

Caveat: neither checksum in that packet is reproduced by the two's-complement
rule over the byte ranges tried, so the field layout above is inferred from the
SOP, the self-consistent length and the SYN bit, not fully validated.

Sending a valid-looking iAP1 ACK or RequestIdentify produced no reply and no
change in the watchdog. Only the echo triggered the iAP2 response.

### What this means

The camera will not enable its vendor interfaces until a host completes Apple's
accessory handshake. A Mac acting as a plain USB host never starts one, so the
camera waits and reboots. The USB-level work in sections 1-3 is correct and
complete; the blocker sits one layer up.

### Why a host-side implementation is not obviously hopeless

In MFi the **accessory** carries the authentication coprocessor and proves
itself to the Apple **device**. Implementing the device side means issuing the
challenge and accepting the response, not producing Apple-signed material. So
this does not require an auth chip or forged credentials, unlike the reverse
direction. Whether FLIR gates the video stream behind a successful
authentication that a non-Apple host cannot conclude is untested.

Rough shape of the work: iAP2 link layer (SYN/ACK, sequence numbers,
retransmission), control session and identification, the authentication
exchange, then the vendor session that starts video. Days of work, uncertain
outcome.

### Practical alternatives

- An Android/USB-C FLIR One speaks the documented vendor protocol with no iAP
  layer. `src/flirone/usb_link.py` already implements it.
- Keep an iOS device as the host for this camera.

## 5. The iAP2 handshake works, and the camera authenticates

Implemented in `src/flirone/iap2/`. The full exchange completes:

```
RX  iAP1 hello        ff 55 02 00 ee 10
TX  (echo)                                  -> camera switches to iAP2
RX  Packet(SYN     seq=0x74 ack=0x00 session=0 payload=16B)
TX  Packet(SYN|ACK seq=0x00 ack=0x74 session=0 payload=16B)
RX  Packet(ACK     seq=0x74 ack=0x00 session=0)          link established
TX  StartIdentification
RX  IdentificationInformation (18 params)
TX  IdentificationAccepted
RX  RequestAppLaunch  "com.flir.one"
TX  RequestAuthenticationCertificate
RX  AuthenticationCertificate            908 bytes, PKCS#7 signedData
TX  RequestAuthenticationChallengeResponse   20-byte challenge
RX  AuthenticationResponse               128 bytes, RSA-1024 signature
TX  AuthenticationSucceeded
```

The camera reports itself as **FLIR ONE Camera**, model 435-0003-01-00, serial
FLIRONEF02F9T00570A, firmware 0.4.27, bundle com.flir.one, and advertises three
External Accessory protocols:

| id | protocol | purpose |
|----|----------|---------|
| 0 | com.flir.rosebud.config | options and configuration |
| 1 | com.flir.rosebud.fileio | file access, calibration |
| 2 | com.flir.rosebud.frame | video |

Once the handshake completes the watchdog stops: the camera stays up
indefinitely rather than resetting at 4.69s. Idling with cumulative acks held
the link for the full 45s test with no disconnect.

Video does **not** come over the vendor bulk endpoint. With the link up and
authenticated, endpoint 0x85 stays silent; the frames belong to EA protocol 2.

### FLIR's own command framing, fully recovered

Commands are a 16-byte header plus NUL-terminated JSON:

```
cc 01 00 00 | 01 00 00 00 | <len(body), u32 LE> | <crc32(header[:12]), u32 LE>
```

The trailing checksum covers the **header**, not the body. Both hardcoded
headers in the reference driver regenerate byte-for-byte from
`src/flirone/rosebud.py`, so arbitrary commands can now be built.

## 6. Open blocker: the camera resets whenever it starts real work

With authentication complete, behaviour splits cleanly:

| action | result |
|--------|--------|
| idle, acks only | alive for 45s+ |
| open EA session, protocol 0 (config) | alive for 25s+ |
| open EA session, protocol 1 (fileio) | USB drop within 0.1s |
| open EA session, protocol 2 (frame) | USB drop within 0.1s |
| send `openFile` on the config session | USB drop immediately |

Anything that would spin up the imaging or storage subsystem drops the device
instantly. Anything that leaves it idle is stable. That is the signature of a
brownout rather than a protocol fault: a protocol error would be expected to
produce an error message on a link that is demonstrably healthy, not a
disappearance from the bus.

The unit's battery is known to be worn, and FLIR documents low-battery reboot
behaviour on this hardware. This needs retesting on a fully charged battery
before any protocol-level explanation is worth pursuing.

## 7. The camera declares exactly the messages we send

Parameters 0x0006 and 0x0007 of IdentificationInformation are the accessory's
own capability lists, and they close off a whole family of theories:

```
0x0006  messages SENT by accessory:      0xEA02  RequestAppLaunch
0x0007  messages RECEIVED by accessory:  0xEA00  StartExternalAccessoryProtocolSession
                                         0xEA01  StopExternalAccessoryProtocolSession
```

The camera accepts exactly two control messages and we send both. There is no
power-negotiation message, no start-stream message, and nothing else it will
listen to that we are omitting. It also confirms the message identifiers used in
`control.py` were guessed correctly.

## 8. Survival is flat across trials, not degrading

Five consecutive trials, each a fresh handshake followed by a request for the
frame session:

```
survived 0.00s, 0.11s, 0.11s, 0.00s, 0.00s
reappeared after 0.8s, 0.9s, 0.9s, 0.9s, 0.9s
```

No degradation across trials, and a recovery interval identical to the original
watchdog cycle. That rules out progressive battery drain: repeated attempts to
spin the sensor up do not wear it down.

It does **not** decide between the two remaining explanations, which produce
identical host-side symptoms:

- a deterministic protocol refusal, or
- a deterministic under-voltage protection check, which would refuse
  identically every time rather than collapsing variably.

An earlier note in section 6 called this a brownout signature. That reading was
too strong and this data weakens it.

### The decisive test is off-host

Connect the camera to a Lightning iPhone and run the official FLIR One app.

- It streams there  ->  the hardware is healthy and our handshake is missing
  something the app does. Worth continuing.
- It also fails or reboots there  ->  the fault is in the camera or its battery,
  and no amount of protocol work on this side will fix it.

Nothing measurable from the Mac separates the two, so this test should come
before any further protocol work.

## 9. Hardware confirmed healthy; the blocker is our iAP2 implementation

The camera streams normally from the official iOS app through the **same
USB-C-to-Lightning adapter**. That eliminates every physical explanation:
battery, adapter, cable, and the camera itself are all fine. Sections 6 and 8
speculated about power; that speculation is now dead and the fault is on our
side.

### Eliminated by experiment

| hypothesis | test | result |
|---|---|---|
| battery / brownout | official app on iPhone, same adapter | streams fine |
| progressive battery drain | 5 consecutive trials | survival flat, no decay |
| missing control message | accessory's own capability list (param 0x0007) | it accepts only EA00/EA01, both of which we send |
| deliberate transport switch | descriptor diff across the reset | byte-identical |
| EA session ordering | frame-only, config-then-frame, config+fileio+frame | all identical |
| EA session identifier | ids 1, 2, 3, 256 | all identical |
| vendor pipes not open | claim 0/1/2 and select alt 1 before handshaking | still resets |
| host read buffer too small | raised 4KB -> 64KB -> 1MB | still resets |
| link parameters oversized | swept 127x65535 down to 1x512 | all identical |
| handshake ordering | auth-then-identify, identify-then-auth, either alone | all identical |
| our acknowledgement policy | duplicate seq, fresh seq, no acks at all | all identical |

### What remains

The camera resets roughly 0.11s after receiving StartExternalAccessoryProtocol-
Session for protocol 1 (fileio) or 2 (frame), without acknowledging it. Protocol
0 (config) is accepted and stable. Nothing we do afterwards changes the outcome,
including doing nothing at all.

Note what this implies: on iOS the app cannot be doing anything special at this
layer, because ExternalAccessory and accessoryd own the iAP2 protocol and the
app only opens an EASession. So the difference lies somewhere in iOS's iAP2
implementation that we have not reproduced, in a part of the specification that
is under NDA and was not available here.

### The only reliable way forward

Capture the real exchange between the iPhone and the camera with an inline USB
2.0 analyser (Cynthion, or a Total Phase Beagle) and diff it against our trace.
Everything else at this point is guessing at an unpublished specification.

## 10. Higher-resolution capture corrects sections 6-9

A fresh round of fine-grained probes (camera attached, control interface only)
measured the failure far more precisely than the earlier polling loops, and
some of what sections 6-9 concluded was an artefact of coarse timing.

### The failure is a millisecond-scale pipe error, not a 0.1s reboot

Opening the frame or fileio EA session errors endpoint 0x81 (the iAP2 link IN)
within ~2 ms of the write, with libusb errno 5 (EIO / pipe error), and zero
inbound bytes first. The camera does not send an iAP2 RST or any final message;
the pipe simply dies. The "~0.1s" in section 6 was the granularity of the old
read/pump loops, not the real latency. `clear_halt` on 0x81 does not recover it.

Whether the device then leaves the USB bus depends on host state: with only the
control interface claimed it often stays enumerated with a dead pipe; with the
vendor interfaces (1 and 2) also claimed and being read, it fully re-enumerates.
Either way the symptom is an immediate EIO on 0x81.

### config is acknowledged; frame and fileio are not

Side by side, correctly routed on the control session:

```
StartEASession(config, id 1)  ->  camera link-ACKs at +1.8ms, bus stable
StartEASession(frame,  id 1)  ->  EIO on 0x81 at +2.0ms, no ACK, pipe dead
```

The only difference between the two writes is the protocol-id byte (0 vs 2). The
camera link-acknowledges the config session and refuses the frame/fileio one at
the USB level rather than at the iAP2 level.

### Frame data never appears on the vendor endpoints

With interfaces 1 and 2 claimed and alt-1 selected, and background readers on
both vendor bulk INs (0x83, 0x85) running before and during the frame session
open, **nothing ever arrives on 0x83 or 0x85**. The "frames come over the vendor
endpoint once the EA session opens" hypothesis is not supported.

### iAP2 auth followed by the vendor init does NOT reset

Authenticating over iAP2 and then running the Android vendor start sequence,
skipping EA sessions entirely (interface alt-settings selected via the
IOKit-safe API, the 0xCC-framed openFile/readFile JSON written to EP 0x02), runs
clean: every step stays alive and on the bus. But no frames flow, most likely
because after the iAP2 handshake the camera is in iAP2 mode on EP 0x02 and
ignores raw vendor bytes there.

### Where this leaves it

The reset is provoked specifically by `StartEASession` for a vendor-backed
protocol (1 or 2), and only that. Avoiding it keeps the camera fully alive, but
no path found so far makes frames flow: the vendor command channel (raw 0x02) is
inert under iAP2, and the EA-session channel resets the link. The open question
is narrower now: what does a real device send, in what USB mode, to make the
camera stream after authentication. An analyser capture of the iOS app remains
the way to answer it; these probes have cornered the question, not closed it.

## 11. The EA descriptors refute the native-transport theory for frame

A multi-agent analysis proposed that fileio and frame reset because opening them
commits the camera to bring up an iAP2 "native transport" on the vendor bulk
interfaces, which a real iPhone services and a Mac does not. Persisting and
decoding the full 18-parameter IdentificationInformation (probe saved to
/tmp/flir_ident.bin) tests that against the bytes, and it does not hold.

The three External-Accessory protocol descriptors (param 0x000A), decoded as
nested TLVs (u16 length incl. its 4-byte header, u16 id, data):

```
config (id 0):  {0x0000=00, 0x0001="com.flir.rosebud.config", 0x0002=01}
frame  (id 2):  {0x0000=02, 0x0001="com.flir.rosebud.frame",  0x0002=01}
fileio (id 1):  {0x0000=01, 0x0001="com.flir.rosebud.fileio", 0x0002=01, 0x0003=00 00}
```

0x0003 is the NativeTransportComponentIdentifier. Only **fileio** carries one,
and it resolves to the host transport component (param 0x0010 "Rosebud USB Host
Transport", component id 0x0000). **frame carries no native-transport binding at
all, and its descriptor is structurally identical to config's** apart from the
protocol id and name. Yet frame resets and config is stable.

So the reset is not native-transport bring-up on the frame path: there is no
native transport bound to frame. The two transport components the camera
advertises:

```
param 0x000F "Rosebud USB Device Transport"  component id 0x0003
param 0x0010 "Rosebud USB Host Transport"    component id 0x0000
```

### What actually distinguishes reset from stable

Nothing in the iAP2 descriptors does. config and frame are the same shape; the
only differences are the protocol-id byte and the name. The split is therefore
in the camera's application firmware: opening protocol 1 (file access) or 2
(imaging) makes it start a heavier subsystem, and that bring-up aborts on a Mac
host in a way protocol 0 (settings) never triggers. The camera emits no iAP2
RST, no error message, and nothing on the vendor endpoints; it just stalls the
control pipe and aborts within ~2 ms.

### Net effect on the theories

- Missing control message: refuted earlier (capability list complete).
- Native transport on frame: refuted here (no binding; frame == config shape).
- Frames on the vendor endpoint: refuted (zero bytes on 0x83/0x85 ever).
- Host-supplies-the-second-link: no link to answer (nothing emitted).

What remains is a camera-side subsystem abort triggered by the protocol id,
whose cause is not expressed anywhere the host can read. Only an inline USB 2.0
analyser capture of the working iPhone session can show what the phone does at
StartEASession time that a Mac does not. The multi-agent pass sharpened the
question and killed four hypotheses; it did not find a host-side unblock, and
the byte evidence says one is unlikely to exist without that capture.
