# --------- Necessary Libraries ----------
import time
import math
from error_routing import ErrorRouter as ErrorPopupManager
try:
    import serial as pyserial
except ImportError:
    pyserial = None
import sys
import struct
import threading

class ConnectionState:
    """What the transport actually knows about the link (RC-5 item 3).

    One vocabulary, shared by every view and the web badge, replacing three
    different guesses. The web badge in particular inferred "simulated" from
    the *editable* serial_port field, so typing "SIM" into a hardware probe's
    port box relabelled it as simulated while it kept driving real hardware
    (DC-13).

    The distinction that matters most is VERIFIED vs UNVERIFIED. Opening a
    port proves nothing: the old code logged "Operating blind" and then
    treated the link as good, so a cable into a powered-off board looked
    identical to a working one (SERIAL-7).
    """

    SIMULATED = "simulated"    # no hardware by design, and that is fine
    CONNECTING = "connecting"  # open in progress
    VERIFIED = "verified"      # opened AND the board answered
    UNVERIFIED = "unverified"  # opened, but nothing answered — operating blind
    LOST = "lost"              # it answered once and then failed
    CLOSED = "closed"          # deliberately closed

    #: States in which a command has any prospect of arriving.
    USABLE = (SIMULATED, VERIFIED, UNVERIFIED)


class SimulatedPort:
    """A stand-in for a pyserial handle that acknowledges everything (RC-2 item 4).

    Simulator mode used to be expressed as `ser = None` plus a
    `if SERIAL_PORT in ('SIM', ...): return` early-exit in every method. That
    made SIM a *different code path* rather than a different device, so the
    paths the bench exercises headlessly were not the paths it runs with
    hardware attached — and each new method had to remember to add its own
    early exit, which is how SERIAL-9 happened (`enable()` forgot, and every
    SIM probe became permanently un-armable).

    This ACKs instead: writes succeed, reads return nothing, and the port
    reports itself open. The transport above it then takes exactly one route
    whether or not a board is plugged in.
    """

    def __init__(self):
        self.is_open = True
        self.in_waiting = 0
        self.writes = []

    def write(self, payload):
        self.writes.append(payload)
        return len(payload)

    def read(self, _size=1):
        return b""

    def readline(self):
        return b""

    def reset_input_buffer(self):
        pass

    def reset_output_buffer(self):
        pass

    def flush(self):
        """Nothing is in flight, so a drain is instantly complete.

        It did not answer this at all, and `send_manual_mode_command` calls
        it on every frame — so *every* manual-mode frame in simulator mode
        raised AttributeError inside the write path and was reported to the
        operator as a "Serial Write Error". The same shape as SERIAL-9: a
        method the simulated port forgot, turning SIM back into a different
        code path, which is the one thing this class exists to prevent.
        """

    def close(self):
        self.is_open = False


class TransportError(Exception):
    """A command did not reach the hardware (RC-2).

    Raised by `write_command`. The point of it existing is that the previous
    code swallowed every write exception and returned normally, so a caller
    had no way to distinguish "the coils are disabled" from "the disable
    command never left the process". Models must treat this as *unknown
    hardware state*, never as success.
    """


# Updated to 42-byte format (2 bytes + 10 floats) to match unified firmware struct
PACKET_FORMAT = '<BBffffffffff'
START_MARKER = 0xAA

# ------ Serial Simulation Setup --------

