# c_comms_rx.py Client-side application demo reads data sent by another client

# Released under the MIT licence. See LICENSE.
# Copyright (C) Peter Hinch 2018-2020

import gc
import uasyncio as asyncio

gc.collect()
import ujson
from machine import Pin

from . import local
from micropython_iot import client


class App:
    def __init__(self, verbose):
        self.verbose = verbose
        self.led = Pin(2, Pin.OUT, value=1)  # LED for received data
        self.cl = client.Client('rx', local.SERVER, local.PORT,
                                local.SSID, local.PW, local.TIMEOUT,
                                verbose=verbose)

    async def start(self):
        self.verbose and print('App awaiting connection.')
        await self.cl
        asyncio.create_task(self.reader())

    async def reader(self):
        self.verbose and print('Started reader')
        while True:
            line = await self.cl.readline()
            data = ujson.loads(line)
            self.led.value(data[0])
            print('Got', data, 'from server app')

    def close(self):
        self.cl.close()


app = App(True)
try:
    asyncio.run(app.start())
finally:
    app.close()
    asyncio.new_event_loop()
