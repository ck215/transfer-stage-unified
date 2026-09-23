# --------- Necessary Libraries ----------
import serial
import time
import sys
import struct

PACKET_FORMAT = '<BBfffhhhhhhh'
START_MARKER = 0xAA

# HOTFIX: step sizes and jog speed arrive as RAW Tk Entry text ("400", "0.5", "", " 12 ").
#         A bare int() raised ValueError on a decimal or an empty box, the generic except
#         swallowed it, and NO packet was written -- and the firmware has no host-liveness
#         timeout, so it held the last velocity it received (the operator's "drift").
#         Coerce the text instead of dropping the packet.
def _as_int(value, default=0):
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default

# ------ Serial Simulation Setup --------

# Dedicated class for serial communication with Arduino
class SerialArduino:

    # Class constructor
    def __init__(self, port='SIM', baud_rate=500000):

        # Communation Rate
        self.BAUD_RATE = baud_rate

        # Port Name
        self.SERIAL_PORT = port

        # Empty serial object
        self.ser = None

        # NEW: Buffer for incoming serial data from firmware
        self._read_buffer = ""
        if self.SERIAL_PORT == 'SIM':
            print("[SerialDrive] Running in SIMULATOR mode. No serial connection will be established.")
            return
        
        try:
            print("[SerialDrive] Establishing Serial Connection...")
            self.ser = serial.Serial(
                self.SERIAL_PORT,
                self.BAUD_RATE,
                timeout=1,
                write_timeout=1
            )

            # Wait for the connection to establish
            time.sleep(1) 
            print("[SerialDrive] Serial Connection Established, Arduino Ready")
        
        # Exception handling
        except serial.SerialException as e:
            print(f"[SerialDrive] Error establishing serial connection: {e}")
        except Exception as e:
            print(f"[SerialDrive] Unexpected error: {e}")
            sys.exit(1)
        finally:
            print("[SerialDrive] Finish SerialDrive __init__")

    # Helper to verify serial connection before sending data
    def _verify_serial(self):
        if self.ser is None or not self.ser.is_open:
            print("[SerialDrive] Error: Serial connection not established.")
            return False
        return True

    # NEW: Read and parse absolute position data sent by firmware ("POS:x,y,z\n").
    #      Returns the latest (x, y, z) tuple from boot-time zero, or None if no new data.
    def read_position(self):
        if not self._verify_serial():
            return None

        try:
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
            print(f"[SerialDrive] Error reading position: {e}")
            return None

    # Function to send autonomous command
    def send_autonomous_command(self, params):
        
        # Port not open, do nothing
        if not self._verify_serial():
            return

        try:
            COMMAND_CODE_AUTON = params['command_code_auton']
            COMMAND_CODE_MANUAL = params['command_code_manual']
            empty_data = "0"  # Placeholder for unused target_steps

            # Construct f string for 12-field command
            # HOTFIX: the numeric fields are raw Entry text; format them as ints so
            #         "0.5" and "" become "0" here rather than silently changing
            #         meaning inside the firmware's toInt() parse.
            command = (
                f"{_as_int(params['x_step_size'])},{_as_int(params['y_step_size'])},{_as_int(params['z_step_size'])},"
                f"{empty_data},"
                f"{_as_int(params['full_speed'])},{params['slow_speed']},{params['brake_distance']},"
                f"{_as_int(params['x_dist'])},{_as_int(params['y_dist'])},{_as_int(params['z_dist'])},"
                f"{COMMAND_CODE_MANUAL},{COMMAND_CODE_AUTON}\n"
            )

            print(f"[SerialDrive] Sending 12-Field AUTON Command: {command.strip()}")
            self.ser.write(command.encode('utf-8'))    # type: ignore

        # Exception handling
        except serial.SerialTimeoutException:
            print("[SerialDrive] WRITE TIMEOUT ERROR (Auton)")
            print("The serial write operation timed out.")
        except Exception as e:
            print(f"[SerialDrive] Error sending auton data: {e}")

    # Function to send manual command, looped by manual mode loop
    def send_manual_mode_command(self, params):
        
        # Do nothing if the serial port is closed
        if not self._verify_serial():
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
            combined_bumpers = int(params.get('LBumper')) - int(params.get('RBumper'))

            # FIX #8: Removed errant spaces after commas in fields 3-4 that produced
            #         values like " 0" and " 120" instead of "0" and "120"
            packet = struct.pack(
                PACKET_FORMAT,
                START_MARKER,
                1,
                float(params['x_axisStatus']),
                float(params['y_axisStatus']),
                combined_z_axis_status,
                # HOTFIX: these four are raw Entry text -- coerce, never drop the packet
                _as_int(params['x_stepSize']),
                _as_int(params['y_stepSize']),
                _as_int(params['z_stepSize']),
                int(params['dpad_LR']),
                int(params['dpad_UD']),
                (combined_bumpers),
                _as_int(params['manual_jog_speed']),
            )

            print(f"[SerialDrive] Sending 12-Field MANUAL State: {packet}")
            self.ser.write(packet)  # type: ignore
            self.ser.flush()                         # type: ignore
            
        # Exception handling
        # HOTFIX: a manual packet that never lands leaves the firmware running at its
        #         last velocity, so every failure path now also tries to command STOP.
        except serial.SerialTimeoutException:
            print("[SerialDrive] WRITE TIMEOUT ERROR (Manual)")
            print("The serial write operation timed out.")
            self._write_stop_packet()
        except Exception as e:
            print(f"[SerialDrive] Error sending manual data: {e}")
            self._write_stop_packet()

    # HOTFIX: best-effort fail-safe. Mode byte 0 is the firmware's handleAllStop.
    #         Never raises -- callers of send_manual_mode_command rely on that.
    def _write_stop_packet(self):
        try:
            stop_packet = struct.pack(
                PACKET_FORMAT,
                START_MARKER,
                0,
                0.0, 0.0, 0.0,
                0, 0, 0, 0, 0, 0, 0,
            )
            print("[SerialDrive] Manual packet failed; sending explicit STOP packet.")
            self.ser.write(stop_packet)  # type: ignore
            self.ser.flush()             # type: ignore
        except Exception as e:
            print(f"[SerialDrive] Error sending STOP packet: {e}")

    def enable(self):
        if not self._verify_serial():
            raise ValueError("[SerialDrive] Arduino not detected. Cannot enable system.")
        self.ser.write("t".encode('utf-8'))

    def disable(self):
        if not self._verify_serial():
            raise ValueError("[SerialDrive] Arduino not detected. Cannot disable system.")
        self.ser.write("t".encode('utf-8'))

    # Closes serial connection
    def close(self):
        if self.ser and self.ser.is_open:
            print("[SerialDrive] Closing serial port.")
            self.ser.close()