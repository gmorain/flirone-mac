# 003 — Scale the palette by percentile, not by extremes

## Context

Mapping temperatures to colours needs a range. The obvious choice is the minimum
and maximum of the frame.

## Decision

Default to the 1st and 99th percentile. Offer the true full range, manual
limits, and a locked range as alternatives.

## Why

Thermal scenes routinely contain a few saturated pixels. On the reference frame
0.6% of pixels sat at 191 °C while the 99th percentile was 71 °C, so scaling to
the extremes compressed everything of interest into the bottom quarter of the
palette and rendered the scene almost flat.

Comparing against the camera's own rendering of the same frame settled it: the
percentile version matches what the phone app shows, the min/max version
visibly does not.

## Consequences

The hottest pixels clip. That is acceptable because the measurement tools read
the temperature field directly and are unaffected by display scaling, so nothing
is lost from the numbers — only from the picture, deliberately.

Locking the range matters for comparing frames, so it stays available.
