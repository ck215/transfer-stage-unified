import sys
import os
from unittest.mock import MagicMock

# Mock pygame and serial which might not be installed
sys.modules['pygame'] = MagicMock()
mock_serial_lib = MagicMock()
mock_serial_lib.SerialException = Exception
sys.modules['serial'] = mock_serial_lib
sys.modules['serial.tools'] = MagicMock()
sys.modules['serial.tools.list_ports'] = MagicMock()

# Add the src directory to the python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))
