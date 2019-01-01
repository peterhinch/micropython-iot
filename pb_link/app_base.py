# pb_client.py Run on Pyboard/STM device. Communicate with IOT server via an
# ESP8266 running esp_link.py

# Copyright (c) Peter Hinch 2018
# Released under the MIT licence. Full text in root of this repository.

# Communication uses I2C slave mode.

import uasyncio as asyncio
import ujson
from . import asi2c_i
from micropython_iot import primitives  # Stripped down asyn.py


class AppBase:
    def __init__(self, loop, conn_id, config, hardware, verbose):
        self.loop = loop
        self.verbose = verbose
        self.initial = True
        self._status = False  # Server status
        self.wlock = primitives.Lock(100)
        self.rxevt = primitives.Event()  # rx data ready
        config.insert(0, conn_id)
        config.append('cfg')  # Marker defines a config list
        self.cfg = ''.join((ujson.dumps(config), '\n'))
        i2c, syn, ack, rst = hardware
        self.chan = asi2c_i.Initiator(i2c, syn, ack, rst, verbose, self._go, (), self._fail)
        self.sreader = asyncio.StreamReader(self.chan)
        self.swriter = asyncio.StreamWriter(self.chan, {})
        loop.create_task(self._read())

    # Runs after sync acquired on 1st or subsequent ESP8266 boots.
    async def _go(self):
        self.verbose and print('Sync acquired, sending config')
        # On 1st pass user coros haven't started so don't need lock. On
        # subsequent passes (crash recovery) lock was acquired by ._fail()
        await self.swriter.awrite(self.cfg)  # 1st message is config
        # At this point ESP8266 can handle the Pyboard interface but may not
        # yet be connected to the server
        if self.initial:
            self.initial = False
            self.start()
        else:  # Restarting after an ESP8266 crash
            self.wlock.release()

    # ESP8266 crash: prevent user code from writing until reboot sequence complete
    async def _fail(self):
        self.verbose and print('_fail locking')
        await self.wlock.acquire()
        self.verbose and print('_fail locked')

    async def _read(self):
        loop = self.loop
        while True:
            line = await self.sreader.readline()
            h, p = chr(line[0]), line[1:]  # Header char, payload
            if h == 'n':  # Normal message
                self.rxevt.set(p)
            elif h == 'b':
                loop.create_task(self.bad_wifi())
            elif h == 's':
                loop.create_task(self.bad_server())
            elif h == 'r':
                loop.create_task(self.report(ujson.loads(p)))
            elif h in ('u', 'd'):
                up = h == 'u'
                self._status = up
                loop.create_task(self.server_ok(up))
            else:
                raise ValueError('Unknown header:', h)

    # **** API ****
    async def write(self, line):
        if not line.endswith('\n'):
            line = ''.join((line, '\n'))
        async with self.wlock:  # Not during a resync.
            await self.swriter.awrite(line)

    async def readline(self):
        await self.rxevt
        line = self.rxevt.value()
        self.rxevt.clear()
        return line

    async def reboot(self):
        if self.chan.reset is None:  # No config for reset
            raise OSError("Can't reset ESP8266.")
        await self._fail()
        await self.chan.reboot()  # Hardware reset board

    def close(self):
        self.verbose and print('Closing channel.')
        self.chan.close()

    def status(self):  # Server status
        return self._status

    # **** For subclassing ****

    async def bad_wifi(self):
        await asyncio.sleep(0)
        raise OSError('No initial WiFi connection.')

    async def bad_server(self):
        await asyncio.sleep(0)
        raise OSError('No initial server connection.')

    async def report(self, data):
        await asyncio.sleep(0)
        print('Connects {} Count {} Mem free {}'.format(data[0], data[1], data[2]))

    async def server_ok(self, up):
        await asyncio.sleep(0)
        print('Server is {}'.format('up' if up else 'down'))
