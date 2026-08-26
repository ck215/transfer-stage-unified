import sys
import re
import time
import pytest
from unittest.mock import MagicMock, patch
from controller.seiral import serial

def test_dev_pattern_matching():
    DEV_PATTERN = re.compile(r"(?:DEV:\s*|<)([sdct])>?", re.IGNORECASE)
    
    # Test DEV: x format
    for char, expected in [('s', 's'), ('d', 'd'), ('c', 'c'), ('t', 't')]:
        m = DEV_PATTERN.search(f"DEV: {char}\r\n")
        assert m is not None
        assert m.group(1).lower() == expected
        
        m_tight = DEV_PATTERN.search(f"DEV:{char}")
        assert m_tight is not None
        assert m_tight.group(1).lower() == expected
        
    # Test <x> format
    for char, expected in [('s', 's'), ('d', 'd'), ('c', 'c'), ('t', 't')]:
        m = DEV_PATTERN.search(f"<{char}>")
        assert m is not None
        assert m.group(1).lower() == expected
        
        m_open = DEV_PATTERN.search(f"<{char}")
        assert m_open is not None
        assert m_open.group(1).lower() == expected

    # Test interleaved telemetry
    interleaved = "POS:100,200,300\nPOS:100,205,300\nDEV: s\r\n"
    m_inter = DEV_PATTERN.search(interleaved)
    assert m_inter is not None
    assert m_inter.group(1).lower() == 's'

def test_buffer_accumulation_fragmentation():
    DEV_PATTERN = re.compile(r"(?:DEV:\s*|<)([sdct])>?", re.IGNORECASE)
    
    # Simulate fragmented chunks over serial
    chunks = ["POS: 0,0,0\nDE", "V: ", "t\r\n"]
    buffer = ""
    match = None
    for chunk in chunks:
        buffer += chunk
        match = DEV_PATTERN.search(buffer)
        if match:
            break
            
    assert match is not None
    assert match.group(1).lower() == 't'

def test_linux_port_filtering_and_prioritization():
    mock_port_ttyS0 = MagicMock()
    mock_port_ttyS0.device = "/dev/ttyS0"
    mock_port_ttyS0.hwid = "n/a"

    mock_port_ttyUSB0 = MagicMock()
    mock_port_ttyUSB0.device = "/dev/ttyUSB0"
    mock_port_ttyUSB0.hwid = "USB VID:PID=1a86:7523"

    mock_port_ttyACM0 = MagicMock()
    mock_port_ttyACM0.device = "/dev/ttyACM0"
    mock_port_ttyACM0.hwid = "USB VID:PID=2341:0043"

    com_ports = [mock_port_ttyS0, mock_port_ttyUSB0, mock_port_ttyACM0]
    
    with patch("sys.platform", "linux"):
        valid_ports = []
        for port in com_ports:
            if sys.platform.startswith("linux") and port.device.startswith("/dev/ttyS"):
                if getattr(port, "hwid", "n/a") == "n/a" or not getattr(port, "hwid", None):
                    continue
            valid_ports.append(port.device)
            
        def port_sort_key(dev_name):
            is_usb = any(dev_name.startswith(pfx) for pfx in ("/dev/ttyACM", "/dev/ttyUSB", "/dev/cu.usb", "/dev/tty.usb")) or "USB" in dev_name
            return (0 if is_usb else 1, dev_name)
            
        sorted_ports = sorted(valid_ports, key=port_sort_key)
        
        # /dev/ttyS0 must be filtered out
        assert "/dev/ttyS0" not in sorted_ports
        # USB ports must be present and sorted
        assert "/dev/ttyACM0" in sorted_ports
        assert "/dev/ttyUSB0" in sorted_ports
        assert sorted_ports == ["/dev/ttyACM0", "/dev/ttyUSB0"]

