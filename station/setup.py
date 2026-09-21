"""SKELETON - to be implemented. See REBUILD_BRIEF.md.

The member names below are the contract (design.json). Private helpers may be
added; public names may not change without the lead.
"""
from station.panel import Panel

class Setup(Panel):
    """The setup panel. Scans ports and gamepads, validates an assignment,
    constructs Models into the Controller. Rendered by every view's
    PanelView from its schema, so there is one wizard instead of three.
    
    Absorbs: <app_bootstrap>, <model.devices>, WebModelAdapter
    
    MUST SATISFY:
    [CARRY] ONE setup for three views, with autodetection preserved: port
    scan, handshake identity byte, gamepad list. Scan runs off the UI thread
    with progress and can be cancelled. Build is all-or-nothing with
    rollback. The identity byte is checked against the model class. SIM
    works for every model. A disabled row builds nothing. Probing errors are
    reported, not swallowed. CLI args are strict.  (MANAGER-5, MANAGER-6,
    MANAGER-12, MANAGER-14, MANAGER-18, MANAGER-20, SERIAL-6, SERIAL-7,
    SERIAL-9, SERIAL-17, WEB-4, WEB-15, WEB-16, DC-12, DC-14, REDPERCENT-15,
    VIEW-TKINTER-7)
    """

    @property
    def model_types(self):
        """was <model.devices>.names
        """
        raise NotImplementedError

    def build(self, *args, **kwargs):
        """was <app_bootstrap>.build_models, <app_bootstrap>._build_each,
        <model.devices>.get, <model.devices>.build,
        WebModelAdapter.initialize_setup,
        WebModelAdapter._initialize_setup_locked,
        WebModelAdapter.initialize_system
        """
        raise NotImplementedError

    def identify(self, *args, **kwargs):
        """was <app_bootstrap>.probe_device_at
        """
        raise NotImplementedError

    def scan(self, *args, **kwargs):
        """was WebModelAdapter.scan_hardware,
        WebModelAdapter.start_hardware_scan, WebModelAdapter.get_scan_status
        progress is part of Setup.state
        """
        raise NotImplementedError

    def scan_gamepads(self, *args, **kwargs):
        """was <app_bootstrap>.discover_controllers
        'controller' now means only the Controller
        """
        raise NotImplementedError

    def scan_ports(self, *args, **kwargs):
        """was <app_bootstrap>.discover_ports
        """
        raise NotImplementedError

    def validate(self, *args, **kwargs):
        """was <app_bootstrap>.validate_assignment
        """
        raise NotImplementedError
