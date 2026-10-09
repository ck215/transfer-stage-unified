"""XYZ Stage (Mega): the XYZ stage on one Mega 2560, all three axes (owner
rulings 2026-10-09, docs/rebuild/MEGA_STANDARD.md).

Its own device in the Probe family, beside the Teensy `XYZ Stage`
(`model/xyz_stage.py`), which stays as it is. The board speaks the Stepper
Probe's frame protocol byte for byte, so this is the Stepper Probe's frame
code and units: um views over counts at 0.625 um per count, a target in um
stored as whole step-size units (the board moves step size x distance
counts), and the 32767-step guard on one Step (the board multiplies them
into a 16-bit `int`). Speeds keep the Stepper Probe's 3200 counts/s ceiling.

What the board adds is standard: its identity lists its capabilities
(`DEV: m caps=ext1,log,hostto,home,limits,tmc`), and everything they open,
the `#` channel, the host timeout and heartbeat, the FAULT and LIMIT stops,
per-axis Zero and Home, homed and limit state, lives in the `Probe` base
(`model/probe.py`), gated on those caps.

Why a module of its own rather than a class in probe.py: the class is the
one Probe that builds its port from `devices.mega_standard_sim` (the
simulator a SIM row runs), and probe.py stays the family's base, importing
no board of its own; like `xyz_stage.py`, one device per module.

The one thing this class assumes is in its schema only: before the board
has answered (a Web card is built before the handshake ends), the page
shows the standard's features, greyed by `enabled_by` where INFO later says
the hardware is absent. The wire never assumes: no `#` byte goes out until
the board's own identity lists ext1.
"""
from devices.mega_standard_sim import CAPS, LETTER, MegaStandardPort
from model.probe import STEPPER_UM_PER_COUNT, StepperProbe


class XyzStageMega(StepperProbe):
    """The XYZ stage on one standard Mega board."""

    NAME = "XYZ Stage (Mega)"
    IDENTITY = LETTER
    NEEDS_PORT = True
    NEEDS_GAMEPAD = True
    #: The standard's caps, for the schema while the board has not answered.
    ASSUMED_CAPS = frozenset(CAPS)

    def _build_port(self, port, sim):
        """A MegaStandardPort: "SIM" (or `sim=True`) puts the standard
        board's simulator behind the real transport; a port name is the
        board. A port object handed in (a test's) is used as it is."""
        if port is None or isinstance(port, str):
            return MegaStandardPort("SIM" if sim else port, owner=self.NAME)
        return port

    @property
    def scale_note(self):
        """The stage's lead is known (MEGA_STANDARD section 3: 1600 counts
        per mm), unlike the Stepper Probe's."""
        return (f"{STEPPER_UM_PER_COUNT:g} µm per count (1 mm lead, 200 full steps, "
                "8 microsteps)")
