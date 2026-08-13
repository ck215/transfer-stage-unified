class BaseProbe:
    def __init__(self, serial_comm):
        self.serial_comm = serial_comm
        
        # Position variables
        self.pos_x = "0"
        self.pos_y = "0"
        self.pos_z = "0"
        
        # Connection variables
        self.controller_var = "None"
        self.serial_port = "/dev/ttys00X"
        
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
    
    def run_script(self, script_path=None):
        print(f"[BaseProbe] Running script not fully implemented. Path: {script_path}")
        
        self.active_claims = {}

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
                "manual_jog_speed": self.man_full_speed
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
    def __init__(self, serial_comm):
        super().__init__(serial_comm)
        self.x_step = "1"
        self.y_step = "1"
        self.z_step = "1"
        self.full_speed = "120"
        self.slow_speed = "0"
        self.brake_distance = "0"
        self.man_full_speed = "120"

    def get_params(self):
        params = super().get_params()
        params["slow_speed"] = self.slow_speed
        params["brake_distance"] = self.brake_distance
        return params


class ChuckPositioner(BaseProbe):
    def __init__(self, serial_comm):
        super().__init__(serial_comm)
        self.x_step = "2"
        self.y_step = "2"
        self.z_step = "2"
