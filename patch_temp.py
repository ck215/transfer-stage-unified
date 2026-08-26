import re
with open('src/model/temperature_system.py', 'r') as f:
    content = f.read()

old_ramp = '''            spdelay = 60.0 / float(self.ramp_rate)

            # E.g., <temp,spdelay,on_off>
            command = f"<{self.temperature},{spdelay},{self.enabled}>\\n"'''
new_ramp = '''            ramp_val = max(0.01, float(self.ramp_rate))
            spdelay = min(60000.0, 60.0 / ramp_val)

            # E.g., <temp,spdelay,on_off>
            command = f"<{float(self.temperature):.2f},{spdelay:.2f},{self.enabled}>\\n"'''
content = content.replace(old_ramp, new_ramp)

with open('src/model/temperature_system.py', 'w') as f:
    f.write(content)
