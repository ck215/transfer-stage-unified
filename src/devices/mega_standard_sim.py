"""A standard Mega board (docs/rebuild/MEGA_STANDARD.md, 2026-10-09),
simulated at the protocol level, and the serial link that carries it.

Two things live here.

`MegaStandardSim` is one Mega 2560 running a standard sketch, shaped like the
pyserial handle `SerialPort` drives (`write`, `read`, `in_waiting`, ...), as
`teensy_axis.AxisSimulator` is for one axis board. It speaks:

* the Stepper Probe's frame protocol byte for byte (MEGA_STANDARD section 1,
  read out of `firmware/stepper_firmware/stepper_firmware.ino`): the 42-byte
  jog packet after 0xAA (mode 1 jog, mode 0 stop, an implausible packet
  ignored), `e`, `d`, `s`, the 12-field autonomous frame (X and Z
  distances negated, step size x distance in a 16-bit `int`, per-axis speed
  shares of the vector speed, constant-speed moves), the D-pad step, the
  250 ms jog dead-man, the 30 ms enable settle, and `POS:x,y,z` every
  100 ms;
* the identity reply with its capabilities, `DEV: m caps=...` (section 2);
* the `#` extension channel (section 3): INFO, AXISCFG, LOG, HOSTTIMEOUT,
  HB, HOME, ZERO and STOP, one `#OK`/`#ERR` per command, `#EVT` lines;
* the per-axis safety rules of section 4, in counts: the limit interlock
  with its learned ends, the parked switch (jog only, 160 counts/s, 800
  counts of travel), `#EVT REFUSED` for a frame it will not run, the host
  timeout and its 10 s disable, driver faults, absent hardware inert, and a
  switch seen once.

A board built with `caps=()` and letter `s` is an OLD board: `DEV: s`, no
`#` handling at all. Its parser drops `#` and the letters after it, and
reads a digit as the start of an autonomous frame, so a `#LOG 2` would
reach it as a stop frame. That is the hazard the standard's "never a `#`
byte to a board that did not advertise ext1" rule exists for, and the tests
use this board to show the station never sends one.

Test hooks: trip or release a switch, move a home edge, power up parked on
a switch, go silent (a hung loop: no reply, no stream, while the step
pulses keep the axes moving), inject a driver fault, reboot, unplug, fail
writes, and mark any axis's hardware absent.

It is the contract as the station reads it, not the firmware: the HOME
sequence in particular (time-compressed here) is the firmware's to design,
and only its events are modelled.

`MegaStandardPort` is the link: a `SerialPort` whose "SIM" handle is the
simulator, so a SIM model runs the real transport. SIM has no handshake, so
the simulated board's identity answer stands in for it at `open()`. With a
real port name and a `simulator`, the simulator sits behind the full connect
path (open, identity query, loss, reconnect): tests use that; Setup never
does.
"""
import collections
import math
import re
import struct
import threading
import time

from devices.serial_port import SerialPort

#: The axes, in the order everything lists them.
AXES = ("X", "Y", "Z")
#: Every capability token the standard defines (section 2), in its order.
CAPS = ("ext1", "log", "hostto", "home", "limits", "tmc")
#: The XYZ Mega's identity letter (section 2). Taken: s, d, c, t, x.
LETTER = "m"
#: What INFO names: the sketch and the extension protocol.
FIRMWARE = "xyz_stage_mega"
PROTOCOL = "1"

#: The jog packet: start marker, mode, then ten floats (42 bytes).
JOG_FORMAT = "<BBffffffffff"
JOG_SIZE = struct.calcsize(JOG_FORMAT)
START_MARKER = 0xAA


def _atol(text):
    """Arduino `String.toInt()`: the leading integer, 0 when there is none."""
    found = re.match(r"\s*([+-]?\d+)", text)
    return int(found.group(1)) if found else 0


def _atof(text):
    """Arduino `String.toFloat()`: the leading number, 0.0 when none."""
    found = re.match(r"\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)", text)
    return float(found.group(1)) if found else 0.0


def _end(value):
    """A learned end as INFO and LIMIT print it: +1, -1, or 0."""
    return f"{value:+d}" if value else "0"


def _int16(value):
    """An AVR `int`: what `int x_steps = XAXIS_SIZE * XAXIS_DIST` keeps."""
    return ((int(value) + 32768) % 65536) - 32768


