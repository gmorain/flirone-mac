# flirone-mac

Viewer and radiometric measurement tool for the FLIR One thermal camera, on
macOS and Linux.

Live dual-mode preview (thermal + visible), spot/box/line measurements with
emissivity and atmospheric correction, and captures that can be reprocessed
later.

![Measuring on a radiometric image](docs/ui-measure.png)

Spot, box and line measurements over a radiometric image, with the line profile
plotted beneath and every reading in absolute degrees. Conditions, alignment and
calibration live on the Setup tab.

## Status

0.9.5 runs on macOS and Linux. Everything that works from a stored radiometric
image is complete: measurement, alignment, capture and export. Only live
streaming is blocked.

**Live streaming from a Lightning (iOS) FLIR One does not work yet.** The iAP2
accessory handshake is implemented and the camera authenticates successfully,
but it resets when asked to open its video session. The hardware is fine: it
streams from the official iOS app through the same adapter. Replaying the same
stack on Linux reproduces the failure exactly, which puts the fault in the
camera rather than in macOS. See
[docs/hardware-findings.md](docs/hardware-findings.md) for the full trace and the
list of eliminated causes.

The Android/USB-C variant speaks the documented vendor protocol with no iAP
layer and should work with this driver as written, though that is untested for
want of the hardware.

**Radiometric images shot with the phone app work today.** File > Open
radiometric image loads the raw 16-bit thermal plane along with this camera's
own Planck constants and the conditions of the shot, so every measurement tool
runs properly calibrated.

## Install

### macOS

```bash
brew install exiftool libusb
uv venv --python 3.12
uv pip install -e .
```

Two external tools are needed, for different halves of the application:

- **exiftool** reads FLIR's APP1 metadata: the camera's calibration, the
  conditions of the shot and the raw thermal plane. Without it, radiometric
  images cannot be opened at all. It is the only reliable reader for that
  layout, which is why it is shelled out to rather than reimplemented.
- **libusb** drives the camera over USB. Without it, files still work and only
  live capture is unavailable.

Both are located by absolute path rather than through `PATH`, so they are still
found from an app bundle launched by Finder, which inherits no shell
environment. 

### Linux

Everything runs on Linux, interface included. Development and release testing
happen on macOS, and only live capture is unavailable, for the reason in the
Status section rather than anything platform-specific.

```bash
sudo apt install libimage-exiftool-perl libusb-1.0-0
uv sync
```

Two host settings are needed before the camera will enumerate at all.

**Switch USB enumeration to the old scheme.** Under Linux's default the camera
answers the first descriptor request and then leaves the bus during the second
port reset, so it never receives an address and re-attaches about once a second
forever:

```bash
echo 1 | sudo tee /sys/module/usbcore/parameters/old_scheme_first
```

Make it permanent by adding `usbcore.old_scheme_first=1` to
`GRUB_CMDLINE_LINUX_DEFAULT` in `/etc/default/grub`, then `sudo update-grub`.
The setting is global and safe to carry: it was the kernel's own default
historically, and `use_both_schemes` keeps the new scheme as a fallback.

**Add a udev rule**, or pyusb can only claim the camera as root:

```bash
sudo tee /etc/udev/rules.d/99-flirone.rules >/dev/null <<'EOF'
SUBSYSTEM=="usb", ATTR{idVendor}=="09cb", MODE="0660", GROUP="plugdev", TAG+="uaccess"
EOF
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb
```

Your user has to be in `plugdev`. `ATTR{idVendor}` is lowercase hex with no
`0x`; `0x09CB` matches nothing and fails silently.

Section 12 of [docs/hardware-findings.md](docs/hardware-findings.md) has the
usbmon trace behind both.

## Run

```bash
uv run flirone
```

![The app on launch, waiting for an image](docs/ui-startup.png)

It opens waiting for an image. Drop one on the window, on the Dock icon, or pass
it on the command line.

### macOS

Or build a double-clickable bundle:

```bash
./tools/make_app.sh
```

### Linux

`uv run flirone` is enough. There is no dedicated build for Linux yet.

Qt ships its own platform plugins inside the venv, so nothing is needed beyond
the two packages above.

## Calibration

The camera streams sensor counts, not temperatures. Converting them needs the
Planck constants, which are **per unit** and are not carried in the USB stream.
Until you load them the app runs with a reference camera's constants, marks
itself UNCALIBRATED, and shows readings in orange: those are usable for relative
thermography and must not be quoted as absolute temperatures.

To calibrate, shoot one image with the FLIR One phone app and load it via
*File > Load calibration from radiometric JPEG*. The constants are read straight
out of the FLIR APP1 segment (exiftool is used as a fallback).

## Radiometric images

Open a JPEG shot with the FLIR One phone app by dropping it on the window,
dropping it on the Dock icon, passing it on the command line, or `File > Open
radiometric image` (Ctrl+O, ⌘O on macOS). Unlike the live stream, these carry
the camera's own calibration, so readings are absolute rather than relative.
Emissivity, reflected apparent temperature, humidity and distance are read from
the file and can then be adjusted, and the whole field is recomputed.

The raw plane's byte order is resolved by physical plausibility rather than
assumption: both orders are converted to temperature and scored against the
camera's own stated measurement range. Verified against a FLIR One Gen 2 file,
where the correct order is 172x smoother and puts 99.4% of pixels in range.

Auto scaling clips to the 1st-99th percentile. A few saturated pixels otherwise
consume most of the palette and flatten the scene; "Full range" gives true
min/max if you want it.

Drops also accept a saved calibration (`.json`) and a folder of captures, which
starts replaying it. Dropping a calibration together with an image applies the
calibration first.

## Alignment

The visible and thermal cameras sit a few millimetres apart, so the visible
image must be scaled and shifted onto the thermal one.

