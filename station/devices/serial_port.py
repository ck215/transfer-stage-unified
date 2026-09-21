"""SKELETON - to be implemented. See REBUILD_BRIEF.md.

The member names below are the contract (design.json). Private helpers may be
added; public names may not change without the lead.
"""
from station.devices.device import Device

class ConnectionState:
    """SIMULATED, CONNECTING, VERIFIED, UNVERIFIED, LOST, CLOSED.
    """
    pass


class TransportError(Exception):
    """Lives in the same module as SerialPort, which is the only thing that
    raises it.
    """
    pass


class SimulatedPort:
    """Stand-in pyserial object for simulator mode.
    
    Absorbs: SimulatedPort
    """

    def __init__(self, *args, **kwargs):
        """was SimulatedPort.__init__
        names fixed by the pyserial interface it imitates
        """
        raise NotImplementedError

    def close(self, *args, **kwargs):
        """was SimulatedPort.close
        names fixed by the pyserial interface it imitates
        """
        raise NotImplementedError

    def flush(self, *args, **kwargs):
        """was SimulatedPort.flush
        names fixed by the pyserial interface it imitates
        """
        raise NotImplementedError

    def read(self, *args, **kwargs):
        """was SimulatedPort.read
        names fixed by the pyserial interface it imitates
        """
        raise NotImplementedError

    def readline(self, *args, **kwargs):
        """was SimulatedPort.readline
        names fixed by the pyserial interface it imitates
        """
        raise NotImplementedError

    def reset_input_buffer(self, *args, **kwargs):
        """was SimulatedPort.reset_input_buffer
        names fixed by the pyserial interface it imitates
        """
        raise NotImplementedError

    def reset_output_buffer(self, *args, **kwargs):
        """was SimulatedPort.reset_output_buffer
        names fixed by the pyserial interface it imitates
        """
        raise NotImplementedError

    def write(self, *args, **kwargs):
        """was SimulatedPort.write
        names fixed by the pyserial interface it imitates
        """
        raise NotImplementedError


class SerialPort(Device):
    """Was `serial` (which shadowed pyserial). ONE write path with a priority
    lane and an in-lock abort check. Knows nothing about probes.
    
    Absorbs: SMC100, serial
    
    MUST SATISFY:
    [CARRY] One write path. Transaction lock plus a separate write-in-flight
    lock, so a priority byte never interleaves with a frame already on the
    wire. Priority lane with a lock timeout. abort_if evaluated inside the
    lock. Bounded flush and write timeouts. A failed write marks the port
    LOST and raises TransportError; nothing is swallowed. Bytes on the wire
    are identical to today's (golden-frame test).  (SERIAL-8, SERIAL-11,
    SERIAL-12, SERIAL-16, SERIAL-20, SERIAL-23, TEMP-17, DC-18, ROTATOR-16)
    """

    def __init__(self, *args, **kwargs):
        """was serial.__init__
        """
        raise NotImplementedError

    def flush(self, *args, **kwargs):
        """was serial.flush
        """
        raise NotImplementedError

    def read_line(self, *args, **kwargs):
        """was serial.read_line, SMC100._readline, SMC100._emit
        raw port I/O moves onto the shared SerialPort: one lock discipline
        and one priority lane for the whole station
        """
        raise NotImplementedError

    def wait_open(self, *args, **kwargs):
        """was serial.wait_connected
        """
        raise NotImplementedError

    def write(self, *args, **kwargs):
        """was serial.write_command
        """
        raise NotImplementedError

    def _connect_loop(self, *args, **kwargs):
        """was serial._connect_worker
        """
        raise NotImplementedError

    def _handshake(self, *args, **kwargs):
        """was serial._handshake
        """
        raise NotImplementedError

    def _identity_from(self, *args, **kwargs):
        """was serial._device_from
        """
        raise NotImplementedError

    def _mark_lost(self, *args, **kwargs):
        """was serial._mark_lost
        """
        raise NotImplementedError

    def _verify(self, *args, **kwargs):
        """was serial._verify_serial
        """
        raise NotImplementedError
