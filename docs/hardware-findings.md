# FLIR One on macOS: hardware findings

Measured on macOS 26.6.2, Apple Silicon, libusb 1.0, against a FLIR One with a
Lightning connector via a USB-C adapter. Sections 12 and 13 are Linux and say
so; section 14 onwards is an analyser capture of an iPhone driving the same
camera. Where a later section overturns an earlier one, the earlier one is
marked rather than deleted.

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

## 3. This Lightning unit does not stream from the vendor protocol

**Section 14 settles this: the unit streams, and does so over `0x85` exactly as
the vendor path expected. What was missing was the accessory handshake in front
of it, not anything in the transport.** The observations below stand as a
description of the camera before that handshake; the heading originally read
"does not stream", which was true of what had been tried and not of the camera.

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

**This conclusion is wrong. Section 14 overturns it with an analyser capture of
a working session, where video arrives on 0x85 exactly as this hypothesis
predicted.** The observation below stands; the inference drawn from it does not.

With interfaces 1 and 2 claimed and alt-1 selected, and background readers on
both vendor bulk INs (0x83, 0x85) running before and during the frame session
open, nothing ever arrives on 0x83 or 0x85. What that actually showed is that
opening the frame EA session never reaches a streaming state, not that the
endpoints are unused: a real host never opens that session at all.

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

## 12. Linux: the camera fails Linux's default enumeration scheme

Measured on Ubuntu 22.04.5, kernel 5.15.0-181, libusb 1.0, same Lightning unit
and adapter, serial `FLIRONEF02F9T00570A`.

The camera never enumerates under Linux's default settings. It re-attaches about
once a second indefinitely and `lsusb` never shows it. The kernel logs only
`new high-speed USB device number N`, with no error, because a device that never
finishes enumerating produces no disconnect message either. 537 attempts were
recorded over ten minutes.

usbmon shows the device is healthy and the failure is host-side:

```
S Ci:1:000:0 s 80 06 0100 0000 0040 64 <
C Ci:1:000:0 0 18 = 12010002 00000040 cb099619 08010102 0301
S Co:1:001:0 s 23 03 0004 0005 0000 0
S Ci:1:001:0 s a3 00 0000 0005 0004 4
C Ci:1:001:0 0 4 = 00011100
```

The device descriptor comes back complete and correct on every attempt, status
0, identical to section 1. The hub then issues `SET_FEATURE(PORT_RESET)`, and
the `GET_STATUS` that follows returns `wPortStatus 0x0100` with `wPortChange
0x0011`: PORT_POWER alone, PORT_CONNECTION clear, C_PORT_CONNECTION and
C_PORT_RESET set. The camera has left the bus. `SET_ADDRESS` is never attempted.

Linux's default "new scheme" resets the port, reads the device descriptor,
resets a second time, then assigns the address. This camera does not survive the
second reset. The old scheme assigns the address first and resets once:

```bash
echo 1 | sudo tee /sys/module/usbcore/parameters/old_scheme_first
```

That fixes it, with no replug. The camera then enumerates completely, strings
and all, and behaves as section 5 describes: it resets after 4 to 5 seconds when
nothing talks to it, consistent with the 4.69s watchdog measured on macOS.

Power is not the cause. `PORT_OVER_CURRENT` is never set, the descriptor is
answered cleanly on every attempt, and a host-side software flag fixes it
outright.

This is a host-side enumeration bug. It does not bear on the streaming blocker
in sections 10 and 11.

## 13. Linux replay: the fault is camera-side, not macOS

Measured on Ubuntu 22.04.5, kernel 5.15.0-181, libusb over usbfs, same unit and
adapter, camera on external power. Enumeration needed section 12's flag.

### The EA session split reproduces exactly

`probe_ea_matrix.py`, unmodified, on Linux:

```
A frame only, id 1       : frame(ea=1):DIED
B config then frame      : config(ea=1):ok | frame(ea=2):DIED
C frame, high session id : frame(ea=256):DIED
D config, fileio, frame  : config(ea=1):ok | fileio(ea=2):DIED | frame:SKIPPED
E config then fileio     : config(ea=1):ok | fileio(ea=2):DIED
```

