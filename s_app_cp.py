#! /usr/bin/env python3
# -*- coding: utf-8 -*-

# s_app_cp.py Server-side application demo
# Run under CPython 3.5 or later.

# Released under the MIT licence.
# Copyright (C) Peter Hinch 2018

# The App class emulates a user application intended to service a single
# client. In this case we have four instances of the application servicing
# clients with ID's 1-4.

import asyncio
import json
import server_cp as server

class App():
    def __init__(self, loop, client_id):
        self.data = [0, 0, 0]  # Exchange a 3-list with remote
        loop.create_task(self.start(loop, client_id))

    async def start(self, loop, client_id):
        print('Client {} Awaiting connection.'.format(client_id))
        conn = await server.client_conn(client_id)
        loop.create_task(self.reader(conn, client_id))
        loop.create_task(self.writer(conn, client_id))

    async def reader(self, conn, client_id):
        print('Started reader')
        while True:
            line = await conn.readline()  # Pause in event of outage
            self.data = json.loads(line)
            # Receives [restart count, uptime in secs, mem_free]
            print('Got', self.data, 'from remote', client_id)

    # Send [approx application uptime in secs, received client uptime]
    async def writer(self, conn, client_id):
        print('Started writer')
        count = 0
        while True:
            self.data[0] = count
            count += 1
            print('Sent', self.data, 'to remote', client_id, '\n')
            # .write() behaves as per .readline()
            await conn.write(json.dumps(self.data))
            await asyncio.sleep(5)
        

def run():
    loop = asyncio.get_event_loop()
    clients = [App(loop, str(n)) for n in range(1, 5)]  # Accept 4 clients with ID's 1-4
    try:
        loop.run_until_complete(server.run(loop, 10, False))
    except KeyboardInterrupt:
        print('Interrupted')
    finally:
        print('Closing sockets')
        server.Connection.close_all()

if __name__ == "__main__":
    run()
