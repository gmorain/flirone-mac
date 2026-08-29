# 004 — Select alternate settings through the USB API

## Context

The camera's video endpoint lives on alternate setting 1 of its interfaces, and
the setting has to be selected before anything can be read from it. The
reference Linux implementations issue that as a raw control transfer, and it
works there.

## Decision

Select the alternate setting through the normal USB API, never as a raw control
transfer.

## Why

macOS does not treat SET_INTERFACE as an opaque request. IOKit tracks alternate
settings itself and rebuilds its pipe objects when one changes. A raw control
transfer changes the device's state without telling the host, so the host keeps
the pipes belonging to alternate setting 0, which expose no endpoints, and every
subsequent read times out forever.

This is the cause of a publicly reported, unresolved libusb issue against this
exact camera, where interfaces claim successfully and bulk reads then fail with
a timeout.

## Consequences

The same code path works on both platforms, since the API call does the right
thing on Linux too. Verified against the camera on macOS and on Ubuntu 22.04.

## What this decision is not

It was originally written as "start streaming via alternate setting", which was
wrong about what starts a stream, and the title outlived the evidence by two
releases.

Selecting the alternate setting is necessary on both transports and starts the
stream on neither. On the Android/USB-C path a vendor command follows. On the
Lightning path, which is the one this project actually streams from, what starts
the stream is a `setOption` command over the iAP2 configuration session, and
opening an accessory session for video is precisely what kills the link. That
was unknown when this decision was written; sections 14 and 17 of
`docs/hardware-findings.md` have the traces.

The IOKit reason above is unaffected, and is why the decision still stands.
