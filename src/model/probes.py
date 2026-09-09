import math
import time
import copy
import threading
from controller.seiral import serial
from controller.gamepad import ControllerPoller
from error_routing import ErrorRouter as ErrorPopupManager

def _num(value, default, *, minimum=None, integer=False):
    try:
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return default
    except (TypeError, ValueError):
        return default
    if minimum is not None and v < minimum:
        v = minimum
    return int(v) if integer else v

class BaseProbe:
    def __init__(self, port, controller_id, active_claims=None):
        self.serial_comm = serial(port) if port and port != "None" else None
        
        self.packet_format = '<BBffffffffff'  # Standardized 42-byte float format
        
        # Position variables
        self.pos_x = "0"
        self.pos_y = "0"
        self.pos_z = "0"
        
        # Connection variables
        self.controller_var = controller_id
        self.serial_port = port
        self.active_claims = active_claims or {}
        self.poller = ControllerPoller(controller_id, self.active_claims, self.__class__.__name__)
        
        # Step sizes
        self.x_step = "16"
        self.y_step = "16"
        self.z_step = "16"
        
        # Step counts (distances)
        self.x_dist = "0"
        self.y_dist = "0"
        self.z_dist = "0"
        
        # Velocity control
        self.full_speed = "400"
        self.man_full_speed = "400"
        
        # State flags
        self.system_enabled = False
        self.auton_flag = False
        self.manual_flag = False
        self.is_stepping = False

    @property
    def ui_schema(self):
        return {
            "sections": [
                {
                    "title": "Coordinate Frame",
                    "elements": [
                        {"type": "readonly", "text": "X Position:", "model_attr": "pos_x"},
                        {"type": "readonly", "text": "Y Position:", "model_attr": "pos_y"},
                        {"type": "readonly", "text": "Z Position:", "model_attr": "pos_z"}
                    ]
                },
                {
                    "title": "Configuration",
                    "elements": [
                        {"type": "entry", "text": "Serial Port:", "model_attr": "serial_port"},
                        {"type": "dropdown", "text": "Controller ID:", "model_attr": "controller_var",
                         "options_command": "get_available_controllers", "command": "set_controller"},
                        {"type": "entry", "text": "X Step Size:", "model_attr": "x_step"},
                        {"type": "entry", "text": "Y Step Size:", "model_attr": "y_step"},
                        {"type": "entry", "text": "Z Step Size:", "model_attr": "z_step"},
                        {"type": "entry", "text": "Target X Dist:", "model_attr": "x_dist"},
                        {"type": "entry", "text": "Target Y Dist:", "model_attr": "y_dist"},
                        {"type": "entry", "text": "Target Z Dist:", "model_attr": "z_dist"},
                        {"type": "entry", "text": "Autonomous Speed:", "model_attr": "full_speed"},
                        {"type": "entry", "text": "Manual Speed:", "model_attr": "man_full_speed"}
                    ]
                },
                {
                    "title": "System Control",
                    "elements": [
                        {"type": "toggle", "model_attr": "auton_flag",
                         "true_text": "AUTONOMOUS MODE (Click to Stop)",
                         "false_text": "Enter Autonomous Mode", "command": "toggle_auton"},
                        {"type": "toggle", "model_attr": "manual_flag",
                         "true_text": "MANUAL MODE (Click to Stop)",
                         "false_text": "Enter Manual Mode", "command": "toggle_manual"},
                        {"type": "button", "text": "Start Stepping", "command": "macro_start_auton", "bg": "darkgreen", "fg": "white"},
                        {"type": "button", "text": "Full Stop", "command": "full_stop", "bg": "darkred", "fg": "white"},
                        {"type": "file_picker", "text": "Run Script", "command": "run_script"},
                        {"type": "button", "text": "Serial Reconnect", "command": "reconnect_serial", "bg": "gray", "fg": "black"},
                        {"type": "button", "text": "Controller Log Window", "command": "open_controller_log", "bg": "black", "fg": "white"}
                    ]
                }
            ]
        }

    def reconnect_serial(self):
        print(f"[{self.__class__.__name__}] Reconnecting serial port {self.serial_port}...")
        if self.serial_comm:
            try:
                self.serial_comm.close()
            except Exception as e:
                print(f"[{self.__class__.__name__}] Error closing existing serial connection: {e}")
        time.sleep(1)
        if self.serial_port and self.serial_port != "None":
            self.serial_comm = serial(self.serial_port)
        else:
            self.serial_comm = None
        self.system_enabled = False
        self.auton_flag = False
        self.manual_flag = False

    def get_available_controllers(self):
        if self.poller:
            return self.poller.get_physical_controllers()
        return []

    def set_controller(self, controller_id):
        print(f"[{self.__class__.__name__}] Swapping controller to: {controller_id}")
        self.controller_var = controller_id
        if self.poller:
            self.poller.set_controller(controller_id)
        else:
            self.poller = ControllerPoller(controller_id, self.active_claims, self.__class__.__name__)

    def open_controller_log(self):
        print(f"[{self.__class__.__name__}] Controller log window requested")

    def toggle_manual(self):
        if self.manual_flag:
            self.full_stop()
        else:
            self.enter_manual()

    def toggle_auton(self):
        if self.auton_flag:
            self.full_stop()
        else:
            self.enter_auton()

    def send_stop_command(self):
        if self.serial_comm:
            params = {
                "x_step_size": 0, "y_step_size": 0, "z_step_size": 0,
                "full_speed": 0, "slow_speed": 0, "brake_distance": 0,
                "x_dist": 0, "y_dist": 0, "z_dist": 0,
                "command_code_manual": 0, "command_code_auton": 0
            }
            self.serial_comm.send_autonomous_command(params)

    def enter_auton(self):
        if not self.enable():
            return
        self.auton_flag = True
        self.manual_flag = False
        self.send_stop_command()

    def enter_manual(self):
        if not self.enable():
            return
        self.auton_flag = False
        self.manual_flag = True
        self.send_stop_command()

    def macro_start_auton(self):
        if not self.enable():
            return
        self.auton_flag = True
        self.manual_flag = False
        self.is_stepping = True
        self.send_autonomous_command()

    def run_script(self, script_path=None):
        if not script_path or not self.serial_comm:
            return
            
        print(f"[{self.__class__.__name__}] Parsing and executing script: {script_path}")
        self.enter_auton()

        def _execute():
            self.is_stepping = True
            try:
                import gcodeparser
                with open(script_path, 'r', encoding="utf-8") as f:
                    gcode_content = f.read()
                
                # Check if serial connection is active
                if not getattr(self.serial_comm, 'ser', None):
                    raise AttributeError("Serial connection not active.")
                
                # Support both GcodeParser and parse_gcode_lines
                if hasattr(gcodeparser, 'GcodeParser'):
                    parsed = gcodeparser.GcodeParser(gcode_content)
                    lines = parsed.lines
                elif hasattr(gcodeparser, 'parse_gcode_lines'):
                    import io
                    lines = list(gcodeparser.parse_gcode_lines(io.StringIO(gcode_content), include_comments=False))
                else:
                    lines = []

                for line in lines:
                    gcode_str = getattr(line, 'gcode_str', str(line))
                    params = getattr(line, 'params', {})
                    command = getattr(line, 'command', ('', 0))
                    
                    if command and command[0] == 'G':
                        x_dist = params.get('X', 0)
                        y_dist = params.get('Y', 0)
                        z_dist = params.get('Z', 0)
                        feedrate = params.get('F', self.full_speed)
                        
                        cmd_params = {
                            "x_step_size": self.x_step,
                            "y_step_size": self.y_step,
                            "z_step_size": self.z_step,
                            "full_speed": str(feedrate),
                            "slow_speed": 0,
                            "brake_distance": 0,
                            "x_dist": str(x_dist),
                            "y_dist": str(y_dist),
                            "z_dist": str(z_dist),
                            "command_code_manual": 0,
                            "command_code_auton": 1
                        }
                        self.serial_comm.send_autonomous_command(cmd_params)
                        
                        x_steps = abs(float(self.x_step) * float(x_dist))
                        y_steps = abs(float(self.y_step) * float(y_dist))
                        z_steps = abs(float(self.z_step) * float(z_dist))
                        dist = math.sqrt(x_steps**2 + y_steps**2 + z_steps**2)
                        speed = float(feedrate) if float(feedrate) > 0 else float(self.full_speed)
                        duration = (dist / speed) + 0.05 if speed > 0 else 0.1
                        time.sleep(duration)
                    elif ',' in gcode_str:
                        if self.serial_comm.ser:
                            raw_cmd = gcode_str.strip() + '\n'
                            self.serial_comm.ser.write(raw_cmd.encode('utf-8'))
                        time.sleep(0.1)
                    else:
                        if self.serial_comm.ser:
                            self.serial_comm.ser.write((gcode_str + '\n').encode('utf-8'))
                        time.sleep(0.1)
            except Exception as e:
                print(f"[{self.__class__.__name__}] Script execution error: {e}")
                ErrorPopupManager.report_error("Script Execution Error", f"Error running script:\n{e}", e)
            finally:
                self.full_stop()

        threading.Thread(target=_execute, daemon=True).start()

    def get_params(self):
        return {
            "x_step_size": _num(self.x_step, 16, minimum=1, integer=True),             
            "y_step_size": _num(self.y_step, 16, minimum=1, integer=True),              
            "z_step_size": _num(self.z_step, 16, minimum=1, integer=True),              
            "full_speed": _num(self.full_speed, 400, minimum=1),           
            "slow_speed": 0,           
            "brake_distance": 0,   
            "x_dist": _num(self.x_dist, 0),                   
            "y_dist": _num(self.y_dist, 0),                   
            "z_dist": _num(self.z_dist, 0),  
            "command_code_manual": int(self.manual_flag),               
            "command_code_auton": int(self.auton_flag)                      
        }

    def send_autonomous_command(self):
        if self.serial_comm:
            self.serial_comm.send_autonomous_command(self.get_params())

    def send_manual_mode_command(self, controller_params):
        if self.serial_comm:
            params = {
                "x_axisStatus": controller_params.get("x_axisStatus", 0.0),
                "y_axisStatus": controller_params.get("y_axisStatus", 0.0),
                "z_axisStatusR": controller_params.get("z_axisStatusR", -1.0),
                "z_axisStatusL": controller_params.get("z_axisStatusL", -1.0),
                "x_stepSize": _num(self.x_step, 16, minimum=1, integer=True),
                "y_stepSize": _num(self.y_step, 16, minimum=1, integer=True),
                "z_stepSize": _num(self.z_step, 16, minimum=1, integer=True),
                "dpad_LR": controller_params.get("dpad_LR", 0),
                "dpad_UD": controller_params.get("dpad_UD", 0),
                "LBumper": controller_params.get("LBumper", 0),
                "RBumper": controller_params.get("RBumper", 0),
                "manual_jog_speed": _num(self.man_full_speed, 400, minimum=1),
                "packet_format": self.packet_format
            }
            self.serial_comm.send_manual_mode_command(params)

    def read_position(self):
        if self.serial_comm:
            pos = self.serial_comm.read_position()
            if pos:
                self.pos_x, self.pos_y, self.pos_z = str(pos[0]), str(pos[1]), str(pos[2])

    def enable(self):
        if not self.system_enabled:
            if self.serial_comm:
                try:
                    self.serial_comm.enable()
                except ValueError as e:
                    print(f"[{self.__class__.__name__}] {e}")
                    ErrorPopupManager.report_warning("Enable Failed", str(e))
                    return False
            self.system_enabled = True
        return True

    def toggle_enable(self):
        if self.system_enabled:
            self.disable()
        else:
            self.enable()

    def _stop_and_disarm(self):
        self.manual_flag = False
        self.auton_flag = False
        self.is_stepping = False
        self.send_stop_command()
        if self.system_enabled:
            if self.serial_comm:
                try:
                    self.serial_comm.disable()
                except ValueError as e:
                    print(f"[{self.__class__.__name__}] {e}")
            self.system_enabled = False

    def disable(self):
        self._stop_and_disarm()

    def full_stop(self):
        self._stop_and_disarm()


