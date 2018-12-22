#! /usr/bin/env python3
# -*- coding: utf-8 -*-

# s_comms_cp.py Server-side application demo. Accepts data from one client and
# sends it to another
# Run under CPython 3.5 or later.

# Released under the MIT licence.
# Copyright (C) Peter Hinch 2018

import asyncio
import json

from micropython_iot import server_cp as server
from .local import PORT, TIMEOUT


class App:
    data = None
    clients = {'rx', 'tx'}  # Expected clients

    def __init__(self, loop, client_id):
        self.client_id = client_id  # This instance talks to this client
        self.conn = None  # Connection instance
        loop.create_task(self.start(loop))

    async def start(self, loop):
        my_id = self.client_id
        print('Client {} Awaiting connection.'.format(my_id))
        # Wait for this client to connect
        self.conn = await server.client_conn(my_id)
        print('Got connect from client {} - waiting for peers.'.format(my_id))
        try:
            App.clients.remove(my_id)
        except KeyError:
            print('Warning: unexpected or duplicate client ID', my_id)
        # Wait for all clients to connect: other App instances remove their ID
        while len(App.clients):
            await asyncio.sleep(1)
        print('All peers are connected.')

        if my_id == 'tx':  # Client is sending
            loop.create_task(self.reader())
        else:
            loop.create_task(self.writer())

    async def reader(self):
        print('Started reader')
        while True:
            line = await self.conn.readline()  # Pause in event of outage
            App.data = json.loads(line)
            print('Got', App.data, 'from remote', self.client_id)

    async def writer(self):
        print('Started writer')
        data = None
        while True:
            while App.data == data:
                await asyncio.sleep(0)
            data = App.data
            await self.conn.write(json.dumps(data), False)  # Reduce latency
            print('Sent', data, 'to remote', self.client_id, '\n')


def run():
    loop = asyncio.get_event_loop()
    clients = [App(loop, name) for name in ('tx', 'rx')]  # Accept 2 clients
    try:
        loop.run_until_complete(server.run(loop, 10, False, PORT, TIMEOUT))
    except KeyboardInterrupt:
        print('Interrupted')
    finally:
        print('Closing sockets')
        server.Connection.close_all()


if __name__ == "__main__":
    run()
