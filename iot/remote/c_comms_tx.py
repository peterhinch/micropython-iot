# c_comms_tx.py Client-side app demo. Sends switch state to "rx" device.

# Released under the MIT licence. See LICENSE.
# Copyright (C) Peter Hinch 2018-2020

# Outage handling: switch chnages during the outage are ignored. When the
# outage ends, the switch state at that time is transmitted.

import gc
import uasyncio as asyncio

gc.collect()
from iot import client
from iot.primitives.switch import Switch

gc.collect()
import ujson
from machine import Pin
from . import local

gc.collect()


class App:
    def __init__(self, verbose):
        self.verbose = verbose
        led = Pin(2, Pin.OUT, value=1)  # Optional LED
        # Pushbutton on Cockle board from shrimping.it
        self.switch = Switch(Pin(0, Pin.IN))
        self.switch.close_func(lambda _ : self.must_send.set())
        self.switch.open_func(lambda _ : self.must_send.set())
        self.must_send = asyncio.Event()
        self.cl = client.Client('tx', local.SERVER, local.PORT,
                                local.SSID, local.PW, local.TIMEOUT,
                                verbose=verbose, led=led)

    async def start(self):
        self.verbose and print('App awaiting connection.')
        await self.cl
        self.verbose and print('Got connection')
        while True:
            await self.must_send.wait()
            await self.cl.write(ujson.dumps([self.switch()]), False)
            self.must_send.clear()

    def close(self):
        self.cl.close()


app = App(True)
try:
    asyncio.run(app.start())
finally:
    app.close()
    asyncio.new_event_loop()
