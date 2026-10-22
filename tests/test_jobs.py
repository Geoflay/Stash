"""Parallel limits: how many videos download, and how many convert, at the same time."""

import asyncio
import threading
import time

import pytest

from core.errors import Cancelled
from core.jobs import JobSlots, Slots, entering_conversion


class Counter:
    """Counts how many jobs are inside at once."""

    def __init__(self):
        self.now = self.peak = 0
        self.lock = threading.Lock()

    def __enter__(self):
        with self.lock:
            self.now += 1
            self.peak = max(self.peak, self.now)

    def __exit__(self, *exc):
        with self.lock:
            self.now -= 1


async def test_limit_and_arrival_order():
    slots, inside, order = Slots(2), Counter(), []

    async def job(name):
        assert await slots.acquire(threading.Event())
        order.append(name)
        with inside:
            await asyncio.sleep(0.05)
        slots.release()

    await asyncio.gather(*(job(n) for n in "ABCDE"))
    assert inside.peak == 2
    assert order == list("ABCDE")
    assert slots.active == 0


async def test_cancelled_while_waiting():
    slots = Slots(1)
    assert await slots.acquire(threading.Event())
    cancel = threading.Event()
    waiting = asyncio.create_task(slots.acquire(cancel))
    await asyncio.sleep(0.05)
    cancel.set()
    assert await waiting is False
    slots.release()
    assert slots.active == 0
    assert await slots.acquire(threading.Event())  # the slot was not lost


async def test_raising_the_limit_starts_waiting_jobs():
    slots, inside = Slots(1), Counter()

    async def job():
        await slots.acquire(threading.Event())
        with inside:
            await asyncio.sleep(0.6)
        slots.release()

    tasks = [asyncio.create_task(job()) for _ in range(3)]
    await asyncio.sleep(0.1)
    slots.set_limit(3)
    start = time.monotonic()
    await asyncio.gather(*tasks)
    assert inside.peak == 3
    assert time.monotonic() - start < 1.0  # one after the other would take 1.2 s


def test_waiting_from_worker_threads():
    slots, inside = Slots(2), Counter()

    def worker():
        slots.acquire_blocking(threading.Event())
        with inside:
            time.sleep(0.05)
        slots.release()

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert inside.peak == 2


def test_worker_thread_cancelled_while_waiting():
    slots = Slots(1)
    slots.acquire_blocking(threading.Event())
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        slots.acquire_blocking(cancel)
    slots.release()
    assert slots.active == 0


async def test_job_moves_from_download_slot_to_conversion_slot():
    downloads, conversions = Slots(1), Slots(1)
    job = JobSlots(downloads, conversions, threading.Event())
    assert await job.start()
    assert job.stage == "download" and downloads.active == 1

    def work():
        entering_conversion()  # what the download code calls once the file is downloaded
        return job.stage, downloads.active, conversions.active

    assert await asyncio.to_thread(job.run, lambda *a: None, work) == ("convert", 0, 1)
    job.release()
    assert job.stage == "done" and downloads.active == 0 and conversions.active == 0


async def test_entering_conversion_outside_a_job_does_nothing():
    await asyncio.to_thread(entering_conversion)


async def test_conversions_overlap_the_next_downloads():
    """1 download slot, 2 conversion slots: while A converts, B downloads (a pipeline)."""
    downloads, conversions = Slots(1), Slots(2)
    downloading, converting = Counter(), Counter()
    messages = []

    def fake_download_and_convert():
        with downloading:
            time.sleep(0.2)
        entering_conversion()
        with converting:
            time.sleep(0.6)

    async def job():
        slots = JobSlots(downloads, conversions, threading.Event())
        await slots.start()
        try:
            await asyncio.to_thread(slots.run, lambda f, m, d=None: messages.append(m), fake_download_and_convert)
        finally:
            slots.release()

    start = time.monotonic()
    await asyncio.gather(*(job() for _ in range(3)))
    assert downloading.peak == 1
    assert converting.peak == 2
    assert time.monotonic() - start < 2.4  # one after the other: 3 x 0.8 s
    assert "Waiting for a free conversion slot…" in messages  # the third job waited
    assert downloads.active == 0 and conversions.active == 0


async def test_site_pause():
    from core.jobs import SitePause
    pause, calls = SitePause(), []
    pause.listeners.append(lambda: calls.append(pause.paused))
    assert await pause.wait(threading.Event())  # not paused: no wait
    assert pause.pause("not a bot") is True
    assert pause.pause("again") is False  # already paused
    waiting = asyncio.create_task(pause.wait(threading.Event()))
    await asyncio.sleep(0.3)
    assert not waiting.done()
    pause.resume()
    assert await asyncio.wait_for(waiting, 2) is True
    assert calls == [True, True, False]


async def test_site_pause_wait_cancelled():
    from core.jobs import SitePause
    pause, cancel = SitePause(), threading.Event()
    pause.pause("blocked")
    waiting = asyncio.create_task(pause.wait(cancel))
    await asyncio.sleep(0.1)
    cancel.set()
    assert await asyncio.wait_for(waiting, 2) is False
