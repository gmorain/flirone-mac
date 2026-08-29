# iAP2 hardware probes

Four scripts that answer questions still open for hardware other than the unit
they were written against. They need a camera attached and `uv sync` done:

    uv run python research/iap2-probes/probe_phone_order.py

| script | what it answers |
|---|---|
| `probe_phone_order.py` | does this camera stream at all? Replays the iPhone's bring-up, section 14. The first thing to run when a new camera or a new platform will not start |
| `probe_fixed_points.py` | is the count scale right for this unit? Checks it against ice water and boiling water, which are fixed by physics |
| `probe_live_session.py` | capture round trip, stream survival, frame rate |
| `probe_recovery.py` | how long does a session take to come back after a cable pull? |

They are excluded from lint and are not part of the package.

## The other twenty-six

The investigation probes were removed in 1.0.1. They existed to answer why a
Lightning FLIR One would not stream, which sections 14 to 17 of
`docs/hardware-findings.md` now answer, and the project convention kept them
only while that blocker was open.

They are in git history. `git show v1.0.0:research/iap2-probes/` lists them as
they stood, and what each established is recorded in the findings document,
which is where it belonged in the first place: a conclusion that needs a script
present to be believed was never properly written down.