Identical to macOS: protocol 0 is accepted, 1 and 2 kill the link. A different
host stack, usbfs and libusb rather than IOKit, produces the same split. The
macOS USB stack is therefore not the cause, and every host-side theory that
depended on it is dead.

The failure surfaced through libusb as `ENODEV`, and at URB level as `EPROTO`
or `ESHUTDOWN`. usbmon shows why, below.

### Wire level: the camera leaves the bus 2ms after accepting the write

usbmon on bus 1 across all five trials. The StartEASession write always
completes with status 0, so the camera accepts it at USB level:

```
S Bo:1:048:2 -115 27 = ff5a001b 40065601 ef404000 11ea0000 05000002 00060001 000176
C Bo:1:048:2 0 27 >
```

Then the iAP2 IN pipe dies, with nothing received in between:

| trial | session opened | failure | latency | bytes in first |
|---|---|---|---|---|
| A | frame, id 1 | EPROTO | 2.564 ms | 0 |
| B | config then frame, id 2 | ESHUTDOWN | 1.932 ms | 0 |
| C | frame, id 256 | EPROTO | 2.374 ms | 0 |
| D | config then fileio, id 2 | ESHUTDOWN | 2.003 ms | 0 |

1.9 to 2.6 ms, reproducing the ~2ms measured on macOS on an unrelated host
stack. A config session, by contrast, is answered: 9 bytes arrive on 0x81 and
the link continues.

Decoded, the fatal and surviving writes differ by one byte:

```
config  ea00 0005 0000 00  0006 0001 0001   ACKed, 9 bytes back
frame   ea00 0005 0000 02  0006 0001 0001   dead in 2.56 ms, nothing back
fileio  ea00 0005 0000 01  0006 0001 0002   dead in 2.00 ms, nothing back
                       ^^ protocol id
```

The device is not stalling. 0.8 ms after the EPROTO the hub reports the port
empty:

```
GET_STATUS port 5 -> 00010100
   wPortStatus 0x0100  PORT_POWER only, PORT_CONNECTION clear
   wPortChange 0x0001  C_PORT_CONNECTION
```

So the camera accepts a write it never answers and drops off the bus 2ms later,
emitting no iAP2 response, no RST and no stall handshake. Section 10 described
this as stalling the control pipe; on Linux it is an outright disconnect, and it
happens with only interface 0 claimed, which section 10 expected to leave the
device enumerated. A firmware fault in the subsystem being started fits this
better than any protocol-level refusal, which would have a way to say so.

### A bare link does not unlock the vendor channel either

Section 10 attributed the inert vendor channel to the camera switching into
iAP2 mode at the handshake. Tested directly by `probe_link_then_vendor.py`:
answer the SYN to stop the 4.69s watchdog, send no identification and no
authentication, then run the reference driver's start sequence (alt 2->0, 1->0,
1->1, the 0xCC-framed openFile and readFile, alt 2->1).

Every step survives. Nothing ever arrives.

| command endpoint | link | EP 0x83 | EP 0x85 |
|---|---|---|---|
| 0x04, fileio OUT | alive throughout | 0 bytes | 0 bytes |
| 0x02, control OUT | alive throughout | 0 bytes | 0 bytes |

Authentication is not what makes the vendor channel inert. A link carrying
nothing but sync and acks does it too. That refutes the explanation offered in
section 10.

### What this leaves

Taken with section 11, where fileio is the only protocol carrying a
NativeTransportComponentIdentifier and it resolves to "Rosebud USB Host
Transport", the consistent reading is that the vendor bulk endpoints are the
iAP2 native transport rather than an independent Android-style channel. They
carry nothing until the matching EA session opens, and opening fileio or frame
is what kills the link. Inert without a session, fatal with one.

This narrows the blocker rather than opening it, and settles where it lives.
An analyser capture of a working iOS session is still the only route to what
the phone does differently at StartEASession time.

