"""Outstanding work, including completion callbacks and the cleanup they start.

An activity owns a ticket until its owner finishes it. Cancellation alone does
not finish a ticket. Notifications carry no cached count: an observer can start
more work, and the next observer must read the current count. The UI supplies
its own dispatcher; this module neither starts threads nor knows an application.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable


class Activities:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tickets: set[object] = set()
        self._observers: list[Callable[[], None]] = []

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._tickets)

    def acquire(self) -> object:
        ticket = object()
        with self._lock:
            self._tickets.add(ticket)
        self._changed()
        return ticket

    def finish(self, ticket: object) -> bool:
        """Release once; a repeated or foreign ticket changes nothing."""
        with self._lock:
            if ticket not in self._tickets:
                return False
            self._tickets.remove(ticket)
        self._changed()
        return True

    def subscribe(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Observe changes on the caller's thread; return an idempotent unsubscribe."""
        with self._lock:
            self._observers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._observers:
                    self._observers.remove(callback)

        return unsubscribe

    def _changed(self) -> None:
        with self._lock:
            observers = tuple(self._observers)
        for callback in observers:
            try:
                callback()
            except Exception:
                # A failed view must not strand work or hide completion from
                # another observer (in particular the shutdown coordinator).
                logging.getLogger(__name__).exception("notifying an activity observer")
