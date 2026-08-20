# 004 — Start streaming through the alternate-setting API

## Context

The camera gates its video endpoint behind a standard SET_INTERFACE request
selecting alternate setting 1. The reference Linux implementations issue that as
a raw control transfer, and it works there.

## Decision

Select the alternate setting through the normal USB API, never as a raw control
transfer.

## Why

macOS does not treat SET_INTERFACE as an opaque request. IOKit tracks alternate
settings itself and rebuilds its pipe objects when one changes. A raw control
transfer changes the device's state without telling the host, so the host keeps
the pipes belonging to alternate setting 0 — which expose no endpoints — and
every subsequent read times out forever.

This is the cause of a publicly reported, unresolved libusb issue against this
exact camera, where interfaces claim successfully and bulk reads then fail with
a timeout.

## Consequences

The same code path works on both platforms, since the API call does the right
thing on Linux too.

Note this fixes the transport only. On Lightning cameras the accessory handshake
is a separate and still-unresolved blocker; see feature 001.
