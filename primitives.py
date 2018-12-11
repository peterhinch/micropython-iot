# primitives.py A stripped-down version of asyn.py with Lock and Event only.
# Save RAM on ESP8266

# Released under the MIT licence.
# Copyright (C) Peter Hinch 2018

import uasyncio as asyncio


class Lock:
    def __init__(self, delay_ms=0):
        self._locked = False
        self.delay_ms = delay_ms

    def locked(self):
        return self._locked

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, *args):
        self.release()
        await asyncio.sleep(0)

    async def acquire(self):
        while True:
            if self._locked:
                await asyncio.sleep_ms(self.delay_ms)
            else:
                self._locked = True
                break

    def release(self):
        if not self._locked:
            raise RuntimeError('Attempt to release a lock which has not been set')
        self._locked = False


class Event:
    def __init__(self, delay_ms=0):
        self.delay_ms = delay_ms
        self.clear()

    def clear(self):
        self._flag = False
        self._data = None

    def __iter__(self):
        while not self._flag:
            yield from asyncio.sleep_ms(self.delay_ms)

    __await__ = __iter__

    def is_set(self):
        return self._flag

    def set(self, data=None):
        self._flag = True
        self._data = data

    def value(self):
        return self._data
