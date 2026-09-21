"""SKELETON - to be implemented. See REBUILD_BRIEF.md.

The member names below are the contract (design.json). Private helpers may be
added; public names may not change without the lead.
"""
from station.model import Model

class Heater(Model):
    """Was TemperatureSystem. Owns a SerialPort.
    
    Absorbs: TemperatureSystem
    """

    @property
    def history(self):
        """was TemperatureSystem.get_history
        """
        raise NotImplementedError

    @property
    def series(self):
        """was TemperatureSystem.temp_series
        same name as RedMonitor.series: one name for 'the plot data'
        """
        raise NotImplementedError

    def __init__(self, *args, **kwargs):
        """was TemperatureSystem.__init__
        """
        raise NotImplementedError

    def apply_settings(self, *args, **kwargs):
        """was TemperatureSystem.send_settings
        raises Refused instead of returning None
        """
        raise NotImplementedError

    def _backoff_wait(self, *args, **kwargs):
        """was TemperatureSystem._backoff_wait
        """
        raise NotImplementedError

    def _parse_line(self, *args, **kwargs):
        """was TemperatureSystem.process_raw_data
        """
        raise NotImplementedError

    def _read_loop(self, *args, **kwargs):
        """was TemperatureSystem.read_serial_data
        """
        raise NotImplementedError
