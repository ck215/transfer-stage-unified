import re

with open('src/controller/seiral.py', 'r') as f:
    content = f.read()

# Replace toggle with explicitly enable/disable
content = content.replace('self.ser.write("t".encode())', 'self.ser.write("e".encode())', 1) # Enable
content = content.replace('self.ser.write("t".encode())', 'self.ser.write("d".encode())', 1) # Disable

# Add threading lock and use it for thread safety
if 'import threading' not in content:
    content = content.replace('import struct', 'import struct\\nimport threading')

if 'self.port_lock = threading.Lock()' not in content:
    content = content.replace('self.ser = None\\n', 'self.ser = None\\n        self.port_lock = threading.Lock()\\n')

# Use locks and fix masking crashes
old_manual = '''            self.ser.write(packet)
            
        except pyserial.SerialTimeoutException as e:'''
new_manual = '''            with self.port_lock:
                self.ser.write(packet)
                
        except Exception as e:
            if pyserial and isinstance(e, pyserial.SerialTimeoutException):'''
content = content.replace(old_manual, new_manual)

old_auton = '''            self.ser.write(cmd_str.encode())

        except pyserial.SerialTimeoutException as e:'''
new_auton = '''            cmd_str = cmd_str[:64] # Limit length
            with self.port_lock:
                self.ser.write(cmd_str.encode())

        except Exception as e:
            if pyserial and isinstance(e, pyserial.SerialTimeoutException):'''
content = content.replace(old_auton, new_auton)

with open('src/controller/seiral.py', 'w') as f:
    f.write(content)
