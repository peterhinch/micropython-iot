# c_qos.py Client-side application demo for Quality of Service

# Released under the MIT licence.
# Copyright (C) Peter Hinch 2018

import gc
import uasyncio as asyncio

gc.collect()
import ujson
from machine import Pin
from . import local
from micropython_iot import client


class App:
    def __init__(self, loop, my_id, server, port, timeout, verbose):
        self.verbose = verbose
        self.timeout = timeout
        led = Pin(2, Pin.OUT, value=1)  # Optional LED
        self.cl = client.Client(loop, my_id, server, port, timeout, None, None, verbose, led)
        self.tx_msg_id = 1
        self.rx_msg_id = None  # Incoming ID
        self.dupes_ignored = 0  # Incoming dupe count
        self.msg_missed = 0
        loop.create_task(self.start(loop))

    async def start(self, loop):
        self.verbose and print('App awaiting connection.')
        await self.cl
        loop.create_task(self.reader())
        loop.create_task(self.writer())

    async def reader(self):
        self.verbose and print('Started reader')
        while True:
            line = await self.cl.readline()
            data = ujson.loads(line)
            if self.rx_msg_id is None:
                self.rx_msg_id = data[0]  # Just started
            elif self.rx_msg_id == data[0]:  # We've had a duplicate
                self.dupes_ignored += 1
                continue
            else:  # Message ID is new
                if self.rx_msg_id != data[0] - 1:
                    self.msg_missed += 1
                self.rx_msg_id = data[0]
            print('Got', data, 'from server app')

    # Send [ID, (re)connect count, free RAM, duplicate message count, missed msgcount]
    async def writer(self):
        self.verbose and print('Started writer')
        while True:
            gc.collect()
            data = [self.tx_msg_id, self.cl.connects, gc.mem_free(),
                    self.dupes_ignored, self.msg_missed]
            self.tx_msg_id += 1
            print('Sent', data, 'to server app\n')
            dstr = ujson.dumps(data)
            await self.cl.write(dstr)
            await asyncio.sleep_ms(self.timeout)  # time for outage detection
            if not self.cl.status():  # Meassage may have been lost
                await self.cl.write(dstr)  # Re-send: will wait until outage clears
            await asyncio.sleep(5)

    def close(self):
        self.cl.close()


loop = asyncio.get_event_loop()
app = App(loop, local.MY_ID, local.SERVER, local.PORT, local.TIMEOUT, True)
try:
    loop.run_forever()
finally:
    app.close()
