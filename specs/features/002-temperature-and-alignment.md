# 002 — Temperature and alignment

Status:  shipped

Depends: 001-getting-images-in

---

## User

Two things are derived from a frame, and both are shown with their limits
attached.

**Temperature.** Every reading is degrees Celsius derived from raw radiance
counts, not a colour or a relative value. The conversion accounts for the
physical situation: how emissive the surface is, how warm its surroundings are,
and how much the intervening air absorbs and re-emits over the distance. A user
can change any of those after the fact and the whole field is recomputed, so a
measurement taken against wrong assumptions is corrected without reshooting.

**Alignment.** The two cameras sit a few millimetres apart and see slightly
different scenes, so the visible image is scaled and shifted onto the thermal
one. This happens on import without being asked, and can be rerun or adjusted by
hand. Where an image records the alignment the camera used, that is applied
immediately, but it is not trusted along the axis where the camera derives it
from a subject distance the user is unlikely to have set. Measuring from the
image itself corrects that. A scene too thermally flat to align is reported as
such, with the figures, rather than yielding a confident result derived from
sensor noise.

## Tech

Temperature uses standard FLIR inverse-Planck with the object-radiance
correction: measured signal is object emission plus reflected ambient plus
atmospheric self-emission, and the last two are subtracted before inverting.
Atmospheric coefficients are read from the file when present. Counts outside the
Planck domain are clamped and surface as extreme temperatures rather than NaN
warnings mid-render.

Registration runs on gradient magnitude, since the two images share almost no
intensity relationship but do share structure. Translation comes from phase
correlation with sub-pixel refinement; scale is refined around a prior and
scored by normalised cross-correlation after applying the shift, not by peak
height; see decision 001. Frames spanning under 2 °C are refused and under 8 °C
warned, because a room-temperature wall spans under a degree and its apparent
edges are noise, which correlates with anything. Recorded offsets are converted
from visible-image pixels and inverted sign, not used raw.

One alignment type is shared by the reader, the renderer and the registration
code, carrying fractional offsets in thermal pixels. A recovered alignment is
that type plus a match quality.

Covered by tests inverting the forward Planck function across −20 to 400 °C,
recovering known shifts from synthetic scenes, asserting the refusal path for
flat fields.

## Tasks

- [x] Inverse-Planck conversion with emissivity, reflected and atmospheric correction
- [x] Live recomputation when conditions change
- [x] Gradient-magnitude registration with cross-correlation scoring
- [x] Thermal contrast guard with explicit refusal
- [x] Conversion of recorded offsets to the internal convention
