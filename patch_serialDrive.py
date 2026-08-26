import re
with open('src/serialDrive.py', 'r') as f:
    content = f.read()

old_enable = '''    def enable(self):
        self.ser.write("t".encode())'''
new_enable = '''    def enable(self):
        try:
            self.ser.write("e".encode())
        except Exception:
            self._handle_disconnect()'''
content = content.replace(old_enable, new_enable)

old_disable = '''    def disable(self):
        self.ser.write("t".encode())'''
new_disable = '''    def disable(self):
        try:
            self.ser.write("d".encode())
        except Exception:
            self._handle_disconnect()'''
content = content.replace(old_disable, new_disable)

old_auton = '''    def send_autonomous_command(self, params: dict):
        try:
            cmd_str = ('''
new_auton = '''    def send_autonomous_command(self, params: dict):
        try:
            # Type cast to float to prevent injection
            cmd_str = (
                f"{float(params.get('x_stepSize', 0))},"
                f"{float(params.get('y_stepSize', 0))},"
                f"{float(params.get('z_stepSize', 0))},"
                f"{float(params.get('dpad_LR', 0))},"
                f"{float(params.get('dpad_UD', 0))},"
                f"{float(params.get('RBumper', 0))},"
                f"{float(params.get('LBumper', 0))},"
                f"{float(params.get('manual_jog_speed', 0))},"
                f"{int(params.get('command_code_manual', 0))},"
                f"{int(params.get('command_code_auton', 0))}\\n"
            )
            if len(cmd_str) > 64: return
'''
# Actually wait, I need to properly replace send_autonomous_command. Let's just use `sed` or `replace_file_content` for safety.
