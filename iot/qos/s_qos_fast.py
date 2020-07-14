#! /usr/bin/env python3
# -*- coding: utf-8 -*-

# s_app_cp.py Server-side application demo
# Tests rapid send and receive of qos messages
# Run under CPython 3.8 or later or MicroPython Unix build.
# Under MicroPython uses and requires uasyncio V3.

# Released under the MIT licence. See LICENSE.
# Copyright (C) Peter Hinch 2018-2020

# The App class emulates a user application intended to service a single
# client.

try:
    import asyncio
except ImportError:
    import uasyncio as asyncio
try:
    import json
except ImportError:
    import ujson as json
import time
from iot import server
from .local import PORT, TIMEOUT
from .check_mid import CheckMid

import sys
def _handle_exception(loop, context):
    print('Global handler')
    sys.print_exception(context["exception"])
    sys.exit()


class App:
    def __init__(self, client_id):
        self.client_id = client_id  # This instance talks to this client
        self.conn = None  # Connection instance
        self.tx_msg_id = 0
        self.cm = CheckMid()  # Check message ID's for dupes, missing etc.
        self.data = [0, 0, 0, 0, 0]  # Data from remote
        asyncio.create_task(self.start())

    async def start(self):
        print('Client {} Awaiting connection.'.format(self.client_id))
        self.conn = await server.client_conn(self.client_id)
        asyncio.create_task(self.reader())
        asyncio.create_task(self.writer())
        st = time.time()
        cm = self.cm
        data = self.data
        while True:
            await asyncio.sleep(30)
            outages = self.conn.nconns - 1
            ut = (time.time() - st) / 3600  # Uptime in hrs
            print('Uptime {:6.2f}hr outages {}'.format(ut, outages))
            print('Dupes ignored {} local {} remote. '.format(cm.tot_dupe, data[3]), end='')
            print('Missed msg {} local {} remote.'.format(cm.tot_miss, data[4]), end='')
            print('Client reboots', cm.bcnt)

    async def reader(self):
        print('Started reader')
        while True:
            line = await self.conn.readline()  # Pause in event of outage
            data = json.loads(line)
            self.cm(data[0])
            print('Got {} from remote {}'.format(data, self.client_id))
            self.data = data

    # Send [ID, message count since last outage]
    async def writer(self):
        print('Started writer')
        count = 0
        while True:
            for _ in range(4):
                data = [self.tx_msg_id, count]
                self.tx_msg_id += 1
                count += 1
                await self.conn  # Only launch write if link is up
                print('Sent {} to remote {}\n'.format(data, self.client_id))
                asyncio.create_task(self.conn.write(json.dumps(data), wait=False))
            await asyncio.sleep(3.95)

async def main():
    loop = asyncio.get_event_loop()  # TEST
    loop.set_exception_handler(_handle_exception)  # TEST
    app = App('qos')
    await server.run({'qos'}, True, port=PORT, timeout=TIMEOUT)

def run():
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('Interrupted')
    finally:
        print('Closing sockets')
        server.Connection.close_all()
        asyncio.new_event_loop()


if __name__ == "__main__":
    run()
