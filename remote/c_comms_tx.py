# c_comms_tx.py Client-side app demo. Sends switch state to "rx" device.

# Released under the MIT licence.
# Copyright (C) Peter Hinch 2018

# Outage handling: switch chnages during the outage are ignored. When the
# outage ends, the switch state at that time is transmitted.

import gc
import uasyncio as asyncio

gc.collect()
from micropython_iot import client, Event, launch

gc.collect()
import ujson
from machine import Pin
from . import local

gc.collect()


# Debounced switch. Stripped down version. For full version
# see https://github.com/peterhinch/micropython-async.git
class Switch:
    debounce_ms = 50

    def __init__(self, loop, pin):
        self.pin = pin  # Should be initialised for input with pullup
        self._open_func = False
        self._close_func = False
        self.switchstate = self.pin.value()  # Get initial state
        loop.create_task(self.switchcheck())  # Thread runs forever

    def open_func(self, func):
        self._open_func = func

    def close_func(self, func):
        self._close_func = func

    # Return current state of switch (0 = pressed)
    def __call__(self):
        return self.switchstate

    async def switchcheck(self):
        while True:
            state = self.pin.value()
            if state != self.switchstate:
                # State has changed: act on it now.
                self.switchstate = state
                if state == 0 and self._close_func:
                    launch(self._close_func)
                elif state == 1 and self._open_func:
                    launch(self._open_func)
            # Ignore further state changes until switch has settled
            await asyncio.sleep_ms(Switch.debounce_ms)


class App:
    def __init__(self, loop, verbose):
        self.verbose = verbose
        led = Pin(2, Pin.OUT, value=1)  # Optional LED
        # Pushbutton on Cockle board from shrimping.it
        self.switch = Switch(loop, Pin(0, Pin.IN))
        self.switch.close_func(lambda: self.must_send.set())
        self.switch.open_func(lambda: self.must_send.set())
        self.must_send = Event()
        self.cl = client.Client(loop, 'tx', local.SERVER, local.PORT,
                                local.SSID, local.PW, verbose=verbose, led=led)
        loop.create_task(self.start(loop))

    async def start(self, loop):
        self.verbose and print('App awaiting connection.')
        await self.cl
        self.verbose and print('Got connection')
        while True:
            await self.must_send
            await self.cl.write(ujson.dumps([self.switch()]), False)
            self.must_send.clear()

    def close(self):
        self.cl.close()


loop = asyncio.get_event_loop()
app = App(loop, True)
try:
    loop.run_forever()
finally:
    app.close()
