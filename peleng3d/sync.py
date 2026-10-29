"""Frame synchronization using NTP-style clock offset, not raw device uptime."""
from collections import deque
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")

@dataclass(frozen=True)
class TimedFrame(Generic[T]):
    seq: int
    timestamp: float   # monotonic time in Mac clock domain
    value: T


class FrameMatcher(Generic[T]):
    def __init__(self, tolerance_s: float = 0.09, capacity: int = 90):
        self.tolerance_s = tolerance_s
        self.frames: deque[TimedFrame[T]] = deque(maxlen=capacity)
        self.last_used = -1

    def add(self, frame: TimedFrame[T]) -> None:
        self.frames.append(frame)

    def match(self, remote_mac_timestamp: float, uncertainty_s: float) -> tuple[TimedFrame[T], float] | None:
        if uncertainty_s < 0 or uncertainty_s > self.tolerance_s/2:
            return None
        candidates = [f for f in self.frames if f.seq > self.last_used]
        if not candidates:
            return None
        best = min(candidates, key=lambda f: abs(f.timestamp - remote_mac_timestamp))
        dt = abs(best.timestamp - remote_mac_timestamp)
        if dt + uncertainty_s > self.tolerance_s:
            return None
        self.last_used = best.seq
        return best, dt


def estimate_offset(client_send: float, server_receive: float, server_send: float, client_receive: float) -> tuple[float, float]:
    """Mac-clock - iPhone-clock offset and half RTT uncertainty."""
    if not (client_send <= client_receive and server_receive <= server_send):
        raise ValueError("Invalid clock sample ordering")
    rtt = client_receive - client_send - (server_send - server_receive)
    if rtt < -0.001:
        raise ValueError("Negative network RTT")
    return ((server_receive - client_send) + (server_send - client_receive))/2, max(0.0,rtt/2)
