"""SKELETON - to be implemented. See REBUILD_BRIEF.md.

The member names below are the contract (design.json). Private helpers may be
added; public names may not change without the lead.
"""
from station.devices.device import Device

class SMC100(Device):
    """Newport driver. Protocol methods keep their vendor names so upstream
    diffs stay readable; its raw port I/O moves onto SerialPort so the
    station has one transport stack.
    
    Absorbs: SMC100
    """

    def __init__(self, *args, **kwargs):
        """was SMC100.__init__
        vendor-derived protocol names kept so upstream diffs stay readable
        """
        raise NotImplementedError

    def get_position_deg(self, *args, **kwargs):
        """was SMC100.get_position_deg
        vendor-derived protocol names kept so upstream diffs stay readable
        """
        raise NotImplementedError

    def get_position_mdeg(self, *args, **kwargs):
        """was SMC100.get_position_mdeg
        vendor-derived protocol names kept so upstream diffs stay readable
        """
        raise NotImplementedError

    def get_status(self, *args, **kwargs):
        """was SMC100.get_status
        vendor-derived protocol names kept so upstream diffs stay readable
        """
        raise NotImplementedError

    def home(self, *args, **kwargs):
        """was SMC100.home
        vendor-derived protocol names kept so upstream diffs stay readable
        """
        raise NotImplementedError

    def move_absolute_deg(self, *args, **kwargs):
        """was SMC100.move_absolute_deg
        vendor-derived protocol names kept so upstream diffs stay readable
        """
        raise NotImplementedError

    def move_absolute_mdeg(self, *args, **kwargs):
        """was SMC100.move_absolute_mdeg
        vendor-derived protocol names kept so upstream diffs stay readable
        """
        raise NotImplementedError

    def move_relative_deg(self, *args, **kwargs):
        """was SMC100.move_relative_deg
        vendor-derived protocol names kept so upstream diffs stay readable
        """
        raise NotImplementedError

    def move_relative_mdeg(self, *args, **kwargs):
        """was SMC100.move_relative_mdeg
        vendor-derived protocol names kept so upstream diffs stay readable
        """
        raise NotImplementedError

    def reset_and_configure(self, *args, **kwargs):
        """was SMC100.reset_and_configure
        vendor-derived protocol names kept so upstream diffs stay readable
        """
        raise NotImplementedError

    def sendcmd(self, *args, **kwargs):
        """was SMC100.sendcmd
        vendor-derived protocol names kept so upstream diffs stay readable
        """
        raise NotImplementedError

    def stop(self, *args, **kwargs):
        """was SMC100.stop
        vendor-derived protocol names kept so upstream diffs stay readable
        """
        raise NotImplementedError

    def wait_states(self, *args, **kwargs):
        """was SMC100.wait_states
        vendor-derived protocol names kept so upstream diffs stay readable
        """
        raise NotImplementedError
