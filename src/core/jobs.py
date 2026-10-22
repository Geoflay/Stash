"""Running several downloads at once: slots that cap how many download and how many
convert at the same time."""

import asyncio
import collections
import threading
import time

from core.errors import Cancelled


PARALLEL_CHOICES = (1, 2, 3, 4, 5, 6, 8)
DEFAULT_PARALLEL = 2
DEFAULT_PARALLEL_CONVERSIONS = 2


def ignore_progress(fraction, message, detail=None):
    """Default progress callback: every callback takes (fraction, message, detail=None)."""


class Slots:
    """Caps how many jobs are in one stage (downloading or converting) at once, across every download.

    Waited on from the event loop (acquire) or from a worker thread (acquire_blocking); waiters are
    served in order of their rank (by default the moment they arrived; the queue's downloads use their
    place in the list), and the limit can change while they wait.
    """

    POLL = 0.25  # seconds between two checks of the cancel event while waiting

    def __init__(self, limit):
        self.limit = limit
        self.active = 0
        self._lock = threading.Lock()
        self._waiters = collections.deque()  # (Event set when given a slot, rank) per waiter

    def _grant(self):  # with the lock held
        while self._waiters and self.active < self.limit:
            first = min(self._waiters, key=lambda waiter: waiter[1]())
            self._waiters.remove(first)
            self.active += 1
            first[0].set()

    def _enqueue(self, rank=None):
        granted = threading.Event()
        if rank is None:
            arrived = time.monotonic()
            rank = lambda: arrived
        with self._lock:
            self._waiters.append((granted, rank))
            self._grant()
        return granted

    def _give_up(self, granted):
        """Leave the queue; a slot granted in the meantime is handed back."""
        with self._lock:
            for waiter in self._waiters:
                if waiter[0] is granted:
                    self._waiters.remove(waiter)
                    return
        self.release()

    def is_full(self):
        with self._lock:
            return self.active >= self.limit

    async def acquire(self, cancelled, rank=None):
        """Wait for a slot; returns False (holding none) if `cancelled` is set meanwhile. `rank`: returns
        the waiter's place (smaller first), read again each time a slot frees."""
        granted = self._enqueue(rank)
        while not granted.is_set():
            if cancelled.is_set():
                self._give_up(granted)
                return False
            await asyncio.sleep(self.POLL)
        return True

    def acquire_blocking(self, cancelled):
        """Same as acquire, from a worker thread; raises Cancelled instead of returning False."""
        granted = self._enqueue()
        while not granted.wait(self.POLL):
            if cancelled.is_set():
                self._give_up(granted)
                raise Cancelled()

    def release(self):
        with self._lock:
            self.active -= 1
            self._grant()

    def set_limit(self, limit):
        with self._lock:
            self.limit = limit
            self._grant()


_worker = threading.local()  # the JobSlots of the job running in this worker thread


class JobSlots:
    """The slot one job holds: a download slot first, then a conversion slot once it starts converting,
    so a long conversion never keeps the next video from downloading."""

    def __init__(self, downloads, conversions, cancelled, rank=None):
        self.downloads, self.conversions, self.cancelled = downloads, conversions, cancelled
        self.rank = rank
        self.stage = "waiting"  # then "download", "convert", "done"
        self.notify = ignore_progress

    async def start(self):
        if not await self.downloads.acquire(self.cancelled, self.rank):
            self.stage = "done"
            return False
        self.stage = "download"
        return True

    def run(self, notify, func, *args, **kwargs):
        """Run func in this worker thread on behalf of the job (see entering_conversion)."""
        _worker.job, self.notify = self, notify
        try:
            return func(*args, **kwargs)
        finally:
            _worker.job = None

    def to_conversion(self):
        if self.stage != "download":
            return
        if self.conversions.is_full():
            self.notify(None, "Waiting for a free conversion slot…")
        # Take the conversion slot before giving back the download one: only jobs holding a download
        # slot ever wait here, which bounds the number of blocked worker threads.
        self.conversions.acquire_blocking(self.cancelled)
        self.downloads.release()
        self.stage = "convert"

    def release(self):
        if self.stage == "download":
            self.downloads.release()
        elif self.stage == "convert":
            self.conversions.release()
        self.stage = "done"


class SitePause:
    """Downloads from a site that blocks the app (YouTube's "not a bot" check) wait here until the user
    presses Resume, instead of failing one after the other. Used from the event loop only."""

    POLL = 0.25  # seconds between two checks of the cancel event while waiting

    def __init__(self):
        self.reason = ""  # the site's error message while paused, "" otherwise
        self.listeners = []  # called when the pause starts or ends (to refresh the screens)

    @property
    def paused(self):
        return bool(self.reason)

    def pause(self, reason):
        """Returns True if this starts the pause (False if it was already paused)."""
        started = not self.paused
        self.reason = reason or "Blocked"
        for listener in self.listeners:
            listener()
        return started

    def resume(self):
        self.reason = ""
        for listener in self.listeners:
            listener()

    async def wait(self, cancelled):
        """Wait until resumed; returns False if `cancelled` is set meanwhile."""
        while self.paused:
            if cancelled.is_set():
                return False
            await asyncio.sleep(self.POLL)
        return True


def entering_conversion():
    """Called by the download code when the downloading part is over and converting begins."""
    job = getattr(_worker, "job", None)
    if job is not None:
        job.to_conversion()
