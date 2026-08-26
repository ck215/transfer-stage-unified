import sys
import os
from unittest.mock import MagicMock

# Mock libraries that might not be installed
sys.modules['pygame'] = MagicMock()
sys.modules['pygame'].error = Exception

mock_serial_lib = MagicMock()
mock_serial_lib.SerialException = Exception
mock_serial_lib.SerialTimeoutException = Exception
sys.modules['serial'] = mock_serial_lib
sys.modules['serial.tools'] = MagicMock()
sys.modules['serial.tools.list_ports'] = MagicMock()

sys.modules['PIL'] = MagicMock()
sys.modules['mss'] = MagicMock()
sys.modules['numpy'] = MagicMock()
sys.modules['matplotlib'] = MagicMock()
sys.modules['matplotlib.figure'] = MagicMock()
sys.modules['matplotlib.backends'] = MagicMock()
sys.modules["matplotlib.backends.backend_qtagg"] = MagicMock()
sys.modules["matplotlib.figure"] = MagicMock()

sys.modules['matplotlib.backends.backend_tkagg'] = MagicMock()
sys.modules['matplotlib.colors'] = MagicMock()
sys.modules['tkinter'] = MagicMock()
sys.modules['tkinter.ttk'] = MagicMock()
sys.modules['tkinter.filedialog'] = MagicMock()
sys.modules['tkinter.messagebox'] = MagicMock()

# Add the src directory to the python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))