Radiometric JPEGs record the alignment the camera used (`Real2IR`, `OffsetX`,
`OffsetY`). Those offsets are in **visible-image pixels** and signed opposite to
this code's convention, so they need dividing by the visible-to-thermal grid
ratio and negating. Read correctly they are good, and the app applies them the
moment an image is opened.

They are not the whole story, though. The camera derives them from the
**user-set object distance**, usually left at its 1 m default. Parallax appears
on the horizontal axis, so:

- `dx` carries the parallax, so it is only as good as that distance setting
- `dy` is the fixed boresight offset and does not depend on distance

Which axis carries the parallax was established by measurement, not assumption:
the published optics (160x120 over 46 x 35 degrees) turn a fitted offset-versus-
distance slope into a lens separation, and only the horizontal axis gives one
near the 9 to 11 mm the camera actually has. The vertical axis implied 103
degrees of field, which is impossible.

`Auto-align from edges` measures both from the image. The two images share
almost no intensity relationship but do share structure, so registration runs on
gradient magnitude: phase correlation for translation, swept over scale,
selected by normalised cross-correlation. It runs automatically on import and
the result is reported on a summary card.

![Import summary](docs/import-card.png)

The card sets what the file recorded against what was measured from it, so
the two can be compared rather than silently merged.

![Alignment: none, as recorded, and recovered from edges](docs/alignment-comparison.png)

Left: no offset. Middle: the offsets the camera recorded. Right: recovered from
edges. Watch the warning triangle and the glowing blob, where only the right
panel puts the visible edges on the thermal features.

Measured on a FLIR One Gen 2 frame:

| quantity | recorded | measured | agreement |
|---|---|---|---|
| scale | 1.2379 | 1.2300 | 0.6% |
| `dx` (parallax) | +24.44 px | +24.15 px | 0.29 px |
| `dy` (boresight) | −12.44 px | −16.99 px | 4.54 px |

Edge cross-correlation improves from 0.262 using the recorded values to 0.360
using the measured ones, so measuring the alignment beats trusting the recorded
one whenever the object distance was left at its default.

### Spot size

A radiometric reading is only trustworthy when the target fills several detector
elements. Below three the pixel averages target and surroundings and the reading
is pulled toward the background, which is the commonest way to get a confidently
wrong temperature.

The cursor readout warns when the feature under it is too small, judged on the
**detector** grid: files from the phone app arrive upscaled 4x, which adds no
information, so a target that looks 12 px wide in the file is 3 px on the sensor.

With one detector pixel subtending 5.05 mrad (160x120 over 46 x 35 degrees):

| distance | one pixel | minimum target (3 px) | comfortable (10 px) |
|---|---|---|---|
| 0.3 m | 1.5 mm | 5 mm | 15 mm |
| 0.5 m | 2.5 mm | 8 mm | 25 mm |
| 1 m | 5.1 mm | 15 mm | 51 mm |
| 2 m | 10.1 mm | 30 mm | 101 mm |

The check needs no calibration; it works on the image alone. Setting the
distance in Conditions additionally expresses the size in millimetres.

## Measurement tools

- Spots, boxes and line profiles, with min/max/mean/median/stddev
- Auto-tracked hottest and coldest pixel
- Delta between any two spots, the usual way a thermal fault is called
- Emissivity, reflected apparent temperature, air temperature, humidity and
  distance, applied through the full FLIR object-radiance correction
- Isotherm masking

Left-click places or drags, right-click removes.

Scroll to zoom, up to 16x, anchored on the pointer so the feature under it stays
put. In Cursor mode a left-drag pans, so no middle button is needed. At
fit-to-window the image is drawn at roughly its own size, which is too small to
judge an alignment to the pixel.

Blend and the three alignment values are sliders with the value beside them. On
the two offset sliders a click beside the handle steps one pixel rather than
jumping to where it landed, so a stray click does not discard a careful
alignment; the arrow keys step by one as well.

## Captures

A capture stores everything needed to reconstruct the measurement later: raw
counts, the temperature field, the visible photograph, the rendered image, and
the calibration and conditions in force.

Line profiles are written as CSV alongside, one file per line, and can also be
exported on their own with `File > Export line profile as CSV` (Ctrl+E, ⌘E on
macOS):

```
# flirone line profile bed
# from,(0,384),to,(479,384),samples,480
# calibration,camera,source,IMG_3888.JPG (F02F9T00570)
# emissivity,0.95,reflected_c,22.0,atmospheric_c,20.0,humidity,0.5,distance_m,1.0
index,distance_px,x_px,y_px,temperature_c
0,0.000,0,384,53.730
1,1.000,1,384,53.681
```

Pixel coordinates come with every row, so a sample can be traced back to the
image. The commented header carries the calibration and conditions, because a
column of degrees separated from its provenance cannot be checked or recomputed.
Read it with `pandas.read_csv(path, comment="#")`.

Hovering the image reports the pixel under the cursor; hovering the profile plot
marks the corresponding pixel on the image and names the CSV row, which is how a
feature on the curve gets tied to a place in the scene.

## Diagnostics

```bash
uv run flirone-probe           # descriptor tree, read-only
uv run flirone-probe --open    # also set the configuration and claim the interfaces
```

An idle camera re-enumerates every few seconds, so the probe polls rather than
looking once; `--wait` sets for how long.

The scripts in `research/iap2-probes/` are the evidence behind
[docs/hardware-findings.md](docs/hardware-findings.md). They are unmaintained,
excluded from lint and not part of the package.

## Credit

The frame format and init sequence come from the EEVblog thermal-imaging
community's reverse engineering, via
[fnoop/flirone-v4l2](https://github.com/fnoop/flirone-v4l2) (GPL-2.0).
