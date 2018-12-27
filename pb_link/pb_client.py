# pb_client.py Run on Pyboard/STM device. Communicate with IOT server via an
# ESP8266 running esp_link.py

# Copyright (c) Peter Hinch 2018
# Released under the MIT licence. Full text in root of this repository.

# Communication uses I2C slave mode.

import uasyncio as asyncio
import ujson
from . import app_base
from . import config as cfg

# Server-side connection ID: any newline-terminated string not containing an
# internal newline.
CONN_ID = '1\n'


# User application: must be class subclassed from AppBase
class App(app_base.AppBase):
    def __init__(self, loop, conn_id, config, hardware, verbose):
        super().__init__(loop, conn_id, config, hardware, verbose)

    def start(self):  # Apps must implement a synchronous start method
        self.loop.create_task(self.receiver())
        self.loop.create_task(self.sender())

    # If server is running s_app_cp.py it sends
    # [approx app uptime in secs/5, echoed count, echoed 99]
    async def receiver(self):
        self.verbose and print('Starting receiver.')
        while True:
            line = await self.readline()
            data = ujson.loads(line)
            self.verbose and print('Received', data)

    async def sender(self):
        self.verbose and print('Starting sender.')
        data = [42, 0, 99]  # s_app_cp.py expects a 3-list
        while True:
            await asyncio.sleep(5)
            data[1] += 1
            await self.write(ujson.dumps(data))
            self.verbose and print('Sent', data)


loop = asyncio.get_event_loop()
app = App(loop, CONN_ID, cfg.config, cfg.hardware, True)
try:
    loop.run_forever()
finally:
    app.close()  # for subsequent runs