## 14. The iPhone capture: we were opening a session no real host opens

An inline Cynthion capture of the official iOS app streaming from this camera,
against the same unit and adapter, answers the blocker. Two files, working and
failing, in `research/Cynthion-captures/` (untracked, large).

Three things differ from every probe written before it.

### The phone authenticates first and identifies second

| phone | every probe up to now |
|---|---|
| RequestAuthenticationCertificate | StartIdentification |
| RequestAuthenticationChallengeResponse | IdentificationAccepted |
| AuthenticationSucceeded | RequestAuthenticationCertificate |
| StartIdentification | RequestAuthenticationChallengeResponse |
| IdentificationAccepted | AuthenticationSucceeded |

### It opens one EA session, for config, and never for fileio or frame

Its only `StartEASession` is byte-identical to the config one we already send:

```
phone : 40 40 00 11 ea 00 00 05 00 00 00 00 06 00 01 00 01
ours  : 40 40 00 11 ea 00 00 05 00 00 02 00 06 00 01 00 01   (frame)
                                    ^^ protocol id
```

Sections 6 through 13 were spent characterising the camera's reaction to a
session no host ever opens. The reaction is real and reproducible, and it is
also irrelevant: opening protocol 1 or 2 is simply not part of the protocol.

### Streaming is a command, not a session

Over the config session, framed exactly as `rosebud.py` already builds them:

```
-> {"type":"openFile","data":{"mode":"r","path":"CameraFiles.zip"}}
<- {"type":"openFileStatus","data":{"path":"CameraFiles.zip","streamIdentifier":10,"mode":"r"}}
-> {"type":"readFile","data":{"streamIdentifier":10}}
-> {"type":"setOption","data":{"option":"stream","value":true}}
<- {"type":"setOptionStatus","data":{"option":"stream","value":-1}}
```

`setOption stream=true` is the trigger, and video then arrives as raw bulk on
0x85: 10036 packets, 5.08 MB in the 10s capture.

### Full order, from the capture

```
 106 ms  RequestAuthenticationCertificate
 242 ms  RequestAuthenticationChallengeResponse
2353 ms  AuthenticationSucceeded
2353 ms  StartIdentification
2512 ms  IdentificationAccepted
2529 ms  StartEASession(protocol 0, session 1)
2536 ms  SET_INTERFACE iface 1 -> alt 1
2907 ms  openFile CameraFiles.zip
2912 ms  openFileStatus -> streamIdentifier 10
2916 ms  readFile
3006 ms  SET_INTERFACE iface 2 -> alt 1
3011 ms  setOption stream=true
4161 ms  first video on 0x85
```

Note the alternate settings are selected between the commands, not up front, and
that the first frame arrives about 1.1s after the stream is requested.

`research/iap2-probes/probe_phone_order.py` replays this. Its command bytes
regenerate the captured ones exactly.

### Confirmed against the camera

The replay streams. Reproducible across five runs, 46 to 112 frames each at
about 7.5 fps, no desyncs, and the same frames arrive through the application.
`src/flirone/iap2/stream.py` is the sequence as shipped.

Two details only showed up in the replay, not in the capture:

- The drain matters. `setOption` drew no reply at all while `CameraFiles.zip`
  sat unread on `0x83`; draining it first is what makes the camera answer. The
  capture shows the phone doing this, but it reads as bookkeeping rather than a
  precondition.
- Completing the handshake stops the watchdog permanently, so a camera that has
  streamed never re-announces itself. `close()` resets the device for that
  reason, and a camera is judged usable by whether it can be claimed, since one
  held by another process is indistinguishable from a free one on the bus.

The camera also volunteers `sledInformation` as soon as interface 1 goes to
alternate setting 1. Recovered whole from the capture, split across two bulk
transfers:

