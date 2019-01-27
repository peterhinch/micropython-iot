#! /usr/bin/env python3
# -*- coding: utf-8 -*-

# s_app_cp.py Server-side application demo
# Run under CPython 3.5 or later.

# Released under the MIT licence.
# Copyright (C) Peter Hinch 2018

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
    def __init__(self, loop, client_id):
        self.client_id = client_id  # This instance talks to this client
        self.conn = None  # Connection instance
        self.data = [0, 0, 0]  # Exchange a 3-list with remote
        loop.create_task(self.start(loop))

    async def start(self, loop):
        print('Client {} Awaiting connection.'.format(self.client_id))
        self.conn = await server.client_conn(self.client_id)
        loop.create_task(self.reader())
        loop.create_task(self.writer())

    async def reader(self):
        print('Started reader')
        while True:
            line = await self.conn.readline()  # Pause in event of outage
            try:
                self.data = json.loads(line)
            except json.JSONDecodeError:
                print("Error converting {!s}".format(line))
                continue
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
            await self.conn.writeline(json.dumps(self.data))
            await asyncio.sleep(5)


def run():
    loop = asyncio.get_event_loop()
    clients = {'1', '2', '3', '4'}
    apps = [App(loop, str(n)) for n in clients]  # Accept 4 clients with ID's 1-4
    try:
        loop.run_until_complete(server.run(loop, clients, True, PORT, TIMEOUT))
    except KeyboardInterrupt:
        print('Interrupted')
    finally:
        print('Closing sockets')
        server.Connection.close_all()


if __name__ == "__main__":
    run()
