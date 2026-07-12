"""Display/port allocator for concurrent sessions. Fixes the old
`90 + len(STREAMS)` scheme, which reused live display numbers after a stop."""

DISPLAY_BASE = 90
DEBUG_PORT_BASE = 9222
MAX_SESSIONS = 4


class Allocator:
    def __init__(self, max_sessions: int = MAX_SESSIONS):
        self.max_sessions = max_sessions
        self._held: set[int] = set()

    def acquire(self) -> tuple[int, int]:
        """Return (display_num, debug_port) not currently in use."""
        for i in range(self.max_sessions):
            n = DISPLAY_BASE + i
            if n not in self._held:
                self._held.add(n)
                return n, DEBUG_PORT_BASE + i
        raise RuntimeError('server busy: no free session slots')

    def release(self, display_num: int) -> None:
        self._held.discard(display_num)

    @property
    def active(self) -> int:
        return len(self._held)
