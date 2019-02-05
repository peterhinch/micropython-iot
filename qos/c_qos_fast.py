# c_qos_fast.py Client-side application demo for Quality of Service
# Tests rapid send and receive of qos messages

# Released under the MIT licence.
# Copyright (C) Peter Hinch 2018

import gc
import uasyncio as asyncio

gc.collect()
import ujson
from machine import Pin
from . import local
gc.collect()
from micropython_iot import client

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
    def __init__(self, loop, verbose):
        self.verbose = verbose
        self.cl = client.Client(loop, local.MY_ID, local.SERVER,
                                local.SSID, local.PW,
                                verbose=verbose, led=led)
        self.tx_msg_id = 0
        self.dupes = 0  # Incoming dupe count
        self.missing = 0
        self.last = 0
        self.rxbuf = []
        loop.create_task(self.start(loop))

    async def start(self, loop):
        self.verbose and print('App awaiting connection.')
        await self.cl
        loop.create_task(self.reader())
        loop.create_task(self.writer(loop))

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
    async def writer(self, loop):
        self.verbose and print('Started writer')
        while True:
            for _ in range(4):
                gc.collect()
                data = [self.tx_msg_id, self.cl.connects, gc.mem_free(),
                        self.dupes, self.count_missed()]
                self.tx_msg_id += 1
                print('Sent', data, 'to server app\n')
                dstr = ujson.dumps(data)
                loop.create_task(self.cl.write(dstr, wait=False))
                await asyncio.sleep_ms(300)
            await asyncio.sleep(5)

    def close(self):
        self.cl.close()


loop = asyncio.get_event_loop(runq_len=40, waitq_len=40)
app = App(loop, True)
try:
    loop.run_forever()
finally:
    app.close()
