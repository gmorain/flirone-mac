# 001 — Select registration scale by cross-correlation, not peak height

## Context

Registering the visible image onto the thermal one needs a scale as well as a
translation. Translation comes from phase correlation; scale is found by trying
candidates and picking the best. "Best" has to be defined.

## Decision

Score each candidate scale by normalised cross-correlation of the edge maps
*after* applying the shift that phase correlation found. Do not score by the
height or sharpness of the correlation peak.

## Why

Peak height is not comparable across scales. A correlation peak sharpens as an
image loses detail, so scoring on it rewards blur: the search walks to the
largest scale in the range and reports high confidence there. The first
implementation did exactly this and returned 1.60, the boundary of its own
sweep, on a scene whose true scale was 1.238.

Cross-correlation after the shift measures what is actually wanted — how well
the structures agree — and is comparable between candidates.

## Consequences

Slower, since each candidate needs a second pass. Irrelevant at this scale: the
whole estimate runs in under a second.

Where a file states the scale, the sweep is a narrow refinement around it rather
than a search, which removes the failure mode entirely for that case.
