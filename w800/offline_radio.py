"""Host-side offline radio service model.

This models software service availability, not handset identity or integrity.
It has no socket, serial port, SIM credentials, or RF transport. The firmware
ABI adapter is not implemented; creating this model does not unblock MAIN.
"""
from collections import deque
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RadioStatus:
    service_ready: bool = False
    radio_powered: bool = False
    sim_present: bool = False
    registered: bool = False
    signal_strength: int = 0
    operator: str | None = None
    mode: str = 'offline'


@dataclass(frozen=True)
class RadioResult:
    accepted: bool
    reason: str


class OfflineRadio:
    """Deterministic service lifecycle with permanently unavailable cellular IO."""
    def __init__(self):
        self._status = RadioStatus()
        self._events = deque(maxlen=32)

    @property
    def status(self):
        return self._status

    def _transition(self, ready):
        next_status = RadioStatus(service_ready=ready)
        if next_status != self._status:
            self._status = next_status
            self._events.append(asdict(next_status))

    def start(self):
        self._transition(True)
        return RadioResult(True, 'offline_service_ready')

    def stop(self):
        self._transition(False)
        return RadioResult(True, 'service_stopped')

    def reset(self):
        self._status = RadioStatus()
        self._events.clear()

    def cellular_request(self, operation):
        if operation not in ('enable_radio', 'register', 'call', 'sms', 'packet_data'):
            return RadioResult(False, 'unsupported_operation')
        if not self.status.service_ready:
            return RadioResult(False, 'service_not_started')
        return RadioResult(False, 'unavailable_in_offline_mode')

    def networks(self):
        return ()

    def drain_events(self):
        events = tuple(self._events)
        self._events.clear()
        return events

    def snapshot(self):
        return {'provider': 'offline', 'guest_adapter_connected': False,
                'status': asdict(self.status)}