class _Axis:
    """One axis: the carriage, its switches and home edge (fixed in the
    stage's own frame, in which the board first booted at 0), its EEPROM
    configuration, and the firmware's per-axis state."""

    def __init__(self, name, limits, home_edge, *, limits_on, home_on, tmc):
        self.name = name
        #: Where LS1 (low) and LS2 (high) close, stage frame, counts.
        self.limits = tuple(limits)
        #: Where the home photo-interrupter's beam is broken from (>= edge),
        #: stage frame, counts; None: no beam anywhere (a failing sensor).
        self.home_edge = home_edge
        #: EEPROM (`#AXISCFG`): survives a reboot.
        self.cfg_limits = bool(limits_on)
        self.cfg_home = bool(home_on)
        #: The driver answered on the TMC bus at boot (auto-detected).
        self.tmc_fitted = bool(tmc)
        self.phys = 0.0               # the carriage, stage frame, counts
        self.forced = [False, False]  # a test holds a switch closed
        self.boot()

    def boot(self):
        """The firmware restarts: the counter starts at 0 where the carriage
        is, and every learned or session fact is forgotten."""
        self.tmc = self.tmc_fitted
        self.origin = self.phys
        self.v = 0.0                  # counts/s, signed
        self.target = None            # counter, int
        self.cruise = 0.0             # |counts/s| toward the target
        self.jog = None               # counts/s, signed (manual)
        self.homing = None            # {"phase", "dir", "speed", "started", "reversed"}
        self.home_was_low = False
        self.homed = False
        self.seen = False             # a switch was seen with limits=0 (session)
        self.ls_end = [0, 0]
        self.ls_firm = [False, False]
        self.ls_seen = [False, False]
        self.ls_lo = [self.phys - MegaStandardSim.PARKED_TRAVEL] * 2
        self.ls_hi = [self.phys + MegaStandardSim.PARKED_TRAVEL] * 2
        self.ls_was = [self.tripped(0), self.tripped(1)]

    @property
    def limits_active(self):
        return self.cfg_limits or self.seen

    @property
    def counter(self):
        return int(round(self.phys - self.origin))

    def tripped(self, index):
        low, high = self.limits
        if self.forced[index]:
            return True
        return self.phys <= low if index == 0 else self.phys >= high

    def parked(self, index):
        """Pressed with its end not confirmed (the interlock is live)."""
        return self.limits_active and self.tripped(index) and not self.ls_firm[index]

    def home_level(self):
        return 1 if self.home_edge is not None and self.phys >= self.home_edge else 0

    def busy(self):
        return (abs(self.v) > 0.0 or self.target is not None
                or self.homing is not None or bool(self.jog))

    def halt(self):
        self.v = 0.0
        self.target = None
        self.jog = None


