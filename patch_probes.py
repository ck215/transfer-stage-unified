import re

with open('src/model/probes.py', 'r') as f:
    content = f.read()

# 1. Replace Enable toggle with Manual/Auton toggles in ui_schema
old_schema = '''                        {"type": "toggle", "model_attr": "system_enabled", 
                         "true_text": "Disable System", "false_text": "Enable System", 
                         "command": "toggle_enable"},
                        {"type": "button", "text": "ENTER MANUAL MODE", "command": "enter_manual", "bg": "blue", "fg": "white"},
                        {"type": "button", "text": "ENTER AUTONOMOUS MODE", "command": "enter_auton", "bg": "green", "fg": "white"},'''

new_schema = '''                        {"type": "toggle", "model_attr": "manual_flag", 
                         "true_text": "Manual Mode Active (Click to Disable)", 
                         "false_text": "Enter Manual Mode", 
                         "command": "toggle_manual"},
                        {"type": "toggle", "model_attr": "auton_flag", 
                         "true_text": "Autonomous Mode Active (Click to Disable)", 
                         "false_text": "Enter Autonomous Mode", 
                         "command": "toggle_auton"},'''
content = content.replace(old_schema, new_schema)

# 2. Replace toggle_enable with toggle_manual and toggle_auton
old_toggle = '''    def toggle_enable(self):
        if self.system_enabled:
            self.disable()
        else:
            self.enable()'''

new_toggle = '''    def toggle_manual(self):
        if self.manual_flag:
            self.disable()
            self.manual_flag = False
        else:
            self.enable()
            self.enter_manual()

    def toggle_auton(self):
        if self.auton_flag:
            self.disable()
            self.auton_flag = False
        else:
            self.enable()
            self.enter_auton()'''
content = content.replace(old_toggle, new_toggle)

# 3. Fix send_stop_command bug (don't zero out transition state flags)
old_stop = '''    def send_stop_command(self):
        if self.serial_comm:
            params = self.get_params()
            params["command_code_manual"] = 0
            params["command_code_auton"] = 0
            self.serial_comm.send_autonomous_command(params)'''
new_stop = '''    def send_stop_command(self):
        if self.serial_comm:
            params = self.get_params()
            self.serial_comm.send_autonomous_command(params)'''
content = content.replace(old_stop, new_stop)

# 4. Fix run_script (add finally block, missing gcodeparser, run async to avoid blocking main thread)
old_run = '''    def run_script(self, script_path=None):
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
                self.serial_comm.ser.write((line.gcode_str + '\\n').encode())
                import time
                time.sleep(0.1)  
        except Exception as e:
            print(f"[BaseProbe] Script execution error: {e}")
            from error_routing import ErrorRouter as ErrorPopupManager
            ErrorPopupManager.report_error("Script Execution Error", f"Error running script:\\n{e}", e)'''
            
new_run = '''    def run_script(self, script_path=None):
        if not script_path or not self.serial_comm:
            return
            
        print(f"[BaseProbe] Parsing script: {script_path}")
        self.enter_auton()
        
        import threading
        def _execute():
            try:
                from gcodeparser import GcodeParser
                with open(script_path, 'r', encoding="utf-8") as f:
                    gcode = f.read()
                parsed = GcodeParser(gcode)
                for line in parsed.lines:
                    self.serial_comm.ser.write((line.gcode_str + '\\n').encode())
                    import time
                    time.sleep(0.1)  
            except Exception as e:
                print(f"[BaseProbe] Script execution error: {e}")
                from error_routing import ErrorRouter as ErrorPopupManager
                ErrorPopupManager.report_error("Script Execution Error", f"Error running script:\\n{e}", e)
            finally:
                self.full_stop()
        threading.Thread(target=_execute, daemon=True).start()'''
content = content.replace(old_run, new_run)

with open('src/model/probes.py', 'w') as f:
    f.write(content)
