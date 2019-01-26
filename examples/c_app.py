# c_app.py Client-side application demo

# Released under the MIT licence.
# Copyright (C) Peter Hinch 2018

import gc
import uasyncio as asyncio

gc.collect()
from micropython_iot import client

gc.collect()
import ujson
from machine import Pin

from . import local

gc.collect()


class App:
    def __init__(self, loop, my_id, server, port, timeout, verbose):
        self.verbose = verbose
        led = None  # Pin(2, Pin.OUT, value=1)  # Optional LED
        self.cl = client.Client(loop, my_id, server, port, timeout, self.constate, None, verbose, led)
        loop.create_task(self.start(loop))
        self.latency_added = 0
        self.count = 0

    async def start(self, loop):
        self.verbose and print('App awaiting connection.')
        await self.cl
        loop.create_task(self.reader())
        loop.create_task(self.writer())

    def constate(self, state):
        print("Connection state:", state)

    async def reader(self):
        self.verbose and print('Started reader')
        while True:
            # Attempt to read data: in the event of an outage, .readline()
            # pauses until the connection is re-established.
            line = await self.cl.readline()
            data = ujson.loads(line)
            # Receives [restart count, uptime in secs]
            print('Got', data, 'from server app')

    # Send [approx application uptime in secs, (re)connect count]
    async def writer(self):
        import utime
        self.verbose and print('Started writer')
        data = [0, 0, 0]
        count = 0
        while True:
            data[0] = self.cl.connects
            data[1] = count
            count += 1
            gc.collect()
            data[2] = gc.mem_free()
            print('Sent', data, 'to server app\n')
            # .write() behaves as per .readline()
            st = utime.ticks_ms()
            await self.cl.writeline(ujson.dumps(data))
            latency = utime.ticks_ms() - st
            self.latency_added += latency
            self.count += 1
            print("Latency:", latency, "Avg Latency:", self.latency_added / self.count)
            await asyncio.sleep(2)

    def close(self):
        self.cl.close()


loop = asyncio.get_event_loop()
app = App(loop, local.MY_ID, local.SERVER, local.PORT, local.TIMEOUT, True)
try:
    loop.run_forever()
finally:
    app.close()
