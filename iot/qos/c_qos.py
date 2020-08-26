# c_qos.py Client-side application demo for Quality of Service

# Released under the MIT licence. See LICENSE.
# Copyright (C) Peter Hinch 2018-2020

# Now uses and requires uasyncio V3. This is incorporated in daily builds
# and release builds later than V1.12

import gc
import uasyncio as asyncio

gc.collect()
import ujson
from machine import Pin
import time
from . import local
gc.collect()
from iot import client
import urandom
from .check_mid import CheckMid

# Optional LED. led=None if not required
from sys import platform, exit, print_exception
if platform == 'pyboard':  # D series
    from pyb import LED
    led = LED(1)
else:
    from machine import Pin
    led = Pin(2, Pin.OUT, value=1)  # Optional LED
# End of optionalLED

def _handle_exception(loop, context):
    print_exception(context["exception"])
    exit()

class App:
    def __init__(self, verbose):
        self.verbose = verbose
        self.cl = client.Client(local.MY_ID, local.SERVER,
                                local.PORT, local.SSID, local.PW,
                                local.TIMEOUT, verbose=verbose, led=led)
        self.tx_msg_id = 0
        self.cm = CheckMid()  # Check message ID's for dupes, missing etc.

    async def start(self):
        self.verbose and print('App awaiting connection.')
        await self.cl
        asyncio.create_task(self.writer())
        await self.reader()

    async def reader(self):
        self.verbose and print('Started reader')
        while True:
            line = await self.cl.readline()
            data = ujson.loads(line)
            self.cm(data[0])  # Update statistics
            print('Got', data, 'from server app')

    # Send [ID, (re)connect count, free RAM, duplicate message count, missed msgcount]
    async def writer(self):
        self.verbose and print('Started writer')
        st = time.time()
        while True:
            gc.collect()
            ut = time.time() - st  # Uptime in secs
            data = [self.tx_msg_id, self.cl.connects, gc.mem_free(),
                    self.cm.dupe, self.cm.miss, self.cm.oord, ut]
            self.tx_msg_id += 1
            print('Sent', data, 'to server app\n')
            dstr = ujson.dumps(data)
            await self.cl.write(dstr)  # Wait out any outage
            await asyncio.sleep_ms(7000 + urandom.getrandbits(10))

    def close(self):
        self.cl.close()


app = None
async def main():
    global app  # For finally clause
    loop = asyncio.get_event_loop()
    loop.set_exception_handler(_handle_exception)
    app = App(verbose=True)
    await app.start()

try:
    asyncio.run(main())
finally:
    app.close()
    asyncio.new_event_loop()
