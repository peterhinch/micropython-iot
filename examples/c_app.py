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


class App(client.Client):
    def __init__(self, loop, verbose):
        self.verbose = verbose
        led = Pin(2, Pin.OUT, value=1)  # Optional LED
        super().__init__(loop, local.MY_ID, local.SERVER, local.PORT,
                         local.TIMEOUT, self.constate, None, verbose, led)
        loop.create_task(self.start(loop))

    async def start(self, loop):
        self.verbose and print('App awaiting connection.')
        await self
        loop.create_task(self.reader())
        loop.create_task(self.writer())

    def constate(self, state):
        print("Connection state:", state)

    async def bad_wifi(self):
        import network
        sta_if = network.WLAN(network.STA_IF)
        ap = network.WLAN(network.AP_IF) # create access-point interface
        ap.active(False)         # deactivate the interface
        if not sta_if.isconnected():
            sta_if.active(True)
            sta_if.connect(local.SSID, local.PW)
            while not sta_if.isconnected():
                await asyncio.sleep_ms(200)

    async def reader(self):
        self.verbose and print('Started reader')
        while True:
            # Attempt to read data: in the event of an outage, .readline()
            # pauses until the connection is re-established.
            line = await self.readline()
            data = ujson.loads(line)
            # Receives [restart count, uptime in secs]
            print('Got', data, 'from server app')

    # Send [approx application uptime in secs, (re)connect count]
    async def writer(self):
        self.verbose and print('Started writer')
        data = [0, 0, 0]
        count = 0
        while True:
            data[0] = self.connects
            data[1] = count
            count += 1
            gc.collect()
            data[2] = gc.mem_free()
            print('Sent', data, 'to server app\n')
            # .write() behaves as per .readline()
            await self.write(ujson.dumps(data))
            await asyncio.sleep(5)

    def shutdown(self):
        self.close()


loop = asyncio.get_event_loop()
app = App(loop, verbose=True)
try:
    loop.run_forever()
finally:
    app.shutdown()
