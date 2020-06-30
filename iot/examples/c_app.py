# c_app.py Client-side application demo

# Released under the MIT licence. See LICENSE.
# Copyright (C) Peter Hinch 2018-2020

# Now uses and requires uasyncio V3. This is incorporated in daily builds
# and release builds later than V1.12

import gc
import uasyncio as asyncio
gc.collect()
from iot import client
gc.collect()
import ujson
# Optional LED. led=None if not required
from sys import platform
if platform == 'pyboard':  # D series
    from pyb import LED
    led = LED(1)
else:
    from machine import Pin
    led = Pin(2, Pin.OUT, value=1)  # Optional LED
# End of optional LED

from . import local
gc.collect()


class App(client.Client):
    def __init__(self, verbose):
        self.verbose = verbose
        self.cl = client.Client(local.MY_ID, local.SERVER, local.PORT, local.SSID, local.PW,
                                local.TIMEOUT, conn_cb=self.constate, verbose=verbose,
                                led=led, wdog=False)

    async def start(self):
        self.verbose and print('App awaiting connection.')
        await self.cl
        asyncio.create_task(self.reader())
        await self.writer()

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
            await self.cl.write(ujson.dumps(data))
            await asyncio.sleep(5)

    def shutdown(self):
        self.cl.close()  # Shuts down WDT (but not on Pyboard D).

app = None
async def main():
    global app  # For finally clause
    app = App(verbose=True)
    await app.start()

try:
    asyncio.run(main())
finally:
    app.shutdown()
    asyncio.new_event_loop()
