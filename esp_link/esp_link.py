# esp_link.py Run on ESP8266. Provides a link between Pyboard/STM device and
# IOT server.

# Copyright (c) Peter Hinch 2018
# Released under the MIT licence. Full text in root of this repository.

import gc
import uasyncio as asyncio
import network

gc.collect()
import ujson
from micropython_iot import client
from machine import Pin, I2C
import ujson
from . import asi2c

class LinkClient(client.Client):
    def __init__(self, loop, config, server_status, verbose):
        super().__init__(loop, config[0], config[2], config[1], config[3],
                         connected_cb=server_status, verbose=verbose)
        self.config = config

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

        err = "Can't connect to {}".format(ssid)
        data = ['error', err]
        line = ''.join((ujson.dumps(data), '\n'))
        await self.swriter.awrite(line)
        # Message to Pyboard and REPL. Crash the board. Pyboard
        # detects, can reboot and retry, change config, or whatever
        raise ValueError(err)  # croak...

class App:
    def __init__(self, loop, verbose):
        self.verbose = verbose
        self.cl = None  # Client instance for server comms.
        self.timeout = 0  # Set by config
        self.qos = 0
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

        self.timeout = config[3]
        self.qos = config[6]
        self.verbose and print('Setting client config', config)
        self.cl = LinkClient(loop, config, self.server_status, self.verbose)
        self.verbose and print('App awaiting connection.')
        await self.cl
        loop.create_task(self.to_server(loop))
        loop.create_task(self.from_server())
        if config[4]:
            loop.create_task(self.report(config[4]))

    # qos==1 Repeat tx if outage occurred after initial tx (1st may have been lost)
    #async def to_s_del(self, line):
        #await asyncio.sleep_ms(self.timeout)
        #if not self.cl.status():
            #await self.cl.write(line)
            #self.verbose and print('Repeat', line, 'to server app')

    async def to_server(self, loop):
        self.verbose and print('Started to_server task.')
        while True:
            l = await self.sreader.readline()
            line = l[:]  # Why did it take so long for this old fool to spot this bug?
            self.verbose and print('Got', line, 'to send to server app')
            # If the following pauses for an outage, the Pyboard may write
            # one more line. Subsequent calls to channel.write pause pending
            # resumption of communication with the server.
            await self.cl.write(line)
            # https://github.com/peterhinch/micropython-iot/blob/master/qos/README.md
            # We must await rather than schedule: hold off Pyboard with flow
            # control during outage
            if self.qos:
                await asyncio.sleep_ms(self.timeout)
                if not self.cl.status():
                    await self.cl.write(line)
                    self.verbose and print('Repeat', line, 'to server app')

            #if self.qos:  # qos 0 or 1 supported
                #loop.create_task(self.to_s_del(line[:]))
            self.verbose and print('Sent', line, 'to server app')

    async def from_server(self):
        self.verbose and print('Started from_server task.')
        while True:
            line = await self.cl.readline()
            await self.swriter.awrite(line.decode('utf8'))  # Implied copy
            self.verbose and print('Sent', line, 'to Pyboard app\n')

    async def server_status(self, status):
        data = ['status', status]
        line = ''.join((ujson.dumps(data), '\n'))
        await self.swriter.awrite(line)

    async def report(self, time):
        data = ['report', 0, 0, 0]
        count = 0
        while True:
            await asyncio.sleep(time)
            data[1] = self.cl.connects  # For diagnostics
            data[2] = count
            count += 1
            gc.collect()
            data[3] = gc.mem_free()
            line = ''.join((ujson.dumps(data), '\n'))
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