```json
{"type":"sledInformation","data":{
  "serialNumberBoard":"F02F9T00570","partNumberBoard":"21200436301",
  "versionBoard":"D","serialNumberLepton":"421965","versionLepton":"3.1.46",
  "leptonQR":"A0421965","versionRosebudFactoryESW":"0.4.27",
  "versionRosebudOperationalESW":"0.4.27","versionRosebudUpdaterESW":"0.4.27",
  "versionRosebudAPI":"master.015ede4","gitRevision":"master.015ede4",
  "automaticShutter":"Y","formFactor":"dongle",
  "thermalHeight":"120","thermalWidth":"160","bigEndianThermal":"0",
  "operatingMode":"operational"}}
```

Three things settle here that were previously inferred:

- `bigEndianThermal` is `0`, stating the byte order that decision 002 deduced
  from physical plausibility. The deduction was right, and is now checkable.
- The thermal array is 160x120 by the camera's own account, which places the
  sensor in the Lepton 3 family; Lepton 2 and 2.5 are 80x60. The EXIF model is
  `FLIR ONE (gen 2)`, and a teardown of that product identifies the core as a
  Lepton 3. Lepton 3.5, the radiometric variant of the same format, postdates
  this product. Section 15 rejects TLinear output on measurement alone, which
  agrees.
- `automaticShutter` is `Y`, matching the integral-shutter configuration of the
  datasheet, and the shutter is what section 15 warns against calibrating
  against.

`versionLepton` is the sensor's firmware revision, not its model number. Do not
read a part number out of it.

## 15. Live counts are a factor of four below the reference plane

Streaming works and the temperatures computed from it were wrong. The stream's
counts sit far below what the Planck model expects, using the constants of the
very camera that produced them:

```
JPEG raw plane : 12301-23220   ->  22.2 to 73.4 C   correct
live stream    :  3227- 3354   ->        -72.8 C    impossible
22 C would need raw 12269
```

That is not a provenance problem and not a per-unit difference. It is a scale,
and no choice of Planck constants reconciles the two.

### The shutter is not a usable reference

An earlier version of this section fitted the scale from one point: during a
flat-field correction the sensor images its own shutter, whose temperature the
camera reports.

```
mean 4423.8  std 10.2   shutterTemperature 314.12 K, so 40.97 C
```

Measured against two physical fixed points, those counts are **48.8 C** where
the camera reports 40.97, an error of 7.8 C. `shutterTemperature` is evidently a
thermistor on the shutter mechanism rather than the temperature of the blade
surface the detector images, and the blade sits directly in front of a
self-heating sensor. Do not calibrate against it.

The 7.8 C is a property of the shutter, not of the scale: the fitted scale and
the bare factor of four below put it at 48.90 and 48.78 C respectively. The
mistake in fitting from it was that one point cannot separate a gain from an
offset, so it constrained nothing. Its answer, no offset, happens to be right,
which it had no way of knowing.

### Two fixed points, defined by physics

Neither needs a calibrated instrument, which is the point:

```
ice-water slurry, stirred    0.00 C     2246.3 counts   drift 1.8
boiling water at 158 m       99.48 C    7745.8 counts   drift 52.7
```

The boiling anchor is the noisier of the two, from steam and surface turbulence,
and it carries most of the remaining uncertainty. The ice point is solid.

### Checked against FLIR's own conversion

Two further targets were read with the official iOS app on the same camera at
emissivity 0.95, which makes them a comparison against the reference
implementation rather than against a contact thermometer:

```
white painted wall   26.0 C   3167.5 counts
back of a hand       35.0 C   3637.0 counts
```

These two cannot anchor a fit between them: they are 9 C apart, and a lever arm
that short turns small errors into large extrapolations. Used as anchors they
imply a gain of 397706 and mispredict the fixed points by +5.5 and -5.1 C. They
are useful as checks, not as calibration.

### The result: a factor of four, and an offset that was not there

Fitting gain and offset on all four points, with the radiance correction applied
and each target's own emissivity:

```
gain 372748    offset -383.4    rms 0.88 C, worst 1.23 C
```

That is the form this codebase parameterises in, radiance units. Written the way
everyone else writes it, as a map onto the reference plane the Planck constants
assume:

