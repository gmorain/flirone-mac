# 002 — Choose raw byte order by physical plausibility

## Context

FLIR stores the raw thermal plane as a 16-bit PNG. PNG is defined big-endian,
but the samples inside are written little-endian, so the decoded array usually
needs swapping. Usually, not always, and the file does not say.

## Decision

Convert both byte orders to temperature and score them against the camera's own
stated measurement range, which the file does carry. Take the better one.

## Why

Assuming either order is wrong somewhere, and a wrong choice is not subtle: it
produces a plausible-looking image of noise. An earlier attempt guessed using
dynamic range, which is not a reliable discriminator and crashed on NumPy 2.

Physical plausibility is decisive in practice. On the reference frame the
correct order puts 99.4% of pixels inside the camera's range against 46% for the
wrong one, and is 172 times smoother by mean neighbour difference. There is no
ambiguous middle.

## Consequences

Costs one extra conversion per file, which is negligible.

Depends on the file carrying its measurement range. Where it does not, the
default bounds are wide enough to still separate the two cases.
