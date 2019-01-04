# esp_link.py Run on ESP8266. Provides a link between Pyboard/STM device and
# IOT server.

# Copyright (c) Peter Hinch 2018
# Released under the MIT licence. Full text in root of this repository.

import gc
import uasyncio as asyncio
import network

gc.collect()

from . import asi2c
gc.collect()
from machine import Pin, I2C

import sys
sys.path.append(sys.path.pop(0))  # ******* TEMPORARY *******

from micropython_iot import client
import ujson
gc.collect()

class LinkClient(client.Client):
    def __init__(self, loop, config, swriter, server_status, verbose):
        super().__init__(loop, config[0], config[2], config[1], config[3],
                         connected_cb=server_status, verbose=verbose, qos=config[6])
        self.config = config
        self.swriter = swriter

    # Initial connection to stored network failed. Try to connect using config
    async def bad_wifi(self):
        self.verbose and print('bad_wifi started')
        config = self.config
        sta_if = self._sta_if
        ssid = config[5]  # SSID
        # Either ESP does not 'know' this WLAN or it needs time to connect.
        if ssid == '':  # No SSID supplied: can only keep trying
            self.verbose and print('Connecting to ESP8266 stored network...')
            ssid = 'stored network'
        else:
            # Try to connect to specified WLAN. ESP will save details for
            # subsequent connections.
            self.verbose and print('Connecting to specified network...')
            sta_if.connect(ssid, config[6])
        self.verbose and print('Awaiting WiFi.')
        for _ in range(20):
            await asyncio.sleep(1)
            if sta_if.isconnected():
                return

        await self.swriter.awrite('b\n')
        # Message to Pyboard and REPL. Crash the board. Pyboard
        # detects, can reboot and retry, change config, or whatever
        raise ValueError("Can't connect to {}".format(ssid))  # croak...

    async def bad_server(self):
        await self.swriter.awrite('s\n')
        raise ValueError("Server {} port {} is down.".format(
            self.config[2], self.config[1]))  # As per bad_wifi: croak...


class App:
    def __init__(self, loop, verbose):
        self.verbose = verbose
        self.cl = None  # Client instance for server comms.
        # Instantiate a Pyboard Channel
        i2c = I2C(scl=Pin(0), sda=Pin(2))  # software I2C
        syn = Pin(5)
        ack = Pin(4)
        self.chan = asi2c.Responder(i2c, syn, ack)  # Channel to Pyboard
        self.sreader = asyncio.StreamReader(self.chan)
        self.swriter = asyncio.StreamWriter(self.chan, {})
        loop.create_task(self.start(loop))

    async def start(self, loop):
        await self.chan.ready()  # Wait for sync
        self.verbose and print('awaiting config')
        while True:
            line = await self.sreader.readline()
            # Checks are probably over-defensive. No fails now code is fixed.
            try:
                config = ujson.loads(line)
            except ValueError:
                self.verbose and print('JSON error. Got:', line)
            else:
                if isinstance(config, list) and len(config) == 9 and config[-1] == 'cfg':
                    break  # Got good config
                else:
                    self.verbose and print('Got bad config', line)

        self.verbose and print('Setting client config', config)
        self.cl = LinkClient(loop, config, self.swriter,
                             self.server_status, self.verbose)
        self.verbose and print('App awaiting connection.')
        await self.cl
        loop.create_task(self.to_server(loop))
        loop.create_task(self.from_server())
        if config[4]:
            loop.create_task(self.report(config[4]))

    async def to_server(self, loop):
        self.verbose and print('Started to_server task.')
        while True:
            line = await self.sreader.readline()
#            line = l[:]  # Implied copy at start of write()
            # If the following pauses for an outage, the Pyboard may write
            # one more line. Subsequent calls to channel.write pause pending
            # resumption of communication with the server.
            await self.cl.write(line)
            self.verbose and print('Sent', line, 'to server app')

    async def from_server(self):
        self.verbose and print('Started from_server task.')
        while True:
            line = await self.cl.readline()
            # Implied copy
            await self.swriter.awrite(''.join(('n', line.decode('utf8'))))
            self.verbose and print('Sent', line, 'to Pyboard app\n')

    async def server_status(self, status):
        await self.swriter.awrite('u\n' if status else 'd\n')

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
            line = ''.join(('r', ujson.dumps(data), '\n'))
            await self.swriter.awrite(line)

    def close(self):
        self.verbose and print('Closing interfaces')
        if self.cl is not None:
            self.cl.close()
        self.chan.close()


loop = asyncio.get_event_loop()
app = App(loop, True)
try:
    loop.run_forever()
finally:
    app.close()  # e.g. ctrl-c at REPL
