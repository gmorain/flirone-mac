# CLAUDE.md

Project conventions live in AGENTS.md and apply to any agent. Read it.

@AGENTS.md

Specifications live in `specs/`. Read the relevant feature spec before changing
behaviour it covers, and update it in the same change. `specs/decisions/` records
four choices that were each got wrong first and corrected by evidence; read them
before revisiting registration scoring, raw byte order, palette scaling or how
USB streaming is started.

## Running things here

```bash
uv run pytest -q                  # 77 tests, all offline
uv run ruff check . && uv run ruff format --check .
uv run flirone                    # the app
./tools/make_app.sh               # rebuild the bundle
```

The interface can be driven headlessly for verification:

```bash
QT_QPA_PLATFORM=offscreen uv run python - <<'PY'
...build a MainWindow, call _tick(), grab() to a PNG...
PY
```

Do that rather than asking the user whether something looks right. Screenshots
of the real window are the evidence, not a description of the intended layout.

## Editing gotchas

`ruff format` reflows signatures across lines, so a string-replacement patch
written against an earlier layout will silently match nothing. Assert that every
replacement applied before writing the file. Two bugs in this repo's history came
from patches that quietly did nothing: a dataclass whose field types were never
corrected, and a method whose call sites landed without the method.

Nothing in `research/` is maintained or linted. It is the evidence behind
`docs/hardware-findings.md`, kept because the blocker it documents is still open.

## Hardware

Most work needs no camera: the app runs against radiometric images, recorded
captures and a synthetic scene. Only the USB and iAP2 paths need hardware.

Never present a probe result that was not actually observed. The findings
document distinguishes what was measured from what was inferred, and that
distinction is the only thing making it worth keeping. If a claim there turns
out to be wrong, correct it in place rather than leaving it to mislead.

## Judgement calls

The governing rule in AGENTS.md — never present a number the data cannot
support — outranks convenience, and it is the reason for most of the odd-looking
decisions here: why registration refuses flat scenes, why a negative parallax
constant is rejected rather than saved, why calibration trust has three states
instead of two. When a change would make the software quieter about uncertainty,
that is the wrong direction.
