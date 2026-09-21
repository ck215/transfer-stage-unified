from controller.serial import serial
from model.numeric import num, safe_float
import threading
import time
from model import schema as sch
from model.params import Param, table as _param_table
from model.base import SchemaCommands


class TemperatureSystem(SchemaCommands):
    PARAMS = _param_table(
        Param("setpoint", "float", default=0, decimals=1, unit="C",
              label="Setpoint"),
        Param("ramp_rate", "float", default=10, minimum=0, decimals=2,
              unit="s/C", label="Ramp Rate (s/\u00b0C)"),
        Param("p_term", "float", default=2.0, decimals=3,
              label="Proportional Term (P)"),
        Param("i_term", "float", default=0.5, decimals=3,
              label="Integral Term (I)"),
        Param("d_term", "float", default=0.1, decimals=3,
              label="Derivative Term (D)"),
        Param("offset", "float", default=0, decimals=2, label="Offset"),
        Param("current_temp", "text", default="N/A",
              label="Current Temperature"),
    )

    #: What `current_temp` reads when there is no live link (TEMP-10). One
    #: spelling, because three renderers display this attribute directly and
    #: the old code had the string in one place and "N/A" in another.
    DISCONNECTED_TEMP = "Disconnected"

    @property
    def connection_state(self):
        """The transport's link state, as a string, for every renderer."""
        state = getattr(getattr(self, "serial_conn", None),
                        "connection_state", None)
        if state is None:
            return "CLOSED"
        return getattr(state, "name", str(state))

    def __del__(self):
        print(f"[{self.__class__.__name__}] Destructor called")

    def __init__(self, port=None):
        self.setpoint = "0"
        self.ramp_rate = "10"
        self.p_term = "2.0"
        self.i_term = "0.5"
        self.d_term = ".1"
        self.offset = "0"
        
        self.current_temp = "N/A"
        
        self._lock = threading.Lock()
        # FULL STOP latch (RC-5). A heater that can be re-commanded to a
        # setpoint straight after an emergency stop is the same gap as a
        # stepper that can be re-commanded to move. Cleared only by an
        # explicit operator action.
        self._estop = threading.Event()

        # `send_settings` and `stop` both build a frame from `self.*` fields
        # and then write it. Under the Web view those run on concurrent
        # `ThreadingHTTPServer` request threads, so Enter Settings could read
        # `self.setpoint` *before* stop assigned "0" and land its write
        # *after* stop's -- re-arming the heater the operator just stopped
        # (TEMP-7). Read-and-write is one critical section, not two steps.
        self._write_lock = threading.Lock()
        self.tempC = []
        self.time = []
        self.sp = []
        self.cnt = 0
        
        self.serial_conn = serial(port, baud_rate=115200) if port and port != "None" else None
        self.continue_reading = True
        #: Set by `close()` so the reader's backoff is interruptible.
        #:
        #: TEMP-2 gave the reader a real exponential backoff, which is the
        #: right fix — but a plain `time.sleep(backoff)` is only checked for
        #: `continue_reading` at the top of the loop, so a reader parked in a
        #: 2.0 s backoff outlives `READER_JOIN_TIMEOUT` (1.5 s). `close()`
        #: would then give up waiting and shut the port while that thread was
        #: still about to touch it. Waiting on an event instead means the
        #: backoff ends the instant shutdown asks for it, whatever its length.
        self._reader_wake = threading.Event()
        
        if self.serial_conn and self.serial_conn.is_open():
            try:
                self.serial_conn.write_command(b"<0,6.0,0,0,0,0>")
            except Exception as e:
                from error_routing import ErrorRouter as ErrorPopupManager
                ErrorPopupManager.report_error("Serial Write Error", f"Error writing initial state to serial:\n{e}", e)

            self.serial_thread = threading.Thread(target=self.read_serial_data, daemon=True)
            self.serial_thread.start()
        else:
            # TEMP-10 (headline). The reader is started only in the branch
            # above, so with no port -- SIM, 'None', or an open that failed --
            # nothing is ever in a position to update `current_temp`, and it
            # keeps its "N/A" default for the life of the process. "N/A"
            # reads as *no reading yet*, a transient state, so the operator
            # waits for a number that cannot arrive. Say what is actually
            # true instead. The three renderers show it as one because they
            # all render this same attribute.
            self.current_temp = self.DISCONNECTED_TEMP
                
    @property
    def ui_schema(self):
        P = self.PARAMS
        return sch.schema(
            sch.section(
                "Temperature Readings",
                sch.readonly("Current Temperature:", "current_temp",
                             param=P["current_temp"]),
                sch.readonly("Connection:", "connection_state", role="info"),
                # **D-13: schema-driven plot in all three views.** The history
                # is already collected; this plot uses the same data path as
                # Red Percent: a property that returns {"x": [...], "y": [...]}.
                sch.plot("Temperature over time", "temp_series",
                         x_label="time (s)", y_label="temperature (°C)"),
            ),
            sch.section(
                "Control Parameters",
                *[sch.entry(P[name].label + ":", name, P[name])
                  for name in ("setpoint", "ramp_rate", "p_term", "i_term",
                               "d_term", "offset")],
            ),
            sch.section(
                "System Control",
                # **D-5.** Every field of the frame travels with the command
                # and is validated as a set. This is the heater case from S9:
                # the firmware parses the frame with `strtok`, which does not
                # see an empty field — it sees the next one. A blank box shifts
                # every later parameter left, so the board takes the ramp rate
                # as its setpoint. Refusing the whole frame is the only
                # correct answer.
                sch.button("Enter Settings", "send_settings",
                           inputs=("setpoint", "ramp_rate", "p_term",
                                   "i_term", "d_term", "offset"),
                           role="go"),
                sch.button("Stop System", "stop", role="danger"),
            ),
        )

    @property
    def estop_latched(self):
        return self._estop.is_set()

    def clear_estop(self):
        """Explicit operator action. Nothing else may call this (RC-5)."""
        self._estop.clear()
        print(f"[{self.__class__.__name__}] FULL STOP latch cleared by operator")

    def send_settings(self):
        if self._estop.is_set():
            print(f"[{self.__class__.__name__}] Settings refused: FULL STOP is latched")
            return
        # ramp_rate is spdelay (seconds per 1-degree setpoint step) directly,
        # in the firmware's own native unit -- it's sent and displayed on the
        # firmware's LCD ("RR = {spdelay}s/C") unconverted, so what's entered
        # here matches what's shown on the physical display.
        rate_float = num(self.ramp_rate, 0.0)

        try:
            spdelay = f"{rate_float:.2f}" if rate_float >= 0 else "0"
            if "inf" in spdelay.lower() or "nan" in spdelay.lower():
                spdelay = "0"
        except OverflowError:
            spdelay = "0"

        # Every field is validated before the frame is built (RC-6 item 4).
        #
        # These values used to be interpolated raw. An empty field is enough
        # to produce "<,6.0,2.0,0.5,.1,0>", and the firmware parses that
        # with strtok — which does not see an empty field, it sees the *next*
        # one. Every parameter after the blank shifts left, so the board is
        # handed the ramp rate as its setpoint and the gains as everything
        # else. A blank box silently commands the wrong temperature with the
        # wrong gains; refusing is the only safe answer.
        fields = {
            "Setpoint": self.setpoint,
            "P": self.p_term,
            "I": self.i_term,
            "D": self.d_term,
            "Offset": self.offset,
        }
        invalid = [name for name, value in fields.items()
                   if safe_float(value) is None]
        if invalid:
            msg = (f"[{self.__class__.__name__}] Refusing to send: "
                   f"{', '.join(invalid)} is not a number. Nothing was sent.")
            print(msg)
            try:
                from error_routing import ErrorRouter
                ErrorRouter.report_warning("Temperature Settings Invalid", msg)
            except Exception:
                pass
            return

        if self.serial_conn and self.serial_conn.is_open():
            msg = f"[{self.__class__.__name__}] Sending: Setpoint={self.setpoint}C, Ramp={self.ramp_rate}s/°C (delay={spdelay}s), P={self.p_term}, I={self.i_term}, D={self.d_term}, Offset={self.offset}"
            print(msg)
            try:
                from error_routing import ErrorRouter
                ErrorRouter.report_info("Temperature Send", msg)
            except Exception:
                pass
            with self._write_lock:
                # Re-checked *inside* the lock, immediately before the write.
                # The check at the top of this method is a check-then-act: a
                # FULL STOP landing after it and before this write would be
                # overwritten by the frame we are about to send. The latch is
                # set before any of the stop's I/O, so testing it here is what
                # actually orders the two (TEMP-7).
                if self._estop.is_set():
                    print(f"[{self.__class__.__name__}] Settings refused at "
                          f"the write: FULL STOP latched while the frame was "
                          f"being built")
                    return
                input_string = f"<{self.setpoint},{spdelay},{self.p_term},{self.i_term},{self.d_term},{self.offset}>"
                try:
                    self.serial_conn.write_command(input_string)
                except Exception as e:
                    from error_routing import ErrorRouter as ErrorPopupManager
                    ErrorPopupManager.report_error("Serial Write Error", f"Error writing to serial:\n{e}", e)
        else:
            # TEMP-10: No port means the model is not connected. Report this
            # to the operator so they know that the command did not reach
            # the hardware.
            msg = f"[{self.__class__.__name__}] Not connected: the temperature controller port is not open. Settings not sent."
            print(msg)
            try:
                from error_routing import ErrorRouter
                ErrorRouter.report_warning("Temperature Not Connected", msg)
            except Exception:
                pass

    def _backoff_wait(self, backoff):
        """Wait out a backoff, but return early the moment close() asks.

        Returns True when the reader should stop. `Event.wait` returns True
        if the flag was set, which is exactly the shutdown case, so the two
        conditions collapse into one call.
        """
        if self._reader_wake.wait(backoff):
            return True
        return not getattr(self, "continue_reading", True)

    def read_serial_data(self):
        """Background reader with exponential backoff and indefinite retry.

        TEMP-2: Real backoff (0.1 -> 0.2 -> ... up to ~2 s), retry indefinitely,
        and set current_temp to indicate disconnection on persistent failure.
        """
        consecutive_failures = 0
        max_backoff = 2.0  # Maximum backoff delay in seconds
        failure_reported = False  # Track if we've reported a disconnection

        while getattr(self, 'continue_reading', True):
            try:
                if self.serial_conn and self.serial_conn.is_open():
                    raw_line = self.serial_conn.read_line()
                    if raw_line:
                        line = raw_line.decode('utf-8', errors='ignore')
                        self.process_raw_data(line)
                        # Reset on successful read
                        consecutive_failures = 0
                        failure_reported = False
                    # Unconditional floor
                    time.sleep(0.01)
                else:
                    # Port is closed/not open, treat as a read failure
                    consecutive_failures += 1
                    # TEMP-10 (headline). This branch used to back off in
                    # silence. `current_temp` was set to the disconnected
                    # state *only* in the `except Exception` handler below --
                    # and a port that is simply not open raises nothing, so
                    # that line was unreachable from here. A link that
                    # dropped mid-session went on displaying its last good
                    # reading as though it were live.
                    self.current_temp = self.DISCONNECTED_TEMP
                    # Exponential backoff: 0.1, 0.2, 0.4, 0.8, 1.6, 2.0, 2.0, ...
                    backoff = min(0.1 * (2 ** (consecutive_failures - 1)), max_backoff)
                    if self._backoff_wait(backoff):
                        break
            except Exception as e:
                if not getattr(self, 'continue_reading', True):
                    break
                consecutive_failures += 1
                # Exponential backoff: 0.1, 0.2, 0.4, 0.8, 1.6, 2.0, 2.0, ...
                backoff = min(0.1 * (2 ** (consecutive_failures - 1)), max_backoff)

                from error_routing import ErrorRouter

                # Only report once on transition to persistent failure
                # (first 5 failures are transient, after that is persistent)
                if consecutive_failures <= 5:
                    msg = f"Serial background read error (transient, retry {consecutive_failures}/5): {e}"
                    print(msg)
                    ErrorRouter.report_error("Temperature Read Error", msg, e)
                elif not failure_reported:
                    # Persistent failure: set disconnected state
                    msg = f"Temperature reader persistent connection loss after {consecutive_failures} failures: {e}"
                    print(msg)
                    ErrorRouter.report_warning("Temperature Disconnected", msg)
                    self.current_temp = self.DISCONNECTED_TEMP
                    failure_reported = True

                if self._backoff_wait(backoff):
                    break

    def process_raw_data(self, data_line):
        line = data_line.strip()
        if not line:
            return
        data_array = line.split(',')
        if len(data_array) >= 3:
            try:
                t = float(data_array[0].strip())
                temp = float(data_array[1].strip())
                sp_val = float(data_array[2].strip())
                
                with self._lock:
                    self.tempC.append(temp)
                    self.time.append(t)
                    self.sp.append(sp_val)
                    self.cnt += 1
                    
                    if self.cnt > 200:
                        self.tempC.pop(0)
                        self.time.pop(0)
                        self.sp.pop(0)
                        
                    self.current_temp = f"{temp:.2f} °C"
            except ValueError:
                pass

    def get_history(self):
        """Thread-safe snapshot of history arrays."""
        with self._lock:
            return list(self.time), list(self.tempC), list(self.sp)

    def temp_series(self):
        """The data behind the `plot` composite.

        Returns `{"x": [...], "y": [...]}`. This is the whole of what a plot
        renderer needs, and it is the reason the plot can now be schema-driven
        in all three views. The x axis is time, the y axis is temperature.
        """
        with self._lock:
            return {"x": list(self.time), "y": list(self.tempC)}

    #: A stop that cannot get the write lock is worse than an unsynchronised
    #: one. Mirrors the probes' and the rotator's priority paths (RC-5 item 2).
    PRIORITY_LOCK_TIMEOUT = 0.05
    ESTOP_RETURN_BUDGET = 0.08

    def stop(self, priority=False):
        """Stops heating immediately by setting target setpoint to 0 while keeping serial monitoring active.

        With `priority`, the write lock is taken with a timeout and the frame
        is forced through if an Enter Settings is mid-write. A zero-setpoint
        frame is idempotent, so the worst case is one mangled *stop* and the
        alternative is a FULL STOP that waits on the heater (TEMP-7).
        """
        self.setpoint = "0"
        rate_float = num(self.ramp_rate, 0.0)

        try:
            spdelay = f"{rate_float:.1f}" if rate_float >= 0 else "0"
            if "inf" in spdelay.lower() or "nan" in spdelay.lower():
                spdelay = "0"
        except OverflowError:
            spdelay = "0"

        if self.serial_conn and self.serial_conn.is_open():
            vals = ['0', spdelay, '0', '0', '0', str(self.offset)]
            input_string = f"<{','.join(vals)}>"
            acquired = self._write_lock.acquire(
                timeout=self.PRIORITY_LOCK_TIMEOUT if priority else -1)
            if not acquired:
                print(f"[{self.__class__.__name__}] PRIORITY: write lock busy, "
                      f"forcing the stop frame through")
            try:
                self.serial_conn.write_command(input_string, priority=priority)
            except Exception as e:
                from error_routing import ErrorRouter as ErrorPopupManager
                ErrorPopupManager.report_error("Serial Write Error", f"Error writing stop state to serial:\n{e}", e)
            finally:
                if acquired:
                    self._write_lock.release()
        else:
            # TEMP-10: No port means the model is not connected. Report this
            # to the operator so they know that the stop command did not reach
            # the hardware.
            msg = f"[{self.__class__.__name__}] Not connected: the temperature controller port is not open. Stop command not sent."
            print(msg)
            try:
                from error_routing import ErrorRouter
                ErrorRouter.report_warning("Temperature Not Connected", msg)
            except Exception:
                pass

    #: How long close() waits for the reader to leave the port. It reads with
    #: a 1 s timeout, so one outstanding read plus slack.
    READER_JOIN_TIMEOUT = 1.5

    def close(self):
        """Send the heater-off frame, let the reader leave the port, close it.

        This is a heater shutdown path, and the firmware has no watchdog: a
        zero-setpoint frame that does not go out leaves the heater at its
        last setpoint for as long as it stays powered. It therefore differs
        from the version it replaces in three ways (TEMP-11):

        1. **A failed write is reported, not swallowed.** The old body was
           `except Exception: pass` around both the write and the close, so
           quitting with a dead cable delivered nothing and said nothing.
           The docstring here used to claim it "cleanly terminates" the
           serial thread, which it also did not do — that claim, and the
           error-routing notes calling these two `except` blocks candidate
           *warning* sites, were both describing code that reported nothing
           at all.
        2. **The reader is joined before the port closes**, rather than
           being left inside `read_line()` on a descriptor closing under it.
           That join is also the only window the off-frame has to drain.
        3. **The frame goes out on the priority path.** It is a single
           idempotent zero-setpoint frame, exactly the case
           `docs/architecture/safety-pattern.md` item 3 permits, so a
           transaction holding the transport lock cannot hold the shutdown.

        4. **The frame is drained before the port is released.** `close()`
           on POSIX does not guarantee that bytes handed to the OS have been
           transmitted, so the off-frame can be discarded by the very close
           that follows it. `transport.flush()` is bounded and returns
           whether the drain actually completed; a `False` is reported with
           the same obligation as a failed write, because both leave the
           heater at its last setpoint. A transport without a `flush()` —
           a simulated port, or an older double — is not an undelivered
           frame and is not reported.

        Items 1-3 were written in the `fix-web` worktree against a transport
        that had no flush; item 4 is the seam, joined on merge once
        `fix-transport` added one. Neither half could be tested alone.
        """
        self.continue_reading = False
        # Wake a reader parked in its backoff, so the join below is waiting on
        # a thread that is actually trying to leave (TEMP-2 follow-up).
        wake = getattr(self, "_reader_wake", None)
        if wake is not None:
            wake.set()
        conn = self.serial_conn
        if conn and conn.is_open():
            try:
                conn.write_command(b"<0,6.0,0,0,0,0>", priority=True)
            except Exception as e:
                from error_routing import ErrorRouter
                ErrorRouter.report_error(
                    "Heater Off Not Delivered",
                    f"The heater-off frame could not be sent while closing "
                    f"the temperature controller, so the heater may still be "
                    f"at its last setpoint:\n{e}",
                    e, source=self.__class__.__name__, requires_ack=True)

            # Drain before the close can discard the frame. Bounded by the
            # transport; `False` means the bytes may still be buffered.
            flush = getattr(conn, "flush", None)
            if callable(flush):
                try:
                    drained = flush()
                except Exception as e:
                    drained, exc = False, e
                else:
                    exc = None
                if drained is False:
                    from error_routing import ErrorRouter
                    ErrorRouter.report_error(
                        "Heater Off Not Delivered",
                        "The heater-off frame was written but could not be "
                        "confirmed on the wire before the port closed, so "
                        "the heater may still be at its last setpoint."
                        + (f"\n{exc}" if exc else ""),
                        exc, source=self.__class__.__name__,
                        requires_ack=True)

            reader = getattr(self, "serial_thread", None)
            if (reader is not None and reader is not threading.current_thread()
                    and reader.is_alive()):
                reader.join(timeout=self.READER_JOIN_TIMEOUT)
                if reader.is_alive():
                    print(f"[{self.__class__.__name__}] Reader still in the "
                          f"port after {self.READER_JOIN_TIMEOUT}s; closing "
                          f"anyway")

            try:
                conn.close()
            except Exception as e:
                from error_routing import ErrorRouter
                ErrorRouter.report_error(
                    "Serial Close Error",
                    f"Failed to release the temperature controller port:\n{e}",
                    e, source=self.__class__.__name__)

    def disconnect(self):
        """Alias for close to support unified model lifecycle."""
        self.close()

    def teardown(self):
        """Command the setpoint down, then close (RC-1).

        close() writes a stop frame of its own, but only if the port is still
        open and the write succeeds; stop() first makes the hardware stop the
        step that cannot be skipped by a transport failure.
        """
        try:
            self.stop()
        except Exception as e:
            print(f"[{self.__class__.__name__}] Stop failed during teardown: {e}")
        self.close()

    def emergency_stop(self):
        """Latch first, then dispatch the setpoint-down with a bounded wait.

        The latch was already here; the dispatch was not. `stop()` writes
        through the serial wrapper, whose lock is held for whole transactions,
        so an emergency stop on the heater ran its I/O on the calling thread
        -- frequently a UI thread -- exactly like the probe defect S8 fixed
        and the rotator defect S8 missed (I-5.2, TEMP-7).
        """
        self._estop.set()

        done = threading.Event()

        def _stop():
            try:
                self.stop(priority=True)
            finally:
                done.set()

        worker = threading.Thread(
            target=_stop, daemon=True,
            name=f"estop-{self.__class__.__name__}")
        worker.start()
        if not done.wait(self.ESTOP_RETURN_BUDGET):
            print(f"[{self.__class__.__name__}] FULL STOP: latched; heater "
                  f"stop still in flight after {self.ESTOP_RETURN_BUDGET}s")