```
reference_raw = 3.9576 x counts + 213.7
```

A factor of four, to one percent, with a small residual offset. The four is not
a coincidence: the Lepton delivers 14 bits and the reference plane is 16, so a
two-bit shift is exactly what a bit-depth conversion looks like.

**The residual offset does not survive scrutiny, and neither does the one
percent.** Forcing the offset to zero, each fixed point on its own demands

```
ice water    reference_raw = 3.9743 x counts
boiling      reference_raw = 3.9800 x counts
```

which agree with each other to 0.14% and with four to 0.6%. And the remaining
0.6% is degenerate with the emissivity assumed for water: at 0.9643 rather than
0.96 the fitted factor is exactly 4.0000. Water in LWIR runs 0.96 to 0.98
depending on viewing angle, so the data cannot tell 3.96 from 4.00.

Against a bare factor of four, with no fitted parameter at all:

```
                     fitted        x4
  ice water    0 C    +1.23      +0.35
  boiling   99.5 C    +0.34      +0.72
  wall      26.0 C    -1.22      -1.67
  hand      35.0 C    -0.12      -0.43
  rms                  0.88       0.95
```

Two free parameters bought 0.07 C of rms. On the two points defined by physics,
the only two here that owe nothing to another instrument, the bare four is the
better of the two. **The honest reading is that the scale is exactly four and
the fit is a one percent noisy estimate of it.**

The fit and the runtime conversion still have to make the same assumptions.
Constants fitted from uncorrected counts and then used with the emissivity
correction applied double-count it, which moves the fixed points to -1.1 and
102.2 C.

### How this section got the offset wrong

Worth recording, because the error was in the comparison rather than in the
measurement. `CountScale.to_reference` maps counts through radiance and
subtracts `O` at the end, so `offset=0` does not mean `reference_raw =
4 x counts`. It means `4 x counts - O`, and `-O` is 1731 counts on this camera.
Encoding a bare four needs `offset = O/4` as well as `gain = R1/R2/4`.

Comparing against `offset=0` therefore compared against a model 1731 counts
adrift, which reads 12 C hot at the cold end, and made a correct published
result look like a crude approximation. `tests/test_calibration.py` now pins
both the correct encoding and the trap.

The parameterisation was the root cause, so it changed. `CountScale` now holds
a factor and an intercept on the reference plane, `reference_raw = counts *
factor + offset`, which is where the relationship lives and how every other
implementation states it. A bare four is `4.0, 0.0` and reads as such. Scales
stored under the old form are converted on read, using the Planck constants in
the same record.

The application uses `4.0, 0.0` for every FLIR One live stream. A bit depth is
a property of the product rather than of a serial number, so there is nothing to
calibrate and nothing per-camera to store; the store holds Planck constants and
only those. `probe_fixed_points.py` remains as the way to test that constant
against a camera, which is how a unit that disagrees with it would be found.

### The Android work had it right in 2016

`nebarnix/flirone-v4l2_filtered`, a fork of the driver this project already
credits, converts live counts with Planck constants read from a JPEG and a shift
applied first:

```c
double raw2temperature(unsigned short RAW)
{
 // mystery correction factor
 //RAW *=4;
 RAW = RAW << 2;
```

tomas123 established the same factor on the eevblog FLIR One for Android thread
in January 2016, and tested it properly. He took a Lepton sample frame from the
Android SDK's own `sampleframes.zip`, multiplied by four, and compared its
statistics against the `RawThermalImage` the SDK app wrote for that same frame:

```
                        min      max      mean     sd
  Lepton raw x4       14220    20840    15014.9   1218.95
  SDK app JPEG raw    14266    20831    15021.4   1221.27
```

6.5 counts of difference on the mean, which he puts at 0.031 K. That is a far
tighter check on the factor than four hand-held targets, and it is independent
of any calibration, since both sides describe the same frame.

