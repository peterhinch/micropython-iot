#! /usr/bin/env python3
# -*- coding: utf-8 -*-

# Released under the MIT licence. See LICENSE.
# Copyright (C) Peter Hinch 2018-2020

# s_app_cp.py Server-side application demo
# Run under CPython 3.8 or later or MicroPython Unix build.
# Under MicroPython uses and requires uasyncio V3.

# The App class emulates a user application intended to service a single
# client. In this case we have four instances of the application servicing
# clients with ID's 1-4.

try:
    import asyncio
except ImportError:
    import uasyncio as asyncio
try:
    import json
except ImportError:
    import ujson as json

from micropython_iot import server
from .local import PORT, TIMEOUT


class App:
    def __init__(self, client_id):
        self.client_id = client_id  # This instance talks to this client
        self.conn = None  # Connection instance
        self.data = [0, 0, 0]  # Exchange a 3-list with remote
        asyncio.create_task(self.start())

    async def start(self):
        print('Client {} Awaiting connection.'.format(self.client_id))
        self.conn = await server.client_conn(self.client_id)
        asyncio.create_task(self.reader())
        asyncio.create_task(self.writer())

    async def reader(self):
        print('Started reader')
        while True:
            line = await self.conn.readline()  # Pause in event of outage
            self.data = json.loads(line)
            # Receives [restart count, uptime in secs, mem_free]
            print('Got', self.data, 'from remote', self.client_id)

    # Send
    # [approx app uptime in secs/5, received client uptime, received mem_free]
    async def writer(self):
        print('Started writer')
        count = 0
        while True:
            self.data[0] = count
            count += 1
            print('Sent', self.data, 'to remote', self.client_id, '\n')
            # .write() behaves as per .readline()
            await self.conn.write(json.dumps(self.data))
            await asyncio.sleep(5)

async def main():
    clients = {'1', '2', '3', '4'}
    apps = [App(n) for n in clients]  # Accept 4 clients with ID's 1-4
    await server.run(clients, True, port=PORT, timeout=TIMEOUT)

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
