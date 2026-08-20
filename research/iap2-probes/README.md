# iAP2 hardware probes

Throwaway scripts written while working out why a Lightning FLIR One will not
stream over USB. They are kept because they are the evidence behind
`docs/hardware-findings.md`, not because they are useful code: they hardcode
paths, ignore errors and were each edited in place many times.

They are excluded from lint and are not part of the package.

Roughly in the order they were written:

| script | what it established |
|---|---|
| `probe.py`, `probe_eps.py` | endpoint and descriptor layout |
| `probe_pounce.py` | the camera resets every 4.7s |
| `probe_iap.py` | the 6-byte message is a valid iAP1 frame |
| `probe_iap2_link.py` | answering the SYN stops the watchdog |
| `probe_iap2_control.py` | identification, and the EA protocol list |
| `probe_iap2_auth.py` | full authentication, certificate and challenge |
| `probe_ea_video.py`, `probe_config_session.py` | EA sessions; config is accepted |
| `probe_rosebud.py` | FLIR's own command framing over EA |
| `probe_idle.py`, `probe_trend.py` | the failure is deterministic, not power |
| `probe_ea_matrix.py` | fileio and frame sessions both reset the camera |
| `probe_reenum.py` | it is a genuine reset, descriptors unchanged |
| `probe_linksync.py`, `probe_order.py`, `probe_ackpolicy.py` | link parameters, handshake order and ack policy all ruled out |

To run one, the camera must be attached and `uv sync` done:

    uv run python research/iap2-probes/probe_iap2_auth.py
