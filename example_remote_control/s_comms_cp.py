#! /usr/bin/env python3
# -*- coding: utf-8 -*-

# s_comms_cp.py Server-side application demo. Accepts data from one client and
# sends it to another
# Run under CPython 3.5 or later.

# Released under the MIT licence.
# Copyright (C) Peter Hinch 2018

import sys

upython = sys.implementation.name == 'micropython'

if upython:
    import uasyncio as asyncio
    import ujson as json
    from micropython_iot import Event
else:
    import asyncio
    import json

from micropython_iot import server
from .local_tx import PORT, TIMEOUT


class App:
    data = None
    if upython:
        trig_send = Event()
    else:
        trig_send = asyncio.Event()

    def __init__(self, loop, client_id):
        self.client_id = client_id  # This instance talks to this client
        self.conn = None  # Connection instance
        loop.create_task(self.start(loop))

    async def start(self, loop):
        my_id = self.client_id
        print('Client {} Awaiting connection.'.format(my_id))
        # Wait for all clients to connect
        self.conn = await server.wait_all(my_id)
        print('Message from {}: all peers are connected.'.format(my_id))

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
            App.trig_send.set()

    async def writer(self):
        print('Started writer')
        data = None
        while True:
            await App.trig_send.wait()
            App.trig_send.clear()
            data = App.data
            await self.conn.write(json.dumps(data), False)  # Reduce latency
            print('Sent', data, 'to remote', self.client_id, '\n')


def run():
    clients = {'rx', 'tx'}  # Expected clients
    loop = asyncio.get_event_loop()
    apps = [App(loop, name) for name in clients]  # Accept 2 clients
    try:
        loop.run_until_complete(server.run(loop, clients, False, PORT, TIMEOUT))
    except KeyboardInterrupt:
        print('Interrupted')
    finally:
        print('Closing sockets')
        server.Connection.close_all()


if __name__ == "__main__":
    run()
