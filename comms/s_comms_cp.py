#! /usr/bin/env python3
# -*- coding: utf-8 -*-

# s_comms_cp.py Server-side application demo. Accepts data from one client and
# sends it to another
# Run under CPython 3.5 or later.

# Released under the MIT licence.
# Copyright (C) Peter Hinch 2018

import asyncio
import json
import server_cp as server

class App():
    data = None
    connections = set()
    NCONNS = 2  # Number of peers in network
    def __init__(self, loop, client_id):
        self.client_id = client_id  # This instance talks to this client
        self.conn = None  # Connection instance
        loop.create_task(self.start(loop))

    async def start(self, loop):
        print('Client {} Awaiting connection.'.format(self.client_id))
        # Wait for this client to connect
        self.conn = await server.client_conn(self.client_id)
        App.connections.add(self.client_id)
        print('Got connect from client {} - waiting for peers.'.format(self.client_id))
        # Wait until all peers have connected
        while len(App.connections) < App.NCONNS:
            await asyncio.sleep(1)
        print('All peers are connected.')

        if self.client_id == 'tx':  # Client is sending
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
            await self.conn.write(json.dumps(data))
            print('Sent', data, 'to remote', self.client_id, '\n')
        

def run():
    loop = asyncio.get_event_loop()
    clients = [App(loop, name) for name in ('tx', 'rx')]  # Accept 2 clients
    try:
        loop.run_until_complete(server.run(loop, 10, False))
    except KeyboardInterrupt:
        print('Interrupted')
    finally:
        print('Closing sockets')
        server.Connection.close_all()

if __name__ == "__main__":
    run()
