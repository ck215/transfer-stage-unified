from controller.serial import serial
from model.numeric import num, safe_float
import threading
import time

class TemperatureSystem:
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
        self.tempC = []
        self.time = []
        self.sp = []
        self.cnt = 0
        
        self.serial_conn = serial(port, baud_rate=115200) if port and port != "None" else None
        self.continue_reading = True
        
        if self.serial_conn and self.serial_conn.is_open():
            try:
                self.serial_conn.write_command(b"<0,6.0,0,0,0,0>")
            except Exception as e:
                from error_routing import ErrorRouter as ErrorPopupManager
                ErrorPopupManager.report_error("Serial Write Error", f"Error writing initial state to serial:\n{e}", e)

            self.serial_thread = threading.Thread(target=self.read_serial_data, daemon=True)
            self.serial_thread.start()
                
    @property
    def ui_schema(self):
        return {
            "sections": [
                {
                    "title": "Temperature Readings",
                    "elements": [
                        {"type": "readonly", "text": "Current Temperature:", "model_attr": "current_temp"}
                    ]
                },
                {
                    "title": "Control Parameters",
                    "elements": [
                        {"type": "entry", "text": "Setpoint:", "model_attr": "setpoint"},
                        {"type": "entry", "text": "Ramp Rate (s/°C):", "model_attr": "ramp_rate"},
                        {"type": "entry", "text": "Proportional Term (P):", "model_attr": "p_term"},
                        {"type": "entry", "text": "Integral Term (I):", "model_attr": "i_term"},
                        {"type": "entry", "text": "Derivative Term (D):", "model_attr": "d_term"},
                        {"type": "entry", "text": "Offset:", "model_attr": "offset"}
                    ]
                },
                {
                    "title": "System Control",
                    "elements": [
                        {"type": "button", "text": "Enter Settings", "command": "send_settings", "bg": "darkgreen", "fg": "white"},
                        {"type": "button", "text": "Stop System", "command": "stop", "bg": "darkred", "fg": "white"}
                    ]
                }
            ]
        }
        
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
            input_string = f"<{self.setpoint},{spdelay},{self.p_term},{self.i_term},{self.d_term},{self.offset}>"
            try:
                self.serial_conn.write_command(input_string)
            except Exception as e:
                from error_routing import ErrorRouter as ErrorPopupManager
                ErrorPopupManager.report_error("Serial Write Error", f"Error writing to serial:\n{e}", e)
                
    def read_serial_data(self):
        consecutive_failures = 0
        while getattr(self, 'continue_reading', True):
            try:
                if self.serial_conn and self.serial_conn.is_open():
                    raw_line = self.serial_conn.read_line()
                    if raw_line:
                        line = raw_line.decode('utf-8', errors='ignore')
                        self.process_raw_data(line)
                    # Unconditional floor
                    time.sleep(0.01)
                else:
                    time.sleep(0.1)
                consecutive_failures = 0
            except Exception as e:
                if not getattr(self, 'continue_reading', True):
                    break
                consecutive_failures += 1
                from error_routing import ErrorRouter
                if consecutive_failures >= 5:
                    msg = f"Giving up after {consecutive_failures} consecutive failures: {e}"
                    print(msg)
                    ErrorRouter.report_error("Temperature Read Error (Fatal)", msg, e)
                    break
                else:
                    msg = f"Serial background read error (transient, retry {consecutive_failures}/5): {e}"
                    print(msg)
                    ErrorRouter.report_error("Temperature Read Error", msg, e)
                    time.sleep(0.1)

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

    def stop(self):
        """Stops heating immediately by setting target setpoint to 0 while keeping serial monitoring active."""
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
            try:
                self.serial_conn.write_command(input_string)
            except Exception as e:
                from error_routing import ErrorRouter as ErrorPopupManager
                ErrorPopupManager.report_error("Serial Write Error", f"Error writing stop state to serial:\n{e}", e)

    def close(self):
        """Cleanly terminates serial thread and closes serial connection."""
        self.continue_reading = False
        if self.serial_conn and self.serial_conn.is_open():
            try:
                self.serial_conn.write_command(b"<0,6.0,0,0,0,0>")
            except Exception:
                pass
            try:
                self.serial_conn.close()
            except Exception:
                pass

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
        """Latch first, then command the setpoint down (RC-5)."""
        self._estop.set()
        self.stop()
