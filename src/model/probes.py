from controller.seiral import serial
from controller.gamepad import ControllerPoller

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
        self.poller = ControllerPoller(controller_id, active_claims or {}, self.__class__.__name__) if controller_id and "None" not in controller_id else None
        
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
                        {"type": "entry", "text": "Controller ID:", "model_attr": "controller_var"},
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
                        {"type": "toggle", "model_attr": "system_enabled", 
                         "true_text": "System Enabled (Click to Disable)", 
                         "false_text": "System Disabled (Click to Enable)", 
                         "command": "toggle_enable"},
                        {"type": "button", "text": "ENTER AUTONOMOUS MODE", "command": "enter_auton", "bg": "darkgreen", "fg": "white"},
                        {"type": "button", "text": "ENTER MANUAL MODE", "command": "enter_manual", "bg": "blue", "fg": "white"},
                        {"type": "button", "text": "Start Stepping", "command": "macro_start_auton", "bg": "darkgreen", "fg": "white"},
                        {"type": "button", "text": "Full Stop", "command": "full_stop", "bg": "darkred", "fg": "white"},
                        {"type": "file_picker", "text": "Run Script", "command": "run_script"},
                        {"type": "button", "text": "Serial Reconnect", "command": "reconnect_serial", "bg": "gray", "fg": "black"},
                        {"type": "button", "text": "Color Test Window", "command": "color_test_window", "bg": "purple", "fg": "white"},
                        {"type": "button", "text": "Controller Selector", "command": "open_controller_selector", "bg": "orange", "fg": "black"},
                        {"type": "button", "text": "Controller Log Window", "command": "open_controller_log", "bg": "black", "fg": "white"}
                    ]
                }
            ]
        }

    def reconnect_serial(self):
        if self.serial_comm:
            self.serial_comm.close()
            import time
            time.sleep(1)
            self.serial_comm.__init__(self.serial_port)

    def color_test_window(self):
        print("[BaseProbe] Color test window requested")

    def open_controller_selector(self):
        print("[BaseProbe] Controller selector requested")

    def open_controller_log(self):
        print("[BaseProbe] Controller log window requested")

    def toggle_enable(self):
        if self.system_enabled:
            self.disable()
        else:
            self.enable()

    def send_stop_command(self):
        if self.serial_comm:
            params = self.get_params()
            params["command_code_manual"] = 0
            params["command_code_auton"] = 0
            self.serial_comm.send_autonomous_command(params)

    def enter_auton(self):
        self.auton_flag = True
        self.manual_flag = False
        self.send_stop_command()

    def enter_manual(self):
        self.auton_flag = False
        self.manual_flag = True
        self.send_stop_command()

    def macro_start_auton(self):
        self.auton_flag = True
        self.manual_flag = False
        self.send_autonomous_command()

    def run_script(self, script_path=None):
        if not script_path or not self.serial_comm:
            return
            
        print(f"[BaseProbe] Parsing script: {script_path}")
        self.enter_auton()
        
        try:
            from gcodeparser import GcodeParser
            with open(script_path, 'r') as f:
                gcode = f.read()
            parsed = GcodeParser(gcode)
            for line in parsed.lines:
                # Basic script sending logic
                self.serial_comm.ser.write((line.gcode_str + '\n').encode())
                import time
                time.sleep(0.1) # Simple pacing
        except Exception as e:
            from error_routing import ErrorRouter as ErrorPopupManager
            ErrorPopupManager.report_error("Script Execution Error", f"Error running script:\n{e}", e)

    def get_params(self):
        return {
            "x_step_size": self.x_step,             
            "y_step_size": self.y_step,              
            "z_step_size": self.z_step,              
            "full_speed": self.full_speed,           
            "slow_speed": 0,           
            "brake_distance": 0,   
            "x_dist": self.x_dist,                   
            "y_dist": self.y_dist,                   
            "z_dist": self.z_dist,  
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
                "x_stepSize": self.x_step,
                "y_stepSize": self.y_step,
                "z_stepSize": self.z_step,
                "dpad_LR": controller_params.get("dpad_LR", 0),
                "dpad_UD": controller_params.get("dpad_UD", 0),
                "LBumper": controller_params.get("LBumper", 0),
                "RBumper": controller_params.get("RBumper", 0),
                "manual_jog_speed": self.man_full_speed,
                "packet_format": self.packet_format
            }
            self.serial_comm.send_manual_mode_command(params)

    def read_position(self):
        if self.serial_comm:
            pos = self.serial_comm.read_position()
            if pos:
                self.pos_x, self.pos_y, self.pos_z = str(pos[0]), str(pos[1]), str(pos[2])

    def enable(self):
        if self.serial_comm:
            self.serial_comm.enable()
        self.system_enabled = True

    def disable(self):
        if self.serial_comm:
            self.serial_comm.disable()
        self.system_enabled = False
        self.full_stop()

    def full_stop(self):
        self.manual_flag = False
        self.auton_flag = False
        self.send_autonomous_command()


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
        schema = super().ui_schema
        # Find the Configuration section and insert the two new fields
        for section in schema["sections"]:
            if section["title"] == "Configuration":
                section["elements"].append({"type": "entry", "text": "Brake Speed (Slow):", "model_attr": "slow_speed"})
                section["elements"].append({"type": "entry", "text": "Brake Distance (steps):", "model_attr": "brake_distance"})
                break
        return schema

    def get_params(self):
        params = super().get_params()
        params["slow_speed"] = self.slow_speed
        params["brake_distance"] = self.brake_distance
        return params


class ChuckPositioner(BaseProbe):
    def __init__(self, port, controller_id, active_claims=None):
        super().__init__(port, controller_id, active_claims)
        self.x_step = "2"
        self.y_step = "2"
        self.z_step = "2"
