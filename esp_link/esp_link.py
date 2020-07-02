# esp_link.py Run on ESP8266. Provides a link between Pyboard/STM device and
# IOT server.

# Copyright (c) Peter Hinch 2018-2020
# Released under the MIT licence. Full text in root of this repository.

import gc
import uasyncio as asyncio
import time
gc.collect()
import ujson
from micropython import const
from machine import Pin, I2C
gc.collect()

from . import asi2c
from iot import client
gc.collect()

_ID = const(0)  # Config list index
_PORT = const(1)
_SERVER = const(2)
_TIMEOUT = const(3)
_REPORT = const(4)
_SSID = const(5)
_PW = const(6)

class LinkClient(client.Client):
    def __init__(self, config, swriter, verbose):
        super().__init__(config[_ID], config[_SERVER], config[_PORT],
                         config[_SSID], config[_PW], config[_TIMEOUT],
                         conn_cb=self.conn_cb, verbose=verbose)
        self.config = config
        self.swriter = swriter

    # Initial connection to stored network failed. Try to connect using config
    async def bad_wifi(self):
        try:
            await asyncio.wait_for(super().bad_wifi(), 20)
        except asyncio.TimeoutError:
            self.swriter.write('b\n')
            await self.swriter.drain()
            # Message to Pyboard and REPL. Crash the board. Pyboard
            # detects, can reboot and retry, change config, or whatever
            raise ValueError("Can't connect to {}".format(self.config[_SSID]))  # croak...

    async def bad_server(self):
        self.swriter.write('s\n')
        await self.swriter.drain()
        raise ValueError("Server {} port {} is down.".format(
            self.config[_SERVER], self.config[_PORT]))  # As per bad_wifi: croak...

    # Callback when connection status changes
    async def conn_cb(self, status):
        self.swriter.write('u\n' if status else 'd\n')
        await self.swriter.drain()


class App:
    def __init__(self, verbose):
        self.verbose = verbose
        self.cl = None  # Client instance for server comms.
        # Instantiate a Pyboard Channel
        i2c = I2C(scl=Pin(0), sda=Pin(2))  # software I2C
        syn = Pin(5)
        ack = Pin(4)
        self.chan = asi2c.Responder(i2c, syn, ack)  # Channel to Pyboard
        self.sreader = asyncio.StreamReader(self.chan)
        self.swriter = asyncio.StreamWriter(self.chan, {})

    async def start(self):
        await self.chan.ready()  # Wait for sync
        self.verbose and print('awaiting config')
        line = await self.sreader.readline()
        config = ujson.loads(line)

        self.verbose and print('Setting client config', config)
        self.cl = LinkClient(config, self.swriter, self.verbose)
        self.verbose and print('App awaiting connection.')
        await self.cl
        asyncio.create_task(self.to_server())
        asyncio.create_task(self.from_server())
        t_rep = config[_REPORT]  # Reporting interval (s)
        if t_rep:
            asyncio.create_task(self.report(t_rep))
        await self.crashdet()

    async def to_server(self):
        self.verbose and print('Started to_server task.')
        while True:
            line = await self.sreader.readline()
            line = line.decode()
            n = ord(line[0]) - 0x30  # Decode header to bitfield
            # Implied copy at start of write()
            # If the following pauses for an outage, the Pyboard may write
            # one more line. Subsequent calls to channel.write pause pending
            # resumption of communication with the server.
            await self.cl.write(line[1:], qos=n & 2, wait=n & 1)
            self.verbose and print('Sent', line[1:].rstrip(), 'to server app')

    async def from_server(self):
        self.verbose and print('Started from_server task.')
        while True:
            line = await self.cl.readline()
            # Implied copy
            self.swriter.write('n{}'.format(line))
            await self.swriter.drain()
            self.verbose and print('Sent', line.encode('utf8'), 'to Pyboard app\n')

    async def crashdet(self):
        while True:
            await asyncio.sleep(2)
            self.swriter.write('k\n')
            await self.swriter.drain()
            gc.collect()

    async def report(self, time):
        data = [0, 0, 0]
        count = 0
        while True:
            await asyncio.sleep(time)
            data[0] = self.cl.connects  # For diagnostics
            data[1] = count
            count += 1
            gc.collect()
            data[2] = gc.mem_free()
            line = 'r{}\n'.format(ujson.dumps(data))
            self.swriter.write(line)
            await self.swriter.drain()

    def close(self):
        self.verbose and print('Closing interfaces')
        if self.cl is not None:
            self.cl.close()
        self.chan.close()

async def main():
    app = App(True)
    await app.start()

try:
    asyncio.run(main())
finally:
    app.close()  # e.g. ctrl-c at REPL
    asyncio.new_event_loop()
