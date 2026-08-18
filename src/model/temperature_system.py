from controller.seiral import serial

class TemperatureSystem:
    def __init__(self, port=None):
        self.setpoint = "0"
        self.ramp_rate = "10"
        self.p_term = "2.0"
        self.i_term = "0.5"
        self.d_term = ".1"
        self.offset = "0"
        
        self.current_temp = "N/A"
        
        self.tempC = []
        self.time = []
        self.sp = []
        self.cnt = 0
        
        self.serial_conn = serial(port) if port and port != "None" else None
        
        if self.serial_conn and self.serial_conn.ser and self.serial_conn.ser.is_open:
            try:
                self.serial_conn.ser.write(b"<0,6.0,0,0,0,0>")
            except Exception as e:
                print(f"Error writing initial state to serial: {e}")
                
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
                        {"type": "entry", "text": "Ramp Rate (deg/min):", "model_attr": "ramp_rate"},
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
        # Calculate spdelay (seconds per 1 degree step) assuming ramp_rate is degrees/minute
        try:
            rate_float = float(self.ramp_rate)
        except ValueError:
            rate_float = 0.0
        spdelay = str(60.0 / rate_float) if rate_float > 0 else "0"
        
        if self.serial_conn and self.serial_conn.ser and self.serial_conn.ser.is_open:
            input_string = f"<{self.setpoint},{spdelay},{self.p_term},{self.i_term},{self.d_term},{self.offset}>"
            try:
                self.serial_conn.ser.write(input_string.encode())
            except Exception as e:
                print(f"Error writing to serial: {e}")
                
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

    def stop(self):
        if self.serial_conn and self.serial_conn.ser and self.serial_conn.ser.is_open:
            try:
                rate_float = float(self.ramp_rate)
            except ValueError:
                rate_float = 0.0
            spdelay = str(60.0 / rate_float) if rate_float > 0 else "0"
            vals = ['0', spdelay, self.p_term, self.i_term, self.d_term, self.offset]
            input_string = f"<{','.join(vals)}>"
            try:
                self.serial_conn.ser.write(input_string.encode())
            except Exception as e:
                print(f"Error writing to serial: {e}")
