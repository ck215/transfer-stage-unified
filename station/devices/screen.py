"""SKELETON - to be implemented. See REBUILD_BRIEF.md.

The member names below are the contract (design.json). Private helpers may be
added; public names may not change without the lead.
"""
from station.devices.device import Device

class Screen(Device):
    """Screen capture (mss). Was duplicated in RedPercentSystem and the web
    server.
    
    Absorbs: RedPercentSystem
    """

    def grab(self, *args, **kwargs):
        """was RedPercentSystem.capture_focus_area
        """
        raise NotImplementedError
