class TemperatureSystem:
    def __init__(self, serial_conn=None):
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
        
        self.serial_conn = serial_conn
        
    def send_settings(self, setpoint, ramp_rate, p_term, i_term, d_term, offset):
        self.setpoint = str(setpoint)
        
        # Calculate spdelay (seconds per 1 degree step) assuming ramp_rate is degrees/minute
        try:
            rate_float = float(ramp_rate)
        except ValueError:
            rate_float = 0.0
        spdelay = str(60.0 / rate_float) if rate_float > 0 else "0"

        self.ramp_rate = str(ramp_rate)
        self.p_term = str(p_term)
        self.i_term = str(i_term)
        self.d_term = str(d_term)
        self.offset = str(offset)
        
        if self.serial_conn and self.serial_conn.is_open:
            input_string = f"<{self.setpoint},{spdelay},{self.p_term},{self.i_term},{self.d_term},{self.offset}>"
            try:
                self.serial_conn.write(input_string.encode())
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
        if self.serial_conn and self.serial_conn.is_open:
            vals = ['0', self.ramp_rate, self.p_term, self.i_term, self.d_term, self.offset]
            input_string = f"<{','.join(vals)}>"
            try:
                self.serial_conn.write(input_string.encode())
                self.serial_conn.close()
            except Exception as e:
                print(f"Error writing to serial: {e}")
