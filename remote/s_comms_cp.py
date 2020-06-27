#! /usr/bin/env python3
# -*- coding: utf-8 -*-

# s_comms_cp.py Server-side application demo. Accepts data from one client and
# sends it to another
# Run under CPython 3.8 or later.

# Released under the MIT licence. See LICENSE.
# Copyright (C) Peter Hinch 2018-2020

try:
    import uasyncio as asyncio
    import ujson as json
except ImportError:  #CPython
    import asyncio
    import json

from micropython_iot import server

from .local import PORT, TIMEOUT


class App:
    data = None
    trig_send = asyncio.Event()

    def __init__(self, client_id):
        self.client_id = client_id  # This instance talks to this client
        self.conn = None  # Connection instance
        asyncio.create_task(self.start())

    async def start(self):
        my_id = self.client_id
        print('Client {} Awaiting connection.'.format(my_id))
        # Wait for all clients to connect
        self.conn = await server.wait_all(my_id)
        print('Message from {}: all peers are connected.'.format(my_id))

        if my_id == 'tx':  # Client is sending
            asyncio.create_task(self.reader())
        else:
            asyncio.create_task(self.writer())

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
    apps = [App(name) for name in clients]  # Accept 2 clients
    try:
        asyncio.run(server.run(clients, False, port=PORT, timeout=TIMEOUT))
    except KeyboardInterrupt:
        print('Interrupted')
    finally:
        print('Closing sockets')
        server.Connection.close_all()
        asyncio.new_event_loop()


if __name__ == "__main__":
    run()