Two caveats keep our measurement worth having. His check ran through the SDK
simulator, so it never passed through a dongle, and it could not have revealed
an offset introduced by real hardware. And it was done on the Android USB-C
variant. The measurement here is on a Lightning unit against physical fixed
points, and it agrees.

### Ruled out: the counts are not Lepton TLinear output

The FLIR One Gen 2 carries a 160x120 Lepton, and the Lepton family can be told
to emit temperature directly, in hundredths of a kelvin, rather than something
proportional to flux. If this stream were that, the Planck path above would be
an elaborate way of fitting a straight line. It is not, on three grounds.

**The magnitudes are wrong.** TLinear puts 0 C at a fixed count:

```
0.01 K per count   0 C -> 27315 counts      observed 2246
0.04 K per count   0 C ->  6829 counts      observed 2246
```

**The counts curve.** Fit the best straight line in temperature through all four
measured points, two free parameters, one more point than the Planck model was
given:

```
              linear in T        Planck, fitted on 2 of the 4
  0.0 C         +5.96                    +1.23
 26.0 C         -3.93                    -1.22
 35.0 C         -4.71                    -0.12
 99.5 C         +2.69                    +0.34
  rms            4.48                     0.85
```

The signs are the giveaway: high at both ends, low in the middle, which is a
line chasing a curve. There is real curvature to find, because radiance at the
midpoint of this range is 0.876 of the average of the endpoints where a straight
line would give 1.000.

**The datasheet does not describe it.** Lepton 3 v3.0 documents two radiometry
modes, both defined as a transfer function between incident flux and pixel
output; TLinear belongs to the radiometric variants, not this part. Nor is the
stream raw Lepton output in radiometry-disabled mode, which anchors a scene at
the camera's own temperature near count 8192: here a 26 C wall reads 3167 with
the camera reporting 36.7 C, and 99.5 C water still reads below 8192. The FLIR
One is applying its own scale, which is the one measured above.

One weak supporting observation: the four points span 309.3 to 311.7 K of
reported camera temperature, and a single gain and offset covers them to 1.2 C.
That is what radiometry-enabled mode promises, output independent of the
camera's own temperature. It is 2.4 K of body swing, so it is consistent with
that promise rather than evidence for it.

FLIR's own naming agrees. The image type tomas123 exports from the Android app
in the eevblog thread on full-resolution FLIR One data is
`ThermalLinearFlux14BitImage`: linear in flux, not in temperature.

### What is not established

- **Whether the factor is exactly four.** Everything points that way: the bit
  depths, tomas123's 0.031 K check, and our own points fitting it with no free
  parameter. But our four measurements cannot resolve four from 3.96, because
  water's emissivity absorbs the difference.
- **Whether anything is per-unit.** A bit-depth shift would be a property of the
  product, not of a serial number, and the Android and Lightning variants agree.
  The store still keeps the scale per camera, which costs nothing and would show
  a second unit disagreeing rather than hiding it.
- **The iOS app's assumptions** are inside the two comparison points. It applies
  its own correction, and only its emissivity setting is known.
- **Linearity between 35 and 99 C** rests on the boiling point alone.

## 16. Cross-checked against an independent implementation of FLIR's model

The stored-image conversion was checked against `flirimageextractor`, a Python
port of Glenn Tattersall's `Thermimage`. Both implement the same published FLIR
model, so this checks arithmetic, not physics: an implementation written from
the same documentation cannot confirm the documentation. It is still worth
doing, because the one bug this module has had was arithmetic (the Planck
constants were read from the wrong offset).

Run over every pixel of `IMG_9400`, 640x480, using that file's own constants
and conditions:

```
              mean diff    max diff
  0 m          -0.0000      0.0000
  1 m          +0.0223      0.1096
  5 m          +0.0502      0.2467
 20 m          +0.1017      0.4990
```

At zero range the two agree to floating point. Every difference is therefore in
one place: the atmospheric path.

### The one divergence: how the path length enters tau

