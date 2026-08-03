import serial
import threading
import queue
import tkinter as tk
from tkinter import Label, Button, Entry, messagebox

# Configuration
BAUD_RATE = 115200

class App:
    def __init__(self, master, comport):
        PORT = comport
        self.root = master
        self.root.title("PID Temperature Controller")
        
        # Configure window close protocol to exit cleanly
        self.root.protocol("WM_DELETE_WINDOW", self.stop_plot)

        # Thread-safe queue for serial data
        self.data_queue = queue.Queue()
        self.continue_plotting = True
        
        # Historical arrays
        self.tempC = []
        self.time = []
        self.sp = []
        self.cnt = 0

        # Initialize Serial
        try:
            self.ser = serial.Serial(PORT, BAUD_RATE, timeout=1)
            self.ser.reset_input_buffer()  # Flush any stale buffer data on startup
            self.ser.write("<0,10,0,0,0,0>".encode())
        except Exception as e:
            messagebox.showerror("Serial Error", f"Could not open port {PORT}.\nError: {e}")
            self.ser = None

        # --- UI SETUP ---
        Label(master, text="Controls:").grid(row=0, column=0, columnspan=2, pady=5)
        Label(master, text="Current Temperature:").grid(row=7, column=0, sticky="W", padx=5)

        # Labels for inputs
        fields = ["Set Temperature", "Ramping Rate", "P Term", "I Term", "D Term", "Temperature Offset"]
        for i, text in enumerate(fields, start=1):
            Label(master, text=text).grid(row=i, column=0, sticky="W", padx=5)

        # Entry Boxes
        self.entries = {}
        defaults = ["0", "10", "2.0", "0.5", ".1", "0"]
        for i, val in enumerate(defaults, start=1):
            entry = Entry(master)
            entry.grid(row=i, column=1, padx=5, pady=2)
            entry.insert(0, val)
            self.entries[f'e{i}'] = entry

        # CRITICAL FIX: Create the display label ONCE here
        self.temp_display_label = Label(master, text="N/A", font=("Arial", 10, "bold"))
        self.temp_display_label.grid(row=7, column=1, sticky="W", padx=5)

        # Buttons (Moved to Row 8 to avoid overlapping and cluttering Row 7)
        self.btn_enter = Button(master, text='Enter', command=self.send_settings, width=10)
        self.btn_enter.grid(row=8, column=0, pady=10)

        self.btn_quit = Button(master, text='Quit', command=self.stop_plot, width=10)
        self.btn_quit.grid(row=8, column=1, pady=10)

        # --- START BACKGROUND THREADS AND LOOPS ---
        if self.ser:
            # Run serial reading in a background daemon thread
            self.serial_thread = threading.Thread(target=self.read_serial_data, daemon=True)
            self.serial_thread.start()
        
        # Start the non-blocking GUI polling loop
        self.root.after(50, self.update_gui)

    def send_settings(self):
        """Gets settings from GUI entries and transmits them over Serial."""
        if not self.ser or not self.ser.is_open:
            print("Serial port is not open.")
            return

        vals = [self.entries[f'e{i}'].get() for i in range(1, 7)]
        print(f"Sending: Setpoint={vals[0]}C, Ramp={vals[1]}s/C, P={vals[2]}, I={vals[3]}, D={vals[4]}, Offset={vals[5]}")
        
        input_string = f"<{','.join(vals)}>"
        try:
            self.ser.write(input_string.encode())
        except Exception as e:
            print(f"Error writing to serial: {e}")

    def read_serial_data(self):
        """Runs continuously in the background thread to read incoming serial data."""
        while self.continue_plotting:
            try:
                if self.ser and self.ser.is_open:
                    # readline() blocks gracefully with a 1-second timeout, releasing CPU cycles
                    raw_line = self.ser.readline()
                    if raw_line:
                        self.data_queue.put(raw_line)
            except Exception as e:
                print(f"Serial background read error: {e}")
                break

    def update_gui(self):
        """Runs periodically on the main thread. Decodes serial queue and updates UI labels."""
        try:
            # Drain all completed lines currently in the queue
            while not self.data_queue.empty():
                raw_data = self.data_queue.get_nowait()
                
                # Decode bytes to string and clean up whitespace
                line = raw_data.decode('utf-8', errors='ignore').strip()
                if not line:
                    continue
                
                # Parse incoming "timer , temp , setpoint"
                data_array = line.split(',')
                if len(data_array) >= 3:
                    try:
                        t = float(data_array[0].strip())
                        temp = float(data_array[1].strip())
                        setpoint = float(data_array[2].strip())

                        # Update data arrays
                        self.tempC.append(temp)
                        self.time.append(t)
                        self.sp.append(setpoint)
                        self.cnt += 1

                        if self.cnt > 200:
                            self.tempC.pop(0)
                            self.time.pop(0)
                            self.sp.pop(0)

                        # FIX: Simply update the existing label text
                        self.temp_display_label.config(text=f"{temp:.2f} °C")
                    except ValueError:
                        # Catch and ignore partial/corrupted data transmissions
                        pass
        except Exception as e:
            print(f"GUI Update Error: {e}")

        # Schedule this function to run again in 50 milliseconds
        if self.continue_plotting:
            self.root.after(50, self.update_gui)

    def stop_plot(self):
        """Cleans up resources and closes the application safely."""
        self.continue_plotting = False


        # Pasted command code to set temp to 0
        vals = ['0','10','2.0','0.5','.1','0']
        print(f"Sending: Setpoint={vals[0]}C, Ramp={vals[1]}s/C, P={vals[2]}, I={vals[3]}, D={vals[4]}, Offset={vals[5]}")
        
        input_string = f"<{','.join(vals)}>"
        try:
            self.ser.write(input_string.encode())
        except Exception as e:
            print(f"Error writing to serial: {e}")
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass
        self.root.destroy()

def main(port):
    root = tk.Tk()
    app = App(root, port)
    root.mainloop()

if __name__ == '__main__':
    main('COM5')