class MegaStandardSim:
    """One standard Mega board, simulated (see the module docstring).

    Shaped like a pyserial handle, so `SerialPort` drives it as it drives a
    board. Time comes from `clock` (monotonic seconds); motion is integrated
    lazily, in small steps, whenever the host touches the handle or a test
    reads a property. Positions are counts (1600 per mm)."""

    #: POS every PRINT_INTERVAL (the sketch's 100 ms).
    PRINT_INTERVAL = 0.100
    #: The jog dead-man and the enable settle (stepper_firmware.ino).
    JOG_TIMEOUT = 0.250
    ENABLE_SETTLE = 0.030
    #: setMaxSpeed(1600 * microsteps / 2) at 8 microsteps, counts/s.
    MAX_SPEED = 6400.0
    MANUAL_DEAD_ZONE = 0.05
    #: packetIsValid's ceiling on the jog speed.
    MAX_JOG_SPEED = 6400.0
    #: `#HOSTTIMEOUT`'s range, ms (section 3).
    HOST_TIMEOUT_RANGE_MS = (250, 5000)
    #: Silence after `#EVT FAULT host-timeout` that drives EN high.
    HOST_SILENT_OFF = 10.0
    #: A parked switch: jogs only, at most this fast (0.1 mm/s)...
    PARKED_JOG_SPEED = 160.0
    #: ... and this far before the board learns that end (0.5 mm, PROVISIONAL).
    PARKED_TRAVEL = 800
    #: The simulated HOME, time-compressed (the real speeds are the owner's).
    HOME_SEEK = 3200.0
    HOME_APPROACH = 800.0
    HOME_BACKOFF = 320
    HOME_TIME_LIMIT = 120.0
    #: Integration step, and the most one catch-up integrates.
    STEP_S = 0.002
    MAX_CATCH_UP_S = 30.0
    #: Payloads kept in `writes`, lines in `received`.
    HISTORY = 20000

    def __init__(self, *, letter=LETTER, caps=CAPS, clock=time.monotonic,
                 limits=(-20000, 20000), home_edge=640, hardware=None):
        """`hardware` is {axis: {"limits": bool, "home": bool, "tmc": bool}}
        for any axis that differs from fully fitted (every feature present
        and configured)."""
        self.letter = str(letter)
        self.caps = tuple(caps)
        self.clock = clock
        hardware = hardware or {}
        self.axes = {}
        for name in AXES:
            fitted = dict({"limits": True, "home": True, "tmc": True},
                          **hardware.get(name, {}))
            self.axes[name] = _Axis(name, limits, home_edge,
                                    limits_on=fitted["limits"], home_on=fitted["home"],
                                    tmc=fitted["tmc"])
        self.is_open = False
        #: Every payload the host wrote, in order (bytes).
        self.writes = collections.deque(maxlen=self.HISTORY)
        #: Every line or packet the board took: (clock time, text).
        self.received = collections.deque(maxlen=self.HISTORY)
        #: Every line the parser read as an autonomous frame (on an old
        #: board, a digit inside a `#` line is one: a misread).
        self.frames = collections.deque(maxlen=self.HISTORY)
        #: True: the loop is hung. Nothing is read, nothing printed, and the
        #: loop's guards do not run; the step pulses keep the axes moving.
        self.silent = False
        #: True: the USB cable is out. Every call raises OSError.
        self.unplugged = False
        #: The next N writes raise OSError.
        self.fail_writes = 0
        self._lock = threading.RLock()
        self._out = bytearray()
        self._rx = bytearray()
        self._boot()

    # -- what it is ---------------------------------------------------------------
    @property
    def ext(self):
        return "ext1" in self.caps

    def has(self, token):
        return token in self.caps

    def identity_text(self):
        """What follows `DEV: ` in the identity reply."""
        if self.caps:
            return f"{self.letter} caps={','.join(self.caps)}"
        return self.letter

    def axis(self, name):
        return self.axes[str(name).upper()]

    # -- firmware state -------------------------------------------------------------
    def _boot(self):
        now = self.clock()
        self._t = now
        self.enabled = False
        self._settle_until = None
        self.manual_on = False
        self.auto_on = False
        self.dpad_step = False
        self.manual = [0.0, 0.0, 0.0]
        self.dpad = [0, 0, 0]          # dpad_LR, dpad_UD, bumpers
        self.step_sizes = [16, 16, 16]
        self.full_speed = 400.0
        self._last_packet = now
        self.host_timeout_ms = None    # armed by #HOSTTIMEOUT
        self._last_rx = now
        self._timed_out = False
        self._timed_out_at = None
        self.log_level = 1
        self.rejected_packets = 0
        self._next_print = now + self.PRINT_INTERVAL
        for axis in self.axes.values():
            axis.boot()
        if self.ext and self.has("tmc"):
            for axis in self.axes.values():
                if not axis.tmc:
                    self._emit(f"#EVT FAULT tmc-missing {axis.name}")

    # -- what a test reads (each catches the motion up first) ------------------------
    @property
    def position(self):
        with self._lock:
            self._advance()
            return tuple(self.axes[a].counter for a in AXES)

    @property
    def moving(self):
        with self._lock:
            self._advance()
            return self._busy()

    def homed(self, name):
        with self._lock:
            self._advance()
            return self.axis(name).homed

    def limit_end(self, name, number):
        """The end switch `number` of axis `name` guards, as learned: +1,
        -1, or 0 for not learned."""
        with self._lock:
            self._advance()
            return self.axis(name).ls_end[int(number) - 1]

    # -- injections -------------------------------------------------------------------
    def trip_limit(self, name, number, tripped=True):
        """Close (or with tripped=False, release) switch LS1 or LS2 of an axis."""
        with self._lock:
            self._advance()
            self.axis(name).forced[int(number) - 1] = bool(tripped)

    def release_limit(self, name, number):
        self.trip_limit(name, number, False)

    def move_carriage(self, name, counts):
        """Put the carriage at `counts` in the stage frame (a hand on it,
        with the drives off)."""
        with self._lock:
            self._advance()
            self.axis(name).phys = float(counts)

    def park_on(self, name, number, depth=160):
        """Power the board up with axis `name` on switch `number`: the
        carriage sits `depth` counts inside its pressed zone and the board
        reboots, so the switch reads pressed with its end unknown."""
        with self._lock:
            self._advance()
            axis = self.axis(name)
            low, high = axis.limits
            axis.phys = float(low - depth if int(number) == 1 else high + depth)
            self._boot()

    def set_home_edge(self, name, counts):
        """Move an axis's home edge (stage frame); None: no beam at all."""
        with self._lock:
            self._advance()
            self.axis(name).home_edge = counts

    def set_hardware(self, name, *, limits=None, home=None, tmc=None):
        """Mark hardware present or absent. `limits` and `home` are the
        EEPROM configuration (at once); `tmc` is what the driver bus will
        answer, read at the next boot (call `reboot()`)."""
        with self._lock:
            axis = self.axis(name)
            if limits is not None:
                axis.cfg_limits = bool(limits)
            if home is not None:
                axis.cfg_home = bool(home)
            if tmc is not None:
                axis.tmc_fitted = bool(tmc)

    def inject_fault(self, name, what="short"):
        """A driver fault read over the TMC bus: every axis stops, EN goes
        high, and the board says which."""
        with self._lock:
            self._advance()
            self._halt_motion("fault")
            self.enabled = False
            self._emit(f"#EVT FAULT {what} {str(name).upper()}")

    def reboot(self):
        """The board restarts (a watchdog reset, a brown-out, a DTR pulse)."""
        with self._lock:
            self._boot()

    def unplug(self):
        """The cable is out. As a real pyserial handle does, the handle still
        says it is open; every read and write raises."""
        with self._lock:
            self.unplugged = True

    def replug(self):
        """Back on USB: the board was reset."""
        with self._lock:
            self.unplugged = False
            self._out.clear()
            self._rx.clear()
            self._boot()

    def emit_raw(self, text):
        """Put one raw line on the board's output."""
        with self._lock:
            self._emit(text)

    def ask(self, line):
        """Test helper: send one text line, return every line printed since."""
        payload = line if isinstance(line, bytes) else (line + "\n").encode("ascii")
        self.write(payload)
        return self.drain_lines()

    def drain_lines(self):
        """Test helper: catch up, then take every whole line printed."""
        with self._lock:
            self._advance()
            data = bytes(self._out)
            cut = data.rfind(b"\n") + 1
            del self._out[:cut]
        return [line.decode("utf-8", "replace") for line in data[:cut].split(b"\n")[:-1]]

    # -- the pyserial surface -------------------------------------------------------
    def _check(self):
        if self.unplugged:
            raise OSError(6, "Device not configured")

    def open(self):
        """Opening a Mega's port pulses DTR, which resets it."""
        with self._lock:
            self._check()
            self._advance()
            self._out.clear()
            self._rx.clear()
            self._boot()
            self.is_open = True

    def close(self):
        with self._lock:
            if self.is_open and not self.unplugged:
                self._advance()
            self.is_open = False

    @property
    def in_waiting(self):
        with self._lock:
            self._check()
            self._advance()
            return len(self._out)

    def read(self, size=1):
        with self._lock:
            self._check()
            self._advance()
            taken = bytes(self._out[:size])
            del self._out[:size]
            return taken

    def read_all(self):
        return self.read(len(self._out) or 0)

    def readline(self):
        with self._lock:
            self._check()
            self._advance()
            cut = self._out.find(b"\n") + 1
            taken = bytes(self._out[:cut])
            del self._out[:cut]
            return taken

    def write(self, payload):
        with self._lock:
            self._check()
            if self.fail_writes:
                self.fail_writes -= 1
                raise OSError(5, "Input/output error")
            payload = bytes(payload)
            self.writes.append(payload)
            self._advance()
            self._rx += payload
            if not self.silent:
                self._parse()
            return len(payload)

    def reset_input_buffer(self):
        with self._lock:
            self._out.clear()

    def reset_output_buffer(self):
        pass

    def flush(self):
        """Nothing is in flight (SERIAL-20)."""

    # -- output ---------------------------------------------------------------------
    def _emit(self, text):
        if not self.silent:
            self._out += (text + "\n").encode("utf-8")

    def _evt(self, text):
        if self.ext:
            self._emit(f"#EVT {text}")

    def _dbg(self, text):
        if self.ext and self.has("log") and self.log_level >= 2:
            self._emit(f"#EVT DBG {text}")

    def _ok(self, command, detail=""):
        self._emit(f"#OK {command}" + (f" {detail}" if detail else ""))

    def _err(self, command, reason):
        self._emit(f"#ERR {command} {reason}")

    # -- the parser (parseHybridSerial) -----------------------------------------------
    def _parse(self):
        """Every byte the host sent, in order, as the sketch's dispatch reads
        them. Any byte at all is host activity (section 4)."""
        if self._rx:
            self._last_rx = self.clock()
            self._timed_out = False
        while self._rx:
            head = self._rx[0]
            if head == START_MARKER:
                if len(self._rx) < JOG_SIZE:
                    return                       # the rest is on its way
                packet = bytes(self._rx[:JOG_SIZE])
                del self._rx[:JOG_SIZE]
                self._packet(packet)
            elif head == ord("d"):
                del self._rx[:1]
                self.received.append((self.clock(), "d"))
                self.enabled = False
                self._halt_motion("disabled")
                self._dbg("enabled=0")
            elif head == ord("e"):
                del self._rx[:1]
                self.received.append((self.clock(), "e"))
                if not self.enabled:
                    self.enabled = True
                    self._halt_axes()
                    self._settle_until = self.clock() + self.ENABLE_SETTLE
                    self._dbg("enabled=1")
            elif head == ord("s"):
                del self._rx[:1]
                self.received.append((self.clock(), "s"))
                self._emit(f"DEV: {self.identity_text()}")
            elif head == ord("-") or ord("0") <= head <= ord("9"):
                line = self._take_line()
                if line is None:
                    return
                self.received.append((self.clock(), line))
                self._frame(line)
            elif head == ord("#") and self.ext:
                line = self._take_line()
                if line is None:
                    return
                self.received.append((self.clock(), line))
                self._extension(line[1:].strip())
            else:
                del self._rx[:1]                 # "clear buffer": an unknown byte

    def _take_line(self):
        cut = self._rx.find(b"\n")
        if cut < 0:
            return None
        raw = bytes(self._rx[:cut])
        del self._rx[:cut + 1]
        return raw.decode("ascii", "replace").strip()

    # -- the frame protocol -------------------------------------------------------------
    def _packet(self, packet):
        fields = struct.unpack(JOG_FORMAT, packet)
        mode = fields[1]
        (x, y, z, xs, ys, zs, lr, ud, bumpers, speed) = fields[2:]
        now = self.clock()
        if mode == 1 and not self._packet_is_valid(fields[2:]):
            self.rejected_packets += 1
            mode = 255
        if mode in (0, 1):
            self._last_packet = now
        self.received.append((now, f"jog mode={mode}"))
        if mode == 1:
            if not self.manual_on and not self.dpad_step:
                self.auto_on = False
                self.manual_on = True
                self._halt_axes()
            self.full_speed = speed
            self.manual = [-x, y, z]
            self.dpad = [int(lr), int(ud), int(bumpers)]
            self.step_sizes = [int(xs), int(ys), int(zs)]
        elif mode == 0:
            self._halt_motion("stop")

    @staticmethod
    def _packet_is_valid(values):
        x, y, z, xs, ys, zs, lr, ud, bumpers, speed = values

        def unit(v):
            return math.isfinite(v) and -1.0001 <= v <= 1.0001

        def step(v):
            return math.isfinite(v) and 1.0 <= v <= 100000.0 and v == float(int(v))

        def tri(v):
            return v in (-1.0, 0.0, 1.0)

        return (unit(x) and unit(y) and unit(z) and step(xs) and step(ys) and step(zs)
                and tri(lr) and tri(ud) and tri(bumpers)
                and math.isfinite(speed) and 0.0 <= speed <= MegaStandardSim.MAX_JOG_SPEED)

    def _frame(self, line):
        """parseSerialAuto: one 12-field text frame."""
        fields = line.split(",")

        def field(i):
            return fields[i] if i < len(fields) else ""

        manual, auto = _atol(field(10)) == 1, _atol(field(11)) == 1
        self.frames.append(line)
        if manual:
            self.manual_on, self.auto_on = True, False
            self._halt_axes()
            self.full_speed = _atof(field(4))
            self.manual = [_atof(field(0)), _atof(field(1)), _atof(field(2))]
            return
        if not auto:
            self._halt_motion("stop")
            return
        sizes = [_atol(field(i)) for i in (0, 1, 2)]
        full = _atof(field(4))
        dists = [-_atol(field(7)), _atol(field(8)), -_atol(field(9))]
        steps = [_int16(size * dist) for size, dist in zip(sizes, dists)]
        length = math.sqrt(sum(d * d for d in dists))
        speeds = [int(full * d / length) if length > 0 else 0 for d in dists]
        speeds = [max(-self.MAX_SPEED, min(self.MAX_SPEED, s)) for s in speeds]
        if self.ext:
            refused = []
            for name, count, speed in zip(AXES, steps, speeds):
                if not count or not speed:
                    continue
                why = self._move_block(self.axes[name], 1 if count > 0 else -1)
                if why:
                    refused.append((name, why))
            if refused:
                for name, why in refused:
                    self._evt(f"REFUSED {name} reason={why}")
                return
        self.auto_on, self.manual_on = True, False
        self.manual = [0.0, 0.0, 0.0]
        for name, count, speed in zip(AXES, steps, speeds):
            axis = self.axes[name]
            axis.jog = None
            if count:
                axis.target = axis.counter + count
                axis.cruise = abs(speed)
        self._dbg(f"frame steps={steps[0]},{steps[1]},{steps[2]} speed={full:g}")

    def _halt_axes(self):
        for axis in self.axes.values():
            axis.halt()

    def _halt_motion(self, why):
        """haltMotion(): every axis stops where it is; a HOME ends."""
        self.manual_on = self.auto_on = self.dpad_step = False
        self.manual = [0.0, 0.0, 0.0]
        self.dpad = [0, 0, 0]
        for axis in self.axes.values():
            self._abort_home(axis, why)
            axis.halt()

    def _abort_home(self, axis, why):
        if axis.homing is not None:
            axis.homing = None
            self._evt(f"HOME FAIL {axis.name} reason={why}")

    # -- the extension channel ------------------------------------------------------------
    def _extension(self, text):
        words = text.split()
        if not words:
            return
        command = words[0].upper()
        args = words[1:]
        if command != "HB":
            self._dbg(f"rx={text}")
        handler = getattr(self, "_x_" + command.lower(), None)
        if handler is None or not command.isalpha():
            self._err(command, "unknown-command")
            return
        handler(command, args)

    def _axis_arg(self, args):
        if not args or args[0].upper() not in AXES:
            return None
        return self.axes[args[0].upper()]

    def _x_info(self, command, args):
        words = [f"fw={FIRMWARE}", f"proto={PROTOCOL}", f"caps={','.join(self.caps)}",
                 "axes=" + "".join(AXES)]
        for name in AXES:
            axis = self.axes[name]
            low = name.lower()
            words += [f"{low}_tmc={int(axis.tmc)}", f"{low}_limits={int(axis.limits_active)}",
                      f"{low}_home={int(axis.cfg_home)}"]
        for name in AXES:
            axis = self.axes[name]
            low = name.lower()
            words += [f"{low}_ls1_end={_end(axis.ls_end[0])}",
                      f"{low}_ls2_end={_end(axis.ls_end[1])}"]
        for name in AXES:
            words.append(f"{name.lower()}_homed={int(self.axes[name].homed)}")
        words += [f"log={self.log_level}", f"hostto_ms={self.host_timeout_ms or 0}"]
        self._ok(command, " ".join(words))

    def _x_axiscfg(self, command, args):
        axis = self._axis_arg(args)
        found = dict(w.split("=", 1) for w in args[1:] if "=" in w)
        values = {}
        for key in ("limits", "home"):
            if key in found:
                if found[key] not in ("0", "1"):
                    return self._err(command, "bad-arg")
                values[key] = found[key] == "1"
        if axis is None or not values:
            return self._err(command, "bad-arg")
        if self.enabled or self._busy():
            return self._err(command, "busy")
        if "limits" in values and values["limits"] != axis.cfg_limits:
            axis.cfg_limits = values["limits"]
            axis.ls_end = [0, 0]                 # a new meaning: forget the ends
            axis.ls_firm = [False, False]
            axis.ls_seen = [False, False]
        if "home" in values:
            axis.cfg_home = values["home"]
        self._ok(command, f"axis={axis.name} limits={int(axis.cfg_limits)} "
                          f"home={int(axis.cfg_home)} stored=1")

    def _x_log(self, command, args):
        value = _atol(args[0]) if args and args[0].lstrip("+-").isdigit() else None
        if value is None or not 0 <= value <= 2:
            return self._err(command, "bad-arg")
        self.log_level = value
        self._ok(command, f"level={value}")

    def _x_hosttimeout(self, command, args):
        value = _atol(args[0]) if args and args[0].isdigit() else None
        low, high = self.HOST_TIMEOUT_RANGE_MS
        if value is None or not low <= value <= high:
            return self._err(command, "bad-arg")
        self.host_timeout_ms = value
        self._ok(command, f"ms={value}")

    def _x_hb(self, command, args):
        self._ok(command)

    def _x_stop(self, command, args):
        self._halt_motion("stop")
        self._ok(command)

    def _x_zero(self, command, args):
        axis = self._axis_arg(args)
        if axis is None:
            return self._err(command, "bad-arg")
        if axis.busy():
            return self._err(command, "busy")
        axis.origin = axis.phys
        axis.homed = False
        self._ok(command, f"axis={axis.name}")

    def _x_home(self, command, args):
        axis = self._axis_arg(args)
        if axis is None:
            return self._err(command, "bad-arg")
        if not self.has("home"):
            return self._err(command, "unknown-command")
        if not self.enabled or not axis.tmc:
            return self._err(command, "not-enabled")
        if self._busy() or self.manual_on or self.dpad_step:
            return self._err(command, "busy")
        if not axis.cfg_home:
            return self._err(command, "no-home-sensor")
        if axis.limits_active:
            if axis.tripped(0) and axis.tripped(1):
                return self._err(command, "limits-both-tripped:check-wiring-or-LIMITS-NC|NO")
            for index in (0, 1):
                if axis.parked(index):
                    return self._err(command, f"limit-ls{index + 1}-end-unknown:jog-off-it")
        axis.homing = {"phase": "seek", "dir": -1, "speed": self.HOME_SEEK,
                       "started": self.clock(), "reversed": False}
        self._ok(command, "started")
        self._evt(f"HOME {axis.name} phase=seek dir=-1")

    # -- the per-axis safety rules ------------------------------------------------------
    def _move_block(self, axis, direction, jog=False):
        """Why motion of `axis` toward `direction` is refused, or None. Only
        a jog may move an axis whose switch is parked."""
        if not axis.tmc:
            return "tmc-missing"
        if not (self.has("limits") and axis.limits_active):
            return None
        tripped = [axis.tripped(0), axis.tripped(1)]
        if all(tripped):
            return "limits-both-tripped:check-wiring-or-LIMITS-NC|NO"
        for index in (0, 1):
            if not tripped[index]:
                continue
            name = f"ls{index + 1}"
            if axis.ls_end[index] == direction:
                return f"limit-{name}"
            if not axis.parked(index):
                continue
            if not jog:
                return f"limit-{name}-end-unknown:jog-off-it"
            spent = (axis.phys >= axis.ls_hi[index] - 1e-9 if direction > 0
                     else axis.phys <= axis.ls_lo[index] + 1e-9)
            if spent:
                return f"limit-{name}-pressed-both-ways:check-switch"
        return None

    def _jog_velocity(self, axis, wanted):
        """A jog's velocity after the interlock: blocked, or capped while a
        switch of that axis is parked."""
        if not wanted:
            return 0.0
        if self._move_block(axis, 1 if wanted > 0 else -1, jog=True):
            return 0.0
        if axis.parked(0) or axis.parked(1):
            return math.copysign(min(abs(wanted), self.PARKED_JOG_SPEED), wanted)
        return wanted

    # -- time ----------------------------------------------------------------------------
    def _busy(self):
        return any(axis.busy() for axis in self.axes.values())

    def _settling(self, t):
        return self._settle_until is not None and t < self._settle_until

    def _advance(self):
        now = self.clock()
        if now <= self._t:
            return
        span = min(now - self._t, self.MAX_CATCH_UP_S)
        t = self._t
        while span > 1e-12:
            dt = min(self.STEP_S, span)
            span -= dt
            t += dt
            self._tick(t, dt)
        self._t = now

    def _tick(self, t, dt):
        if not self.silent:
            self._loop_guards(t)
            if not self._settling(t):
                self._run_modes()
        for axis in self.axes.values():
            self._isr(axis, t, dt)
        if not self.silent:
            for axis in self.axes.values():
                self._home_step(axis, t)
            self._finish_moves()
            if t >= self._next_print:
                self._next_print += self.PRINT_INTERVAL
                if self._next_print < t:
                    self._next_print = t + self.PRINT_INTERVAL
                self._emit("POS:" + ",".join(str(self.axes[a].counter) for a in AXES))

    def _loop_guards(self, t):
        if (self.manual_on or self.dpad_step) and t - self._last_packet > self.JOG_TIMEOUT:
            # The jog dead-man: motion stops, the coils hold, manual stays on.
            self.manual = [0.0, 0.0, 0.0]
            self.dpad = [0, 0, 0]
            self.dpad_step = False
            self.manual_on = True
            self._halt_axes()
        if not (self.ext and self.has("hostto")) or self.host_timeout_ms is None:
            return
        if (self._busy() and not self._timed_out
                and (t - self._last_rx) * 1000 > self.host_timeout_ms):
            self._timed_out = True
            self._timed_out_at = t
            self._halt_motion("host-timeout")
            self._evt("FAULT host-timeout")
        if (self._timed_out and self.enabled
                and t - self._timed_out_at >= self.HOST_SILENT_OFF):
            self.enabled = False
            self._halt_motion("host-timeout")
            self._evt(f"FAULT host-timeout-disabled silent_ms={int((t - self._last_rx) * 1000)}")

    def _run_modes(self):
        """The loop's mode handlers: manual jog velocities and the D-pad step
        (autonomous moves are targets, run by the step ISR)."""
        if not self.manual_on:
            return
        size = math.sqrt(sum(m * m for m in self.manual))
        for index, name in enumerate(AXES):
            axis = self.axes[name]
            if axis.homing is not None:
                continue
            wanted = 0.0
            if size > self.MANUAL_DEAD_ZONE:
                wanted = max(-self.MAX_SPEED, min(self.MAX_SPEED,
                                                  self.manual[index] * self.full_speed))
            axis.target = None
            axis.jog = self._jog_velocity(axis, wanted) or None
        if any(self.dpad):
            self.dpad_step, self.manual_on = True, False
            for index, name in enumerate(AXES):
                axis = self.axes[name]
                axis.jog = None
                count = self.dpad[index] * self.step_sizes[index]
                if count and not self._move_block(axis, 1 if count > 0 else -1, jog=True):
                    axis.target = axis.counter + count
                    speed = min(abs(self.full_speed), self.MAX_SPEED)
                    if axis.parked(0) or axis.parked(1):
                        speed = min(speed, self.PARKED_JOG_SPEED)
                    axis.cruise = speed

    def _finish_moves(self):
        if self.dpad_step and not any(a.target is not None for a in self.axes.values()):
            self.dpad_step = False
            self.manual_on = True

    def _desired(self, axis, t):
        if not self.enabled or not axis.tmc or self._settling(t):
            return 0.0
        if axis.homing is not None:
            return axis.homing["dir"] * axis.homing["speed"]
        if axis.target is not None:
            remaining = axis.target - (axis.phys - axis.origin)
            if abs(remaining) < 1e-9 or not axis.cruise:
                return 0.0
            return math.copysign(axis.cruise, remaining)
        return axis.jog or 0.0

    def _isr(self, axis, t, dt):
        if not self.enabled or not axis.tmc:
            axis.v = 0.0
            axis.target = None
            axis.jog = None
        axis.v = self._desired(axis, t)
        before = axis.phys - axis.origin
        axis.phys += axis.v * dt
        if axis.target is not None:
            after = axis.phys - axis.origin
            if (axis.target - before) * (axis.target - after) <= 0 and axis.cruise:
                axis.phys = axis.origin + axis.target
                axis.v = 0.0
                axis.target = None
                self._dbg(f"done {axis.name} pos={axis.counter}")
        if self.has("limits"):
            self._limit_isr(axis, (axis.v > 0) - (axis.v < 0))

    def _limit_isr(self, axis, direction):
        """The interlock, per axis, once per tick, with the direction of the
        motion just made: learns each switch's end, bounds the parked
        travel, and halts that axis; each halt is one #EVT LIMIT."""
        tripped = [axis.tripped(0), axis.tripped(1)]
        if not axis.limits_active:
            new = [tripped[i] and not axis.ls_was[i] for i in (0, 1)]
            if not (direction and any(new)):
                axis.ls_was = tripped
                return
            axis.seen = True                       # seen once: live for the session
            for i in (0, 1):
                if new[i]:
                    self._evt(f"LIMIT {axis.name} ls{i + 1} seen=1")
        halt = direction != 0 and tripped[0] and tripped[1]
        travel = [False, False]
        both = [False, False]
        for i in (0, 1):
            now, was = tripped[i], axis.ls_was[i]
            new_trip, new_clear = now and not was, was and not now
            if not axis.ls_firm[i]:
                if direction and new_clear:                # left it: it guards -dir
                    axis.ls_end[i], axis.ls_firm[i] = -direction, True
                elif (direction and new_trip and not axis.ls_seen[i]
                      and axis.ls_end[i] == 0):            # reached from clear: it guards dir
                    axis.ls_end[i], axis.ls_firm[i] = direction, True
                elif new_trip:                             # pressed again, end not firm
                    axis.ls_lo[i] = axis.phys - self.PARKED_TRAVEL
                    axis.ls_hi[i] = axis.phys + self.PARKED_TRAVEL
                    halt = halt or direction != 0
                elif now and direction and (
                        axis.phys >= axis.ls_hi[i] - 1e-9 if direction > 0
                        else axis.phys <= axis.ls_lo[i] + 1e-9):
                    # The parked travel is spent, still pressed: this way drives into it.
                    axis.phys = axis.ls_hi[i] if direction > 0 else axis.ls_lo[i]
                    halt = True
                    if axis.ls_end[i] == 0:
                        axis.ls_end[i] = direction
                        travel[i] = True
                    elif axis.ls_end[i] != direction:
                        both[i] = True
                if now and not axis.ls_firm[i]:
                    axis.ls_seen[i] = True
            if now and direction and axis.ls_end[i] == direction:
                halt = True
            axis.ls_was[i] = now
        if not halt:
            return
        axis.v = 0.0
        axis.target = None
        axis.jog = None
        for i in (0, 1):
            if not tripped[i]:
                continue
            why = ""
            if travel[i]:
                why = " learned=travel"
            elif both[i]:
                why = " pressed_both_ways=1"
            self._evt(f"LIMIT {axis.name} ls{i + 1} pos={axis.counter} "
                      f"end={_end(axis.ls_end[i])}{why}")

    def _home_step(self, axis, t):
        homing = axis.homing
        if homing is None:
            return
        if t - homing["started"] > self.HOME_TIME_LIMIT:
            return self._home_failed(axis, "timeout")
        edge = axis.home_edge
        phase = homing["phase"]
        low_hit, high_hit = axis.tripped(0), axis.tripped(1)
        if phase == "seek":
            if (edge is not None and axis.phys <= edge - self.HOME_BACKOFF
                    and axis.home_level() == 0):
                self._home_phase(axis, "approach", 1, self.HOME_APPROACH)
            elif low_hit:
                if homing["reversed"]:
                    return self._home_failed(axis, "no-edge")
                homing["reversed"] = True
                self._home_phase(axis, "search", 1, self.HOME_SEEK)
        elif phase == "search":
            if axis.home_level() == 1 and axis.home_was_low:
                self._home_phase(axis, "seek", -1, self.HOME_SEEK)
            elif high_hit:
                return self._home_failed(axis, "no-edge")
            axis.home_was_low = axis.home_was_low or axis.home_level() == 0
        elif phase == "approach":
            if edge is not None and axis.phys >= edge:
                before = int(round(edge - axis.origin))
                axis.phys = float(edge)              # latched at the edge
                axis.origin = float(edge)
                axis.v = 0.0
                axis.homing = None
                axis.homed = True
                self._evt(f"HOMED {axis.name} edge={before} pos=0")
            elif high_hit:
                return self._home_failed(axis, "no-edge")

    def _home_phase(self, axis, phase, direction, speed):
        axis.homing.update(phase=phase, dir=direction, speed=speed)
        if phase == "search":
            axis.home_was_low = axis.home_level() == 0
        self._evt(f"HOME {axis.name} phase={phase} dir={direction:+d}")

    def _home_failed(self, axis, reason):
        axis.homing = None
        axis.v = 0.0
        self._evt(f"HOME FAIL {axis.name} reason={reason}")