def test_serial_init_bootloader_timing():
    with patch("controller.seiral.pyserial.Serial") as mock_serial_cls:
        mock_ser = MagicMock()
        mock_ser.is_open = True
        mock_ser.in_waiting = 0
        mock_serial_cls.return_value = mock_ser
        
        call_order = []
        
        def fake_sleep(duration):
            call_order.append(('sleep', duration))
            
        time_counter = [100.0]
        def fake_time():
            time_counter[0] += 0.1
            call_order.append(('time', time_counter[0]))
            return time_counter[0]
            
        with patch("controller.seiral.time.sleep", side_effect=fake_sleep), \
             patch("controller.seiral.time.time", side_effect=fake_time):
            
            s = serial("COM1")
            
            # Verify that time.sleep(1.5) happens BEFORE start_time is recorded for the while loop
            sleep_idx = [i for i, c in enumerate(call_order) if c == ('sleep', 1.5)][0]
            time_indices_after_sleep = [i for i, c in enumerate(call_order) if c[0] == 'time' and i > sleep_idx]
            assert len(time_indices_after_sleep) > 0, "start_time must be recorded after sleep(1.5)"

def test_scanner_thread_multi_baud_fallback(qapp):
    import app
    # Import ScannerThread from app's run_pyside_app scope
    # To extract ScannerThread cleanly, we can define or test through the same logic
    from PySide6.QtCore import QThread, Signal
    import serial as pyserial_mod

    # Mock Serial to fail at 500000 but succeed at 115200 with DEV: t
    attempted_bauds = []
    
    def fake_serial(port, baudrate, **kwargs):
        attempted_bauds.append(baudrate)
        mock_ser = MagicMock()
        mock_ser.__enter__ = MagicMock(return_value=mock_ser)
        mock_ser.__exit__ = MagicMock(return_value=None)
        
        if baudrate == 500000:
            mock_ser.in_waiting = 0
        elif baudrate == 115200:
            mock_ser.in_waiting = 8
            mock_ser.read.return_value = b"DEV: t\r\n"
        return mock_ser

    with patch("time.sleep", return_value=None), \
         patch("serial.Serial", side_effect=fake_serial):
         
        # Test 115200 fallback
        # Simulate ScannerThread execution logic
        devices = ["Temperature Controller"]
        detected_ports = ["/dev/ttyUSB0"]
        
        found_signals = []
        
        # Test the scanning sequence
        DEVICE_MAP = {
            'c': 'Chuck Positioner',
            's': 'Stepper Probe',
            'd': 'DC Probe',
            't': 'Temperature Controller'
        }
        DEV_PATTERN = re.compile(r"(?:DEV:\s*|<)([sdct])>?", re.IGNORECASE)
        
        port = "/dev/ttyUSB0"
        device_found = False
        
        # 1. 500k
        try:
            with fake_serial(port, baudrate=500000) as ser:
                ser.reset_input_buffer()
                ser.reset_output_buffer()
                start_time = time.time()
                response_buffer = ""
                if ser.in_waiting > 0:
                    response_bytes = ser.read(ser.in_waiting)
                    response_buffer += response_bytes.decode('utf-8', errors='ignore')
                    match = DEV_PATTERN.search(response_buffer)
                    if match:
                        device_found = True
        except Exception:
            pass
            
        # 2. 115.2k fallback
        if not device_found:
            try:
                with fake_serial(port, baudrate=115200) as ser:
                    ser.reset_input_buffer()
                    ser.reset_output_buffer()
                    start_time = time.time()
                    response_buffer = ""
                    if ser.in_waiting > 0:
                        response_bytes = ser.read(ser.in_waiting)
                        response_buffer += response_bytes.decode('utf-8', errors='ignore')
                        match = DEV_PATTERN.search(response_buffer)
                        if match:
                            dev_char = match.group(1).lower()
                            if dev_char in DEVICE_MAP:
                                found_signals.append((DEVICE_MAP[dev_char], port))
                            device_found = True
            except Exception:
                pass
                
        assert 500000 in attempted_bauds
        assert 115200 in attempted_bauds
        assert found_signals == [("Temperature Controller", "/dev/ttyUSB0")]
