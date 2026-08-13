import serial
import threading
import queue
import tkinter as tk
from tkinter import messagebox
from models.temp_model import TempModel
from views.temp_view import TempView

BAUD_RATE = 115200

class TempController:
    def __init__(self, root: tk.Tk, port: str):
        self.root = root
        self.port = port
        
        self.model = TempModel()
        self.view = TempView(self.root, self.model, self)
        
        self.root.protocol("WM_DELETE_WINDOW", self.stop_plot)

        self.data_queue = queue.Queue()
        self.continue_plotting = True
        
        self.ser = None
        self._init_serial()
        
        if self.ser:
            self.serial_thread = threading.Thread(target=self.read_serial_data, daemon=True)
            self.serial_thread.start()
            
        self.root.after(50, self.update_gui)
        
    def _init_serial(self):
        try:
            self.ser = serial.Serial(self.port, BAUD_RATE, timeout=1)
            self.ser.reset_input_buffer()
            self.ser.write(b"<0,10,0,0,0,0>")
        except Exception as e:
            messagebox.showerror("Serial Error", f"Could not open port {self.port}.\nError: {e}")
            self.ser = None

    def send_settings(self):
        if not self.ser or not self.ser.is_open:
            print("Serial port is not open.")
            return

        vals = [
            self.model.setpoint.get(),
            self.model.ramp_rate.get(),
            self.model.p_term.get(),
            self.model.i_term.get(),
            self.model.d_term.get(),
            self.model.offset.get()
        ]
        print(f"Sending: Setpoint={vals[0]}C, Ramp={vals[1]}s/C, P={vals[2]}, I={vals[3]}, D={vals[4]}, Offset={vals[5]}")
        
        input_string = f"<{','.join(vals)}>"
        try:
            self.ser.write(input_string.encode())
        except Exception as e:
            print(f"Error writing to serial: {e}")

    def read_serial_data(self):
        while self.continue_plotting:
            try:
                if self.ser and self.ser.is_open:
                    raw_line = self.ser.readline()
                    if raw_line:
                        self.data_queue.put(raw_line)
            except Exception as e:
                print(f"Serial background read error: {e}")
                break

    def update_gui(self):
        try:
            while not self.data_queue.empty():
                raw_data = self.data_queue.get_nowait()
                line = raw_data.decode('utf-8', errors='ignore').strip()
                if not line:
                    continue
                
                data_array = line.split(',')
                if len(data_array) >= 3:
                    try:
                        t = float(data_array[0].strip())
                        temp = float(data_array[1].strip())
                        setpoint = float(data_array[2].strip())

                        self.model.tempC.append(temp)
                        self.model.time.append(t)
                        self.model.sp.append(setpoint)
                        self.model.cnt += 1

                        if self.model.cnt > 200:
                            self.model.tempC.pop(0)
                            self.model.time.pop(0)
                            self.model.sp.pop(0)

                        self.model.current_temp.set(f"{temp:.2f} °C")
                    except ValueError:
                        pass
        except Exception as e:
            print(f"GUI Update Error: {e}")

        if self.continue_plotting:
            self.root.after(50, self.update_gui)

    def stop_plot(self):
        self.continue_plotting = False
        vals = ['0','10','2.0','0.5','.1','0']
        print(f"Sending: Setpoint={vals[0]}C, Ramp={vals[1]}s/C, P={vals[2]}, I={vals[3]}, D={vals[4]}, Offset={vals[5]}")
        
        input_string = f"<{','.join(vals)}>"
        try:
            if self.ser and self.ser.is_open:
                self.ser.write(input_string.encode())
                self.ser.close()
        except Exception as e:
            print(f"Error writing to serial: {e}")
        self.root.destroy()
