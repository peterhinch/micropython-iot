# c_comms_rx.py Client-side application demo reads data sent by another client

# Released under the MIT licence.
# Copyright (C) Peter Hinch 2018

import gc
import uasyncio as asyncio

gc.collect()
import ujson
from machine import Pin

from . import local_rx as local
from micropython_iot import client


class App:
    def __init__(self, loop, my_id, server, port, timeout, verbose):
        self.verbose = verbose
        self.led = Pin(2, Pin.OUT, value=1)  # LED for received data
        self.cl = client.Client(loop, my_id, server, port, timeout, None, None, verbose, None)
        loop.create_task(self.start(loop))

    async def start(self, loop):
        self.verbose and print('App awaiting connection.')
        await self.cl
        loop.create_task(self.reader())

    async def reader(self):
        self.verbose and print('Started reader')
        while True:
            line = await self.cl.readline()
            data = ujson.loads(line)
            self.led.value(data[0])
            print('Got', data, 'from server app')

    def close(self):
        self.cl.close()


loop = asyncio.get_event_loop()
app = App(loop, local.MY_ID, local.SERVER, local.PORT, local.TIMEOUT, True)
try:
    loop.run_forever()
finally:
    app.close()
