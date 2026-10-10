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
(`DEV: m caps=ext1,log,hostto,home,limits,tmc,soft`), and everything they
open, the `#` channel, the host timeout and heartbeat, the FAULT and LIMIT
stops, per-axis Zero and Home, homed and limit state, lives in the `Probe`
base (`model/probe.py`), gated on those caps.

The soft travel limit (cap `soft`, X-14, MEGA_STANDARD section 4) lives
here, the one standard feature only this board has so far: an axis whose
carriage cannot reach one of its switches is held to `#SOFTLIMIT` counts
from LS1, measured from where LS1 last tripped. The page shows each axis's
limit from `#INFO` and sets it while the stage is disabled; a Step that
would cross a referenced limit is refused before its frame is sent, and the
board's `#EVT SOFTLIMIT ... stopped` ends a Step in flight on every axis, as
a LIMIT does. No `#SOFTLIMIT` byte goes to a board whose caps lack `soft`.

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
import schema as sch
from devices.mega_standard_sim import CAPS, LETTER, MegaStandardPort
from events import events
from model.probe import (AXES, EXT, STEPPER_UM_PER_COUNT, ProbeMode, StepperProbe,
                         _stored)
from param import Param

#: The capability of the soft travel limit (MEGA_STANDARD section 2).
SOFT = "soft"
#: `#SOFTLIMIT`'s ceiling on the board (STD_SOFT_MAX: 100 mm), counts.
MAX_SOFT_COUNTS = 160000
#: A soft limit is set only while the stage is disabled (the board refuses
#: `#SOFTLIMIT` while enabled or moving).
_SOFT_GATE = ("idle", "autonomous", "manual", "latched", "fault")
#: The board's soft-limit refusals, in an operator's words.
_SOFT_REASONS = {
    "soft-limit": "it is at its soft limit",
    "soft-limit-unreferenced:touch-ls1": (
        "it has a soft limit and has not touched LS1 since the board started or "
        "lost its count: jog it onto LS1 (or Step toward LS1) first"),
    "soft-limit-eeprom-damaged:set-SOFTLIMIT": (
        "its stored soft limit is damaged: set it again under Configuration"),
}
#: The X and Z distances are negated on the wire (stepper_firmware): the board
#: moves each axis by sign x step size x distance counts.
_WIRE_SIGN = {"X": -1, "Y": 1, "Z": -1}


def _and(names):
    names = list(names)
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


