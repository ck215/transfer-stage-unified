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

        # Threading lock for thread-safe serial port access
        self._lock = threading.RLock()

        # NEW: Buffer for incoming serial data from firmware
        self._read_buffer = ""
        self.device_type = None

        if self.SERIAL_PORT == 'SIM':
            msg = "[SerialDrive] Running in SIMULATOR mode. No serial connection will be established."
            print(msg)
            ErrorPopupManager.report_info("Simulator Mode", msg)
            return
        
        try:
            print("[SerialDrive] Establishing Serial Connection...")
            self.ser = pyserial.Serial(
                self.SERIAL_PORT,
                self.BAUD_RATE,
                timeout=1,
                write_timeout=1
            )

            # Wait up to 1.5s for the Arduino to boot and respond
            print(f"[SerialDrive] Pinging port {self.SERIAL_PORT} to verify connection...")
            verified = False
            
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            
            time.sleep(1.5) # Wait for bootloader
            start_time = time.time()
            buffer = ""
            while (time.time() - start_time < 3.0):
                try:
                    self.ser.write(b"s\n")
                except Exception:
                    break
                    
                if self.ser.in_waiting > 0:
                    buffer += self.ser.read(self.ser.in_waiting).decode('utf-8', errors='ignore')
                    if "DEV:" in buffer:
                        verified = True
                        for line in buffer.splitlines():
                            if "DEV:" in line:
                                self.device_type = line.split("DEV:")[1].strip()
                        break
                time.sleep(0.05)
                
            if verified:
                print(f"[SerialDrive] Serial Connection Verified! Arduino Ready on {self.SERIAL_PORT}")
            else:
                msg = f"[WARNING] Port {self.SERIAL_PORT} opened, but no Arduino response received. Operating blind."
                print(msg)
                ErrorPopupManager.report_warning("Serial Connection Warning", msg)
        
        # Exception handling
        except pyserial.SerialException as e:
            msg = f"[WARNING] Error establishing serial connection to {self.SERIAL_PORT}:\n{e}\nOperating blind without hardware."
            print(msg)
            ErrorPopupManager.report_warning("Serial Exception", msg, e)
        except Exception as e:
            msg = f"[WARNING] Unexpected error:\n{e}\nOperating blind without hardware."
            print(msg)
            ErrorPopupManager.report_error("Unexpected Serial Error", msg, e)
        finally:
            print("[SerialDrive] Finish SerialDrive __init__")

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
                    raw = self.ser.read(self.ser.in_waiting).decode('utf-8', errors='ignore')   # type: ignore
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

    def enable(self):
        if not self._verify_serial(verbose=True):
            raise ValueError("[SerialDrive] Arduino not detected. Cannot enable system.")
        with self._lock:
            self.ser.write("e".encode('utf-8'))

    def disable(self):
        if not self._verify_serial(verbose=True):
            raise ValueError("[SerialDrive] Arduino not detected. Cannot disable system.")
        with self._lock:
            self.ser.write("d".encode('utf-8'))

    # Closes serial connection
    def close(self):
        with self._lock:
            if self.ser and self.ser.is_open:
                print("[SerialDrive] Closing serial port.")
                self.ser.close()


# Aliases for backwards compatibility with legacy stable branch and standard naming
SerialArduino = serial
Serial = serial