class StepperProbe(BaseProbe):
    pass


class DCProbe(BaseProbe):
    def __init__(self, port, controller_id, active_claims=None):
        super().__init__(port, controller_id, active_claims)
        self.packet_format = '<BBffffffffff'
        self.x_step = "1"
        self.y_step = "1"
        self.z_step = "1"
        self.full_speed = "120"
        self.slow_speed = "0"
        self.brake_distance = "0"
        self.man_full_speed = "120"

    @property
    def ui_schema(self):
        schema = copy.deepcopy(super().ui_schema)
        # Find the Configuration section and insert the two new fields
        for section in schema["sections"]:
            if section["title"] == "Configuration":
                section["elements"].append({"type": "entry", "text": "Brake Speed (Slow):", "model_attr": "slow_speed"})
                section["elements"].append({"type": "entry", "text": "Brake Distance (steps):", "model_attr": "brake_distance"})
                break
        return schema

    def get_params(self):
        params = super().get_params()
        params["slow_speed"] = _num(self.slow_speed, 0)
        params["brake_distance"] = _num(self.brake_distance, 0)
        return params


class ChuckPositioner(BaseProbe):
    def __init__(self, port, controller_id, active_claims=None):
        super().__init__(port, controller_id, active_claims)
        self.x_step = "2"
        self.y_step = "2"
        self.z_step = "2"
