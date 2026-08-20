# AGENTS.md

Conventions for any agent working on this project.

## Stack

- Python managed with `uv` and `pyproject.toml`. Never `pip install` into the
  environment directly.
- Lint and format with Ruff. `ruff check src/ tools/ tests/` must pass before a
  change is considered done. `research/` is excluded on purpose.
- Type-hint public function signatures. Prefer `pathlib` over `os.path`.
- NumPy for the temperature field, Pillow for image containers, PySide6 for the
  interface, pyusb for the camera.
- Do not add a dependency without asking. GeoTIFF support is deliberately an
  optional extra rather than a hard dependency.

## Layout

- `src/flirone/` is the library and must not import from `src/flirone/ui/`.
  Every capability has to be usable from a script or a notebook.
- `src/flirone/ui/` is the PySide6 application and depends on the library, never
  the reverse.
- `tools/` holds maintained utilities that ship. `research/` holds throwaway
  hardware probes kept as evidence; they are not maintained and not linted.
- `docs/hardware-findings.md` is the reverse-engineering record. Append to it,
  and correct earlier conclusions in place when later evidence overturns them
  rather than leaving them to mislead.

## Measurement honesty

This is the project's governing rule, and it outranks convenience.

- Never present a temperature as absolute when the Planck constants did not come
  from the camera that took the picture. Track provenance, surface it.
- Refuse to produce a number from data that cannot support it. A registration on
  a thermally flat scene, or a parallax fit with a negative `K`, must fail loudly
  rather than return something plausible.
- State uncertainty where it is large. An estimate that degrades quadratically
  with distance has to say so.
- When a value can be measured or assumed, prefer measuring, and report both when
  they disagree.

## Testing

- `pytest`, in `tests/`, mirroring module names.
- Test the maths against known ground truth: invert the forward model, use
  synthetic scenes with known answers, assert on captured real-hardware bytes.
- Tests needing hardware or a real camera file must skip cleanly, never fail.
- Any bug found in reasoning, not just in code, gets a regression test.

## Security and external tools

- Resolve external binaries by absolute path, not through `PATH`. An app bundle
  launched from Finder inherits no shell environment.
- Treat anything read from a file or a device as untrusted: validate lengths and
  checksums before acting on them, and resynchronise rather than trusting a
  header that does not add up.
- No secrets in the repository. There are none today; keep it that way.

## Writing

- Short declarative sentences. Lead with the conclusion. Assume a competent
  reader.
- Comments explain why, not what. A comment restating the code is noise; a
  comment recording why an obvious approach was rejected is worth keeping.
- No em-dashes. No marketing language in docs, comments or commit messages.
- Commit messages describe the change and its reason, briefly.

## Git

- Never add a `Co-Authored-By` trailer.
- Propose commits; do not commit unprompted. Do not push unprompted.
- Identity is bound to the remote URL by `includeIf` rules. Never set
  `user.name` or `user.email`, and never change a remote.

<!-- BEGIN project_add_lightspec -->
## LightSpec

Specifications live in `specs/`:

- `specs/overview.md` — what the system is, who it is for, what it does not do
- `specs/features/NNN-slug.md` — one file per user-facing capability

Read the relevant feature spec before changing behaviour it covers, and update
it in the same change. The `## User` section is capability language and must not
name modules or files; the `## Tech` section is the short one and records
constraints and known gaps, not a restatement of the code.
<!-- END project_add_lightspec -->