We model the path as one segment of length `d`, so the exponent carries
`sqrt(d)`. Thermimage models it as two segments of `d/2` whose transmissions
multiply, so the exponent effectively carries `sqrt(2d)`, and it attenuates
more. Which convention FLIR itself uses is not established here. The effect is
0.11 C at 1 m and 0.5 C at 20 m against a camera specified at about 3 C, and the
FLIR One is a close-range instrument, so it is recorded rather than acted on.

### What an official SDK would and would not settle

The Science File SDK reads radiometric files and returns temperatures with
FLIR's own code. Running it on `IMG_9400` would settle the atmospheric
convention above, which is the only open question in the stored-image path.

It cannot say anything about section 15. That SDK reads files, and the live
count scale is not in any file. The Atlas Android SDK does stream from a FLIR
One over USB, which would be a direct check, but only from the USB-C variant;
the unit here is Lightning and the Android SDK cannot reach it.

MSX is not a route in either. It embosses visible-light edges onto the rendered
thermal image and does not touch the radiometric plane. Temperatures computed
"from MSX data" are computed from the raw plane underneath it, with MSX only
affecting the picture. Reading temperature out of an MSX render measures a
palette.

## 17. A powered-off camera completes the handshake and never says so

The FLIR One has its own power button. With it off, the camera still enumerates,
still completes iAP2 authentication and identification, still returns
`sledInformation` with its serial and geometry, still opens `CameraFiles.zip`
and hands back a stream identifier, and still answers the stream request. The
frame endpoint then delivers nothing, for as long as you care to wait.

```
<- sledInformation: {"serialNumberBoard": "F02F9T00570", ...}
<- openFileStatus:  {"path": "CameraFiles.zip", "streamIdentifier": 10}
<- batteryChargingStateUpdate: {"chargingState": "stateManagedBatteryChargingOnly"}
<- setOptionStatus: {"option": "stream", "value": -1}
   frame endpoint: 0 bytes
```

The sled controller and the imager are separately powered, and only the first
one is talking.

**`value: -1` is not the signal.** It looked like a refusal and it is not: a
working stream returns exactly the same reply. That was checked by pressing the
button and running again without changing anything else.

```
<- setOptionStatus: {"option": "stream", "value": -1}
   161 frames in 20 s, 8.05 fps, 0 desyncs
```

So the only evidence that a stream started is bytes arriving on the frame
endpoint. `open()` now waits up to six seconds for the first of them and fails
with the power button named if none come. Those bytes are frame data and are
handed to the next `read_frame_chunk`, since dropping them would tear the first
frame.

This mattered more than a missing message. Without the check the failure is
silent in the worst way: the session is alive, the source reports "streaming",
no error is set anywhere, and the application waits forever.

### What a healthy session looks like

Measured on macOS, camera powered, in the same sitting:

```
bring-up            15.8 to 20 s from open() to the first frame
rate                8.0 fps over 4 minutes
frames              1922 in 240 s, 1531 in 210 s
longest gap         0.4 s
desyncs             0
reconnects          0
```

That is the answer to how long a stream survives, at least at this length: it
does not degrade. The 36-frame drop recorded in section 8 does not reproduce,
and the most likely explanation is that those early runs were fighting the
bring-up rather than the stream.

### Recovery from a cable pulled mid-stream

Traced through `UsbFrameSource` with the camera physically unplugged while it
was streaming, and plugged back in:

```
 t= 26.7s  handshake failed   the camera accepted the stream request and sent
                              no frames  (the imager was not powered)
 t= 51.9s  streaming
 t= 57.2s  waiting for camera  frames=34   iAP2 link dropped   <- unplugged
 t= 87.9s  streaming                                           <- recovered
```

It recovers in about 31 seconds, which is one bring-up, and needs no
intervention. The first entry is the check from this section firing on a real
camera rather than in a test.

The trace also corrected the `reconnects` counter. It used to be incremented
from the exception type, which missed the commonest drop of all: a pulled cable
surfaces as `FlirUsbError("iAP2 link dropped")`, the same error a camera that
was never there raises. A reconnect is now counted when a session that reached
streaming ends, which is what the word means.
