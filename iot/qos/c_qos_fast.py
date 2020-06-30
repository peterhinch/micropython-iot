# c_qos_fast.py Client-side application demo for Quality of Service
# Tests rapid send and receive of qos messages

# Released under the MIT licence. See LICENSE.
# Copyright (C) Peter Hinch 2018-2020

# Now uses and requires uasyncio V3. This is incorporated in daily builds
# and release builds later than V1.12

import gc
import uasyncio as asyncio

gc.collect()
import ujson
from machine import Pin
from . import local
gc.collect()
from iot import client

# Optional LED. led=None if not required
from sys import platform
if platform == 'pyboard':  # D series
    from pyb import LED
    led = LED(1)
else:
    from machine import Pin
    led = Pin(2, Pin.OUT, value=1)  # Optional LED
# End of optionalLED

class App:
    def __init__(self, verbose):
        self.verbose = verbose
        self.cl = client.Client(local.MY_ID, local.SERVER,
                                local.PORT, local.SSID, local.PW,
                                local.TIMEOUT, verbose=verbose, led=led)
        self.tx_msg_id = 0
        self.dupes = 0  # Incoming dupe count
        self.missing = 0
        self.last = 0
        self.rxbuf = []

    async def start(self):
        self.verbose and print('App awaiting connection.')
        await self.cl
        asyncio.create_task(self.reader())
        await self.writer()

    async def reader(self):
        self.verbose and print('Started reader')
        while True:
            line = await self.cl.readline()
            data = ujson.loads(line)
            rxmid = data[0]
            if rxmid in self.rxbuf:
                self.dupes += 1
            else:
                self.rxbuf.append(rxmid)
            print('Got', data, 'from server app')

    def count_missed(self):
        if len(self.rxbuf) >= 25:
            idx = 0
            while self.rxbuf[idx] < self.last + 10:
                idx += 1
            self.last += 10
            self.missing += 10 - idx
            self.rxbuf = self.rxbuf[idx:]
        return self.missing

    # Send [ID, (re)connect count, free RAM, duplicate message count, missed msgcount]
    async def writer(self):
        self.verbose and print('Started writer')
        while True:
            for _ in range(4):
                gc.collect()
                data = [self.tx_msg_id, self.cl.connects, gc.mem_free(),
                        self.dupes, self.count_missed()]
                self.tx_msg_id += 1
                await self.cl  # Only launch write if link is up
                print('Sent', data, 'to server app\n')
                dstr = ujson.dumps(data)
                asyncio.create_task(self.cl.write(dstr, wait=False))
            await asyncio.sleep(5)

    def close(self):
        self.cl.close()


app = None
async def main():
    global app  # For finally clause
    app = App(verbose=True)
    await app.start()

try:
    asyncio.run(main())
finally:
    app.close()
    asyncio.new_event_loop()
