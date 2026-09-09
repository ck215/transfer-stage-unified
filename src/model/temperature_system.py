from controller.seiral import serial
import threading
import time

class TemperatureSystem:
    def __init__(self, port=None):
        self.setpoint = "0"
        self.ramp_rate = "10"
        self.p_term = "2.0"
        self.i_term = "0.5"
        self.d_term = ".1"
        self.offset = "0"
        
        self.current_temp = "N/A"
        
        self._lock = threading.Lock()
        self.tempC = []
        self.time = []
        self.sp = []
        self.cnt = 0
        
        self.serial_conn = serial(port, baud_rate=115200) if port and port != "None" else None
        self.continue_reading = True
        
        if self.serial_conn and self.serial_conn.ser and self.serial_conn.ser.is_open:
            try:
                self.serial_conn.ser.write(b"<0,6.0,0,0,0,0>")
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
                        {"type": "entry", "text": "Ramp Rate (°C/min):", "model_attr": "ramp_rate"},
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
        
    def send_settings(self):
        # Calculate spdelay (seconds per 1 degree step) assuming ramp_rate is °C/minute
        import math
        try:
            rate_float = float(self.ramp_rate)
            if math.isnan(rate_float) or math.isinf(rate_float):
                rate_float = 0.0
        except ValueError:
            rate_float = 0.0
            
        try:
            spdelay = str(60.0 / rate_float) if rate_float > 0 else "0"
            if "inf" in spdelay.lower() or "nan" in spdelay.lower():
                spdelay = "0"
        except OverflowError:
            spdelay = "0"
        
        if self.serial_conn and self.serial_conn.ser and self.serial_conn.ser.is_open:
            print(f"Sending: Setpoint={self.setpoint}C, Ramp={self.ramp_rate}°C/min (delay={spdelay}s), P={self.p_term}, I={self.i_term}, D={self.d_term}, Offset={self.offset}")
            input_string = f"<{self.setpoint},{spdelay},{self.p_term},{self.i_term},{self.d_term},{self.offset}>"
            try:
                self.serial_conn.ser.write(input_string.encode())
            except Exception as e:
                from error_routing import ErrorRouter as ErrorPopupManager
                ErrorPopupManager.report_error("Serial Write Error", f"Error writing to serial:\n{e}", e)
                
    def read_serial_data(self):
        while getattr(self, 'continue_reading', True):
            try:
                if self.serial_conn and self.serial_conn.ser and self.serial_conn.ser.is_open:
                    raw_line = self.serial_conn.ser.readline()
                    if raw_line:
                        line = raw_line.decode('utf-8', errors='ignore')
                        self.process_raw_data(line)
                else:
                    time.sleep(0.1)
            except Exception as e:
                if not getattr(self, 'continue_reading', True):
                    break
                from error_routing import ErrorRouter
                msg = f"Serial background read error: {e}"
                print(msg)
                ErrorRouter.report_error("Temperature Read Error", msg, e)
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

    def stop(self):
        """Stops heating immediately by setting target setpoint to 0 while keeping serial monitoring active."""
        self.setpoint = "0"
        import math
        try:
            rate_float = float(self.ramp_rate)
            if math.isnan(rate_float) or math.isinf(rate_float):
                rate_float = 0.0
        except ValueError:
            rate_float = 0.0
            
        try:
            spdelay = str(60.0 / rate_float) if rate_float > 0 else "0"
            if "inf" in spdelay.lower() or "nan" in spdelay.lower():
                spdelay = "0"
        except OverflowError:
            spdelay = "0"
            
        if self.serial_conn and self.serial_conn.ser and self.serial_conn.ser.is_open:
            vals = ['0', spdelay, '0', '0', '0', str(self.offset)]
            input_string = f"<{','.join(vals)}>"
            try:
                self.serial_conn.ser.write(input_string.encode())
            except Exception as e:
                from error_routing import ErrorRouter as ErrorPopupManager
                ErrorPopupManager.report_error("Serial Write Error", f"Error writing stop state to serial:\n{e}", e)

    def close(self):
        """Cleanly terminates serial thread and closes serial connection."""
        self.continue_reading = False
        if self.serial_conn and self.serial_conn.ser and self.serial_conn.ser.is_open:
            try:
                self.serial_conn.ser.write(b"<0,6.0,0,0,0,0>")
            except Exception:
                pass
            try:
                self.serial_conn.close()
            except Exception:
                pass

    def disconnect(self):
        """Alias for close to support unified model lifecycle."""
        self.close()