class MegaStandardPort(SerialPort):
    """The serial link of a standard Mega board (see the module docstring).

        MegaStandardPort(port, *, simulator=None, owner=None)

    `port` is a port name, or "SIM"/None for the built-in simulator."""

    #: The Probe family's line speed (`Probe.BAUD_RATE`).
    BAUD_RATE = 500000

    def __init__(self, port, *, simulator=None, owner=None):
        super().__init__(port, baud_rate=self.BAUD_RATE, owner=owner)
        self._simulator = simulator
        if self.is_simulated:
            if self._simulator is None:
                self._simulator = MegaStandardSim()
            # SerialPort built a SimulatedPort; the protocol simulator takes
            # its place, closed until `open()` opens it.
            self._handle = self._simulator
            self._handle.close()

    @property
    def simulator(self):
        """The simulator behind this link, or None for a real board."""
        return self._simulator

    def _open_handle(self):
        if self._simulator is not None and not self.is_simulated:
            self._simulator.open()       # raises while "unplugged"
            return self._simulator
        return super()._open_handle()

    def open(self):
        """SIM has no handshake (`SerialPort.open`), so the simulated board's
        own answer to `s` is its identity, without a byte on the wire."""
        super().open()
        if self.is_simulated and self._simulator is not None:
            with self._state_lock:
                self._identity = self._simulator.identity_text()
