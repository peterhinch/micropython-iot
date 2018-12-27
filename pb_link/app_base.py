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
        self.wlock = primitives.Lock(100)
        config.insert(0, conn_id)
        config.append('cfg')  # Marker defines a config list
        self.cfg = ''.join((ujson.dumps(config), '\n'))
        i2c, syn, ack, rst = hardware
        self.chan = asi2c_i.Initiator(i2c, syn, ack, rst, verbose, self._go, (), self._fail)
        self.sreader = asyncio.StreamReader(self.chan)
        self.swriter = asyncio.StreamWriter(self.chan, {})

    # Runs after sync acquired on 1st or subsequent ESP8266 boots.
    async def _go(self):
        self.verbose and print('Sync aquired, sending config')
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

    async def write(self, line):
        if not line.endswith('\n'):
            line = ''.join((line, '\n'))
        async with self.wlock:  # Not during a resync.
            await self.swriter.awrite(line)

    async def readline(self):
        return await self.sreader.readline()

    async def reboot(self):
        if self.chan.reset is None:  # No config for reset
            raise OSError("Can't reset ESP8266.")
        self._fail()
        await self.chan.reboot()  # Hardware reset board

    def close(self):
        self.verbose and print('Closing channel.')
        self.chan.close()