# Dedicated class for serial communication with Arduino
class serial:

    # Class constructor
    def __init__(self, port='SIM', baud_rate=500000):

        # Communation Rate
        self.BAUD_RATE = baud_rate

        # Port Name
        self.SERIAL_PORT = port

        # Empty serial object
        self.ser = None

        # What we actually know about the link (RC-5 item 3).
        self.connection_state = ConnectionState.CONNECTING

        # Threading lock for thread-safe serial port access
        self._lock = threading.RLock()

        # NEW: Buffer for incoming serial data from firmware
        self._read_buffer = ""
        self.device_type = None

        if self.SERIAL_PORT in ('SIM', 'None', None):
            msg = "[SerialDrive] Running in SIMULATOR mode. Commands are acknowledged locally."
            print(msg)
            ErrorPopupManager.report_info("Simulator Mode", msg)
            # An explicit simulated port, not `ser = None` (RC-2 item 4), so
            # every method below takes one route regardless of hardware.
            self.ser = SimulatedPort()
            self.connection_state = ConnectionState.SIMULATED
            return
        
        try:
            print("[SerialDrive] Establishing Serial Connection...")
            self.ser = pyserial.Serial(
                self.SERIAL_PORT,
                self.BAUD_RATE,
                timeout=1,
                write_timeout=1
            )

            # Wait for the Arduino to boot, then ask it what it is.
            print(f"[SerialDrive] Pinging port {self.SERIAL_PORT} to verify connection...")

            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()

            time.sleep(self.BOOTLOADER_WAIT)
            verified = self._handshake()

            if verified:
                self.connection_state = ConnectionState.VERIFIED
                print(f"[SerialDrive] Serial Connection Verified! Arduino Ready on {self.SERIAL_PORT}")
            else:
                self.connection_state = ConnectionState.UNVERIFIED
                msg = f"[WARNING] Port {self.SERIAL_PORT} opened, but no Arduino response received. Operating blind."
                print(msg)
                ErrorPopupManager.report_warning("Serial Connection Warning", msg)
        
        # Exception handling
        except pyserial.SerialException as e:
            self.connection_state = ConnectionState.LOST
            msg = f"[WARNING] Error establishing serial connection to {self.SERIAL_PORT}:\n{e}\nOperating blind without hardware."
            print(msg)
            ErrorPopupManager.report_warning("Serial Exception", msg, e)
        except Exception as e:
            msg = f"[WARNING] Unexpected error:\n{e}\nOperating blind without hardware."
            print(msg)
            ErrorPopupManager.report_error("Unexpected Serial Error", msg, e)
        finally:
            print("[SerialDrive] Finish SerialDrive __init__")

    # --- identity handshake (SERIAL-17) -------------------------------
    #
    #: How long to give the bootloader before the first ping.
    BOOTLOADER_WAIT = 1.5
    #: Seconds between identity pings. The loop used to write `s\n` on every
    #: pass of a 50 ms poll — ~20 per second — and kept writing after the
    #: board had already answered the previous one. Every `s` is answered, so
    #: a verified link started life with a queue of unread `DEV:` replies
    #: that `read_position` then skipped past for the rest of the session.
    PING_INTERVAL = 0.25
    #: How long to keep asking before declaring the link UNVERIFIED.
    HANDSHAKE_TIMEOUT = 3.0
    #: How often to look for a reply between pings.
    _HANDSHAKE_POLL = 0.05

    def _handshake(self):
        """Ask the board what it is. True if it answered; sets `device_type`.

        Three things this is careful about, all SERIAL-17:

        * **One ping every `PING_INTERVAL`**, not one per poll, and none at
          all once the board has answered.
        * **A whole line, or nothing.** The old break condition was
          `"DEV:" in buffer`, satisfied the instant those four bytes land —
          `device_type` was then parsed off a line that had not finished
          arriving and came out `""` or truncated. Harmless while nothing
          reads it, wrong the moment it is used to validate the device
          (SERIAL-7), which is exactly the sort of "negligible today" that
          turns into a mis-identified board later.
        * **Drain after the match**, not only before the bootloader wait, so
          the replies to the pings that were already in flight are not left
          for `read_position` to wade through.
        """
        deadline = time.time() + self.HANDSHAKE_TIMEOUT
        buffer = ""
        next_ping = 0.0
        while time.time() < deadline:
            now = time.time()
            if now >= next_ping:
                try:
                    self.ser.write(b"s\n")
                except Exception:
                    return False
                next_ping = now + self.PING_INTERVAL

            try:
                waiting = self.ser.in_waiting
                if waiting > 0:
                    buffer += self.ser.read(waiting).decode(
                        'utf-8', errors='ignore')
            except Exception:
                return False

            device = self._device_from(buffer)
            if device is not None:
                self.device_type = device
                try:
                    self.ser.reset_input_buffer()
                except Exception:
                    pass
                return True

            time.sleep(self._HANDSHAKE_POLL)
        return False

    @staticmethod
    def _device_from(buffer):
        """The device id from a **complete** `DEV:` line, or None.

        The last element of the split is whatever has arrived since the final
        newline — a partial line by definition — and is deliberately not
        considered.
        """
        if '\n' not in buffer:
            return None
        for line in buffer.split('\n')[:-1]:
            if "DEV:" in line:
                device = line.split("DEV:")[1].strip()
                if device:
                    return device
        return None

    # Helper to verify serial connection before sending data
    def _verify_serial(self, verbose=False):
        if self.ser is None or not self.ser.is_open:
            if verbose and self.SERIAL_PORT != 'SIM':
                msg = "[SerialDrive] Error: Serial connection not established."
                print(msg)
                ErrorPopupManager.report_warning("Serial Disconnected", msg)
            return False
        return True

    # NEW: Read and parse absolute position data sent by firmware ("POS:x,y,z\n").
    #      Returns the latest (x, y, z) tuple from boot-time zero, or None if no new data.
    def read_position(self):
        if not self._verify_serial(verbose=False):
            return None

        try:
            with self._lock:
                # Read all available bytes into the buffer without blocking
                if self.ser.in_waiting > 0:                                                     # type: ignore
                    try:
                        raw = self.ser.read(self.ser.in_waiting).decode('utf-8', errors='ignore')
                    except Exception as e:
                        ErrorPopupManager.report_error("Serial Read Error", f"[SerialDrive] Error reading position:\n{e}", e)
                        return None
                    self._read_buffer += raw

                # Safety: prevent unbounded buffer growth if newlines are ever missed
                if len(self._read_buffer) > 1024:
                    self._read_buffer = self._read_buffer[-512:]

                # Process all complete lines, keep only the latest POS reading
                latest_pos = None
                while '\n' in self._read_buffer:
                    line, self._read_buffer = self._read_buffer.split('\n', 1)
                    line = line.strip()
                    if line.startswith("POS:"):
                        try:
                            parts = line[4:].split(',')
                            if len(parts) == 3:
                                latest_pos = (int(parts[0]), int(parts[1]), int(parts[2]))
                        except (ValueError, IndexError):
                            pass  # Malformed line, skip

            return latest_pos

        except Exception as e:
            ErrorPopupManager.report_error("Serial Read Error", f"[SerialDrive] Error reading position:\n{e}", e)
            return None

    # Function to send autonomous command
    def send_autonomous_command(self, params):
        
        # Port not open, do nothing
        if not self._verify_serial(verbose=True):
            return

        try:
            COMMAND_CODE_AUTON = params['command_code_auton']
            COMMAND_CODE_MANUAL = params['command_code_manual']
            empty_data = "0"  # Placeholder for unused target_steps

            # Construct f string for 12-field command
            command = (
                f"{params['x_step_size']},{params['y_step_size']},{params['z_step_size']},"
                f"{empty_data}," 
                f"{params['full_speed']},{params['slow_speed']},{params['brake_distance']},"
                f"{params['x_dist']},{params['y_dist']},{params['z_dist']},"
                f"{COMMAND_CODE_MANUAL},{COMMAND_CODE_AUTON}\n"
            )

            print(f"[SerialDrive] Sending 12-Field AUTON Command: {command.strip()}")
            with self._lock:
                self.ser.write(command.encode('utf-8'))    # type: ignore

        # Exception handling
        except pyserial.SerialTimeoutException as e:
            msg = "[SerialDrive] WRITE TIMEOUT ERROR (Auton)\nThe serial write operation timed out."
            print(msg)
            ErrorPopupManager.report_error("Serial Write Timeout", msg, e)
        except Exception as e:
            msg = f"[SerialDrive] Error sending auton data:\n{e}"
            print(msg)
            ErrorPopupManager.report_error("Serial Write Error", msg, e)

    # Function to send manual command, looped by manual mode loop
    def send_manual_mode_command(self, params):
        
        # Do nothing if the serial port is closed
        if not self._verify_serial(verbose=True):
            return
        try:
            # Build data for z direction triggers
            # Get raw trigger values (assuming idle is -1)
            z_trigger_l_raw = params.get('z_axisStatusL', -1.0)
            z_trigger_r_raw = params.get('z_axisStatusR', -1.0)

            # Remap from [-1, 1] to [0, 1] 
            z_up_value = (z_trigger_l_raw + 1.0) / 2.0
            z_down_value = (z_trigger_r_raw + 1.0) / 2.0

            # Combine the values. UP (L) is positive, DOWN (R) is negative.
            combined_z_axis_status = z_up_value - z_down_value
            l_bump = params.get('LBumper', 0)
            r_bump = params.get('RBumper', 0)
            combined_bumpers = int(l_bump if l_bump is not None else 0) - int(r_bump if r_bump is not None else 0)

            fmt = params.get('packet_format', PACKET_FORMAT)
            packet = struct.pack(
                fmt,
                START_MARKER,
                1,
                float(params['x_axisStatus']),
                float(params['y_axisStatus']),
                float(combined_z_axis_status),
                float(params['x_stepSize']) if 'f' in fmt[5:] else int(params['x_stepSize']),
                float(params['y_stepSize']) if 'f' in fmt[5:] else int(params['y_stepSize']),
                float(params['z_stepSize']) if 'f' in fmt[5:] else int(params['z_stepSize']),
                float(params['dpad_LR']) if 'f' in fmt[5:] else int(params['dpad_LR']),
                float(params['dpad_UD']) if 'f' in fmt[5:] else int(params['dpad_UD']),
                float(combined_bumpers) if 'f' in fmt[5:] else int(combined_bumpers),
                float(params['manual_jog_speed']) if 'f' in fmt[5:] else int(params['manual_jog_speed']),
            )

            print(f"[SerialDrive] Sending 12-Field MANUAL State: {packet}")
            with self._lock:
                self.ser.write(packet)  # type: ignore
                self.ser.flush()        # type: ignore
            
        # Exception handling
        except pyserial.SerialTimeoutException as e:
            msg = "[SerialDrive] WRITE TIMEOUT ERROR (Manual)\nThe serial write operation timed out."
            print(msg)
            ErrorPopupManager.report_error("Serial Write Timeout", msg, e)
        except Exception as e:
            msg = f"[SerialDrive] Error sending manual data:\n{e}"
            print(msg)
            ErrorPopupManager.report_error("Serial Write Error", msg, e)

    # How long a priority write waits for the transport lock before forcing
    # itself through. Short enough that FULL STOP is not held up by an
    # in-flight poll, long enough that the ordinary case still serialises.
    PRIORITY_LOCK_TIMEOUT = 0.05

    def write_command(self, payload, priority=False):
        """The one way anything reaches the hardware (RC-2, invariant I-2.3).

        Takes bytes (or str, encoded as UTF-8), writes under the lock, and
        raises TransportError if the write did not happen. It does **not**
        report the error to the popup router: that would let a caller treat
        a reported failure as handled and carry on. The caller decides what a
        failed write means — for a disable, it means the hardware state is
        now unknown and the model must fault.

        In simulator mode there is no port and nothing to write; that is a
        successful no-op, not a failure.
        """
        if isinstance(payload, str):
            payload = payload.encode('utf-8')

        acquired = self._lock.acquire(
            timeout=self.PRIORITY_LOCK_TIMEOUT if priority else -1)
        if not acquired:
            # A stop that cannot get the lock is worse than an unsynchronised
            # one (RC-5 item 2). A poll or a long autonomous write holding the
            # lock must not be able to delay 'd' or 'k'. The write below can
            # therefore interleave with whatever holds the lock; the firmware
            # treats both commands as idempotent single bytes, so a mangled
            # *stop* is the only thing this risks and the alternative is no
            # stop at all. Never pass priority=True for a motion command.
            print(f"[SerialDrive] PRIORITY: lock busy, forcing {payload!r} through")
        try:
            if self.ser is None or not self.ser.is_open:
                raise TransportError(
                    f"[SerialDrive] Port {self.SERIAL_PORT} is not open; "
                    f"{payload!r} was not sent")
            try:
                self.ser.write(payload)
            except Exception as e:
                lost = e
            else:
                lost = None
        finally:
            if acquired:
                self._lock.release()
        if lost is not None:
            # Outside the lock: _mark_lost takes it, and this may be the
            # priority path, which does not hold it.
            self._mark_lost(lost)
            raise TransportError(
                f"[SerialDrive] Write of {payload!r} failed: {lost}") from lost

    def _mark_lost(self, why):
        """First transport failure wins: go to LOST, close, report once.

        Port loss used to be invisible — read and write errors produced popup
        spam on a 5 s dedupe while the reported state stayed exactly as it
        was, so the UI kept showing the last good position of a device that
        had been unplugged (SERIAL-8). The transition is reported once; the
        state then carries the fact.
        """
        with self._lock:
            if self.connection_state == ConnectionState.LOST:
                return
            self.connection_state = ConnectionState.LOST
            handle, self.ser = self.ser, None
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
        msg = f"[SerialDrive] Connection to {self.SERIAL_PORT} lost: {why}"
        print(msg)
        try:
            ErrorPopupManager.report_error("Connection Lost", msg, None)
        except Exception:
            pass

    #: How long `flush()` waits for the OS to drain the output buffer before
    #: giving up. Short: every caller is on a shutdown path, and a stop that
    #: can hang the shutdown is its own bug.
    FLUSH_TIMEOUT = 1.0

    def flush(self, timeout=None):
        """Block until the bytes already written have reached the wire. -> bool.

        Exists so a model never has to reach for `.ser` (invariant I-2.3).
        The case that needs it is a heater or motion **shutdown frame**:
        `close()` on POSIX does not guarantee that bytes handed to the OS
        have been transmitted, so an off-frame written immediately before a
        close can be discarded by it — and the hardware stays on.

        **Bounded, on a worker.** pyserial's `flush()` is `tcdrain`, which is
        unbounded; SERIAL-12 already names it as a hazard for exactly this
        reason. A dead or flow-controlled port would otherwise hang whatever
        teardown called it, which is a worse failure than the one being
        fixed. The drain is dispatched to a daemon thread and joined with a
        budget — the same shape as `emergency_stop`'s bounded join, and for
        the same reason: returning without the answer beats not returning.

        Returns True only if the drain actually completed. False means the
        bytes may still be in the buffer, and a caller about to close the
        port should treat the frame as **not delivered**.

        Deliberately **not** a state transition: it does not `_mark_lost`. It
        is a query on a teardown path, and a "Connection Lost" popup raised
        while the application is closing is noise, not information. A write
        that matters will have already faulted through `write_command`.

        The handle is snapshotted rather than locked, for the same reason a
        priority write forces itself through: a drain that cannot get the
        lock is worse than an unsynchronised one, and `tcdrain` does not
        mutate the port — it only waits on it.
        """
        budget = self.FLUSH_TIMEOUT if timeout is None else timeout
        handle = self.ser
        if handle is None or not getattr(handle, "is_open", False):
            return False

        drained = threading.Event()
        failure = []

        def _drain():
            try:
                handle.flush()
            except Exception as e:
                failure.append(e)
            finally:
                drained.set()

        worker = threading.Thread(
            target=_drain, daemon=True, name=f"flush-{self.SERIAL_PORT}")
        worker.start()
        if not drained.wait(budget):
            print(f"[SerialDrive] Port {self.SERIAL_PORT} did not drain within "
                  f"{budget}s; treat the last frame as undelivered.")
            return False
        if failure:
            print(f"[SerialDrive] Drain of {self.SERIAL_PORT} failed: "
                  f"{failure[0]}")
            return False
        return True

    def is_open(self):
        """True when a command has somewhere to go — a real port or the simulator."""
        return self.ser is not None and self.ser.is_open

    def read_line(self):
        """One line from the port, or b"" when there is nothing to read.

        Exists so that models never touch `.ser` directly (invariant I-2.3);
        reads are part of the transport's job too, not just writes.
        """
        with self._lock:
            if self.ser is None or not self.ser.is_open:
                raise TransportError(
                    f"[SerialDrive] Port {self.SERIAL_PORT} is not open; cannot read")
            try:
                return self.ser.readline()
            except Exception as e:
                read_error = e
        self._mark_lost(read_error)
        raise TransportError(f"[SerialDrive] Read failed: {read_error}") from read_error

    def enable(self):
        """Raises TransportError if the enable did not reach the hardware.

        Simulator mode is a legal, fully working configuration — it is how the
        bench is exercised without hardware attached. `_verify_serial` returns
        False for SIM because there is no port object, which made `enable()`
        raise and left every SIM probe permanently un-armable (SERIAL-9).
        """
        if not self.is_open():
            raise ValueError("[SerialDrive] Arduino not detected. Cannot enable system.")
        self.write_command(b"e")

    def disable(self):
        """Raises TransportError if the disable did not reach the hardware.

        This used to swallow the write exception and return normally, so the
        model set system_enabled = False and the UI reported the system
        disabled while the coils were still energized.
        """
        if not self.is_open():
            raise ValueError("[SerialDrive] Arduino not detected. Cannot disable system.")
        self.write_command(b"d", priority=True)

    # Closes serial connection
    def close(self):
        with self._lock:
            if self.ser and self.ser.is_open:
                print("[SerialDrive] Closing serial port.")
                self.ser.close()
            if self.connection_state != ConnectionState.SIMULATED:
                self.connection_state = ConnectionState.CLOSED


# Aliases for backwards compatibility with legacy stable branch and standard naming
SerialArduino = serial
Serial = serial