class XyzStageMega(StepperProbe):
    """The XYZ stage on one standard Mega board."""

    NAME = "XYZ Stage (Mega)"
    IDENTITY = LETTER
    NEEDS_PORT = True
    NEEDS_GAMEPAD = True
    #: The standard's caps, for the schema while the board has not answered:
    #: the simulator's set, plus the soft limit this sketch has (X-14).
    ASSUMED_CAPS = frozenset(CAPS) | {SOFT}

    PARAMS = {**StepperProbe.PARAMS, **{p.name: p for p in (
        # X-14: what "Set soft limits" stores on the board, um from LS1 (0 =
        # none); seeded from #INFO, so the entry shows the board's value.
        *[Param(f"{a}_soft_um", "float", default=0, minimum=0,
                maximum=MAX_SOFT_COUNTS * STEPPER_UM_PER_COUNT, unit="µm",
                label=f"{a.upper()} Soft Limit") for a in "xyz"],
    )}}
    SOFT_PARAMS = ("x_soft_um", "y_soft_um", "z_soft_um")

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

    # -- the soft travel limit (X-14) -----------------------------------------------
    def _soft(self, axis):
        """What `#INFO` says about one axis's soft limit: None before INFO (or
        before the board answered at all); {"supported": False} when its caps
        lack `soft`; else {"supported", "counts", "ref", "lim", "damaged",
        "out"} (`lim` the limit in board counts while referenced, `out` the
        way it lies from LS1: -1, +1, or None while LS1's end is unknown)."""
        caps = self.caps
        if caps is None:
            return None
        if EXT not in caps or SOFT not in caps:
            return {"supported": False}
        info, low = self._ext_info, axis.lower()
        try:
            counts = int(info[f"{low}_soft"])
            lim = info.get(f"{low}_soft_lim")
            lim = None if lim is None else int(lim)
            end = int(info.get(f"{low}_ls1_end", "0"))
        except (KeyError, ValueError):
            return None
        return {"supported": True, "counts": counts, "ref": info.get(f"{low}_soft_ref") == "1",
                "lim": lim, "damaged": info.get(f"{low}_soft_damaged") == "1",
                "out": -end if end else None}

    def _take_info(self, fields):
        super()._take_info(fields)
        seeded = self.__dict__.setdefault("_soft_seeded", {})   # axis -> the counts last seeded
        for axis in AXES:
            soft = self._soft(axis)
            if not soft or not soft.get("supported"):
                continue
            if seeded.get(axis) != soft["counts"]:
                seeded[axis] = soft["counts"]
                self._param_store[f"{axis.lower()}_soft_um"] = round(
                    soft["counts"] * STEPPER_UM_PER_COUNT, 6)

    def _soft_counts(self, axis):
        """The axis's Soft Limit entry in whole counts."""
        um = self.PARAMS[f"{axis.lower()}_soft_um"].coerce(
            self._param_store.get(f"{axis.lower()}_soft_um"))
        return int(round(float(um) / STEPPER_UM_PER_COUNT))

    def set_soft_limits(self):
        """Store each axis's Soft Limit entry on the board (`#SOFTLIMIT A
        <counts>`, in its EEPROM; 0 = none), only while the stage is
        disabled. An axis whose entry already matches the board is left
        alone. Never sent to a board whose caps lack `soft`."""
        self._require_ext(SOFT, "Set soft limits")
        self._guard("Set soft limits")
        if self._mode is not ProbeMode.DISABLED:
            self._refuse(f"Disable the {self.NAME} first: the board takes a soft "
                         "limit only while its drivers are off.")
        if not self._ext_info:
            self._ext_request("INFO")
        changes = {}
        for axis in AXES:
            soft = self._soft(axis)
            if soft is None:
                self._refuse(f"The {self.NAME} has not reported axis {axis}'s soft "
                             "limit. Wait for its link, then try again.")
            wanted = self._soft_counts(axis)
            if wanted != soft["counts"] or soft["damaged"]:
                changes[axis] = wanted
        done = []
        for axis, counts in changes.items():
            reply = self._ext_request(f"SOFTLIMIT {axis} {counts}")
            if not reply.ok:
                self._ext_send("INFO")
                set_already = f" Axis {_and(done)} was set." if done else ""
                self._refuse(f"Axis {axis} did not store its soft limit ({reply.why})."
                             f"{set_already}")
            done.append(axis)
            um = counts * STEPPER_UM_PER_COUNT
            events.info("Soft Limit Set", f"Axis {axis} of the {self.NAME}: "
                        + (f"soft limit {um:g} µm ({counts} counts) from LS1. It holds "
                           "once the axis has touched LS1." if counts else "no soft limit."),
                        source=self.NAME)
        if done:
            self._ext_request("INFO")
        return "set " + ", ".join(done) if done else "unchanged"

    def _check_soft_limits(self):
        """Refuse a Step that would carry an axis past its referenced soft
        limit, before the frame is sent: the board would stop that axis on
        the limit and the Step would leave its line. The board stays the
        guard (an unreferenced axis, a stale position); this says so first."""
        for index, axis in enumerate(AXES):
            low = axis.lower()
            delta = _WIRE_SIGN[axis] * _stored(self, f"{low}_dist") * _stored(self, f"{low}_step")
            soft = self._soft(axis)
            if (not delta or not soft or not soft.get("supported") or not soft["counts"]
                    or not soft["ref"] or soft["lim"] is None or soft["out"] is None):
                continue
            here = int(self._position[index])
            if (here + delta - soft["lim"]) * soft["out"] > 0:
                room = max(0, (soft["lim"] - here) * soft["out"])
                self._refuse(f"Axis {axis} would pass its soft limit: it can move at "
                             f"most {room * STEPPER_UM_PER_COUNT:g} µm ({room} counts) "
                             "further that way. Shorten the Step; nothing was sent.")

    def step(self):
        self._guard("Step")
        if not self.is_moving:
            self._check_soft_limits()
        return super().step()

    def zero_axis(self, axis, confirmed=False):
        done = super().zero_axis(axis, confirmed)
        self._ext_send("INFO")                  # the limit's count moved with the origin
        return done

    def _on_ext_event(self, text):
        words = text.split()
        kind = words[0].upper() if words else ""
        found = dict(w.split("=", 1) for w in words[1:] if "=" in w)
        bare = [w for w in words[1:] if "=" not in w]
        axis = next((w.upper() for w in bare if w.upper() in AXES), None)
        if kind == "SOFTLIMIT" and axis:
            what = next((w.lower() for w in bare if w.upper() not in AXES), "")
            return self._soft_event(axis, what, found)
        reason = found.get("reason", "")
        if kind == "REFUSED" and reason in _SOFT_REASONS:
            if self._mode is ProbeMode.AUTO:
                self._moving_deadline = None     # the frame did not run
            events.warn("Move Refused", f"The {self.NAME} refused to move axis "
                        f"{axis or '?'}: {_SOFT_REASONS[reason]}. Nothing moved.",
                        source=self.NAME)
            return None
        super()._on_ext_event(text)
        if kind == "HOMED":
            self._ext_send("INFO")              # the limit's count moved with the origin
        return None

    def _soft_event(self, axis, what, found):
        """`#EVT SOFTLIMIT A ...`. A stop on the limit is a LIMIT to the Step:
        a Step in flight is stopped on every axis (the board already stopped
        this one, on its limit)."""
        self._ext_send("INFO")
        limit = found.get("lim", "?")
        if what == "stopped":
            if self._homing[axis] is not None:
                events.debug("Soft Limit", f"axis {axis} stopped at {limit} while "
                             "homing", source=self.NAME)
                return
            self._last_limit[axis] = f"soft limit at {limit}"
            stopped = ""
            deadline = self._moving_deadline
            if self._mode is ProbeMode.AUTO and deadline is not None:
                self._moving_deadline = None
                try:
                    self._send_zero_frame(f"soft limit on axis {axis}")
                    stopped = " The Step was stopped there, on every axis."
                except Exception as exc:
                    events.debug("Stop Frame Failed", repr(exc), source=self.NAME,
                                 exception=exc)
            events.warn("Soft Limit Reached", f"Axis {axis} of the {self.NAME} stopped "
                        f"at its soft limit ({limit} counts).{stopped}", source=self.NAME)
        elif what == "referenced":
            events.info("Soft Limit Referenced", f"Axis {axis} of the {self.NAME} "
                        f"touched LS1 at {found.get('ls1', '?')} counts: its soft limit "
                        f"is at {limit} counts.", source=self.NAME)
        elif what == "lost":
            events.warn("Soft Limit Reference Lost", f"Axis {axis} of the {self.NAME} "
                        f"lost its LS1 reference ({found.get('reason', 'no reason given')}). "
                        "Until it touches LS1 again it moves only by stick, or toward LS1.",
                        source=self.NAME)

    @property
    def soft_text(self):
        """Each axis's soft limit as `#INFO` reports it (X-14)."""
        parts = []
        for axis in AXES:
            soft = self._soft(axis)
            if soft is None:
                text = "?"
            elif not soft.get("supported"):
                text = "not in this firmware"
            elif soft["damaged"]:
                text = "stored value damaged: set it again"
            elif not soft["counts"]:
                text = "none"
            else:
                text = f"{soft['counts'] * STEPPER_UM_PER_COUNT:g} µm from LS1"
                text += (f", at {soft['lim']} counts" if soft["ref"] and soft["lim"] is not None
                         else ", not referenced (touch LS1)")
            parts.append(f"{axis} {text}")
        return "; ".join(parts)

    @property
    def limits_text(self):
        """The base's switch state, with each axis's soft limit beside it."""
        parts = super().limits_text.split("; ")
        if len(parts) != len(AXES):
            return "; ".join(parts)
        for i, axis in enumerate(AXES):
            soft = self._soft(axis) or {}
            if soft.get("damaged"):
                parts[i] += ", soft limit damaged"
            elif soft.get("counts") and soft.get("ref") and soft.get("lim") is not None:
                parts[i] += f", soft limit at {soft['lim']}"
            elif soft.get("counts"):
                parts[i] += ", soft limit not referenced"
        return "; ".join(parts)

    @property
    def schema(self):
        built = super().schema
        caps = self._schema_caps()
        if EXT not in caps or SOFT not in caps:
            return built
        P = self.PARAMS
        elements = []
        for name in self.SOFT_PARAMS:
            elements += [sch.entry(P[name].label + ":", name, P[name], disabled_when=_SOFT_GATE),
                         sch.readonly("counts", name.replace("_um", "_counts"),
                                      secondary=True, unit="counts")]
        elements += [sch.button("Set soft limits", "set_soft_limits",
                                inputs=self.SOFT_PARAMS, disabled_when=_SOFT_GATE),
                     sch.readonly("Soft limits:", "soft_text")]
        section = next((s for s in built["sections"] if s["title"] == "Configuration"), None)
        if section is not None:
            section["elements"].extend(elements)
        return built


for _name in XyzStageMega.SOFT_PARAMS:
    setattr(XyzStageMega, _name, XyzStageMega._gated_param(_name))
    setattr(XyzStageMega, _name.replace("_um", "_counts"),
            property(lambda self, _n=_name: self._soft_counts(_n[0].upper())))
del _name
