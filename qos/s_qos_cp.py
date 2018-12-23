#! /usr/bin/env python3
# -*- coding: utf-8 -*-

# s_app_cp.py Server-side application demo
# Run under CPython 3.5 or later.

# Released under the MIT licence.
# Copyright (C) Peter Hinch 2018

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
from micropython_iot import server_cp as server
from .local import TIMEOUT, PORT


class App:
    def __init__(self, loop, client_id):
        self.client_id = client_id  # This instance talks to this client
        self.conn = None  # Connection instance
        self.tx_msg_id = 1
        self.rx_msg_id = None  # Incoming ID
        self.dupes_ignored = 0  # Incoming dupe count
        self.msg_missed = 0
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
            data = json.loads(line)
            if self.rx_msg_id is None:
                self.rx_msg_id = data[0]  # Just started
            elif self.rx_msg_id == data[0]:  # We've had a duplicate
                self.dupes_ignored += 1
                continue
            else:  # Message ID is new
                if self.rx_msg_id != data[0] - 1:
                    self.msg_missed += 1
                self.rx_msg_id = data[0]
            print('Got {} from remote {}'.format(data, self.client_id))
            print('Dupes ignored {} local {} remote.'.format(self.dupes_ignored, data[3]))
            print('Missed msg {} local {} remote.'.format(self.msg_missed, data[4]))

    # Send [ID, message count since last outage]
    async def writer(self):
        tout = TIMEOUT / 1000
        print('Started writer')
        count = 0
        while True:
            data = [self.tx_msg_id, count]
            self.tx_msg_id += 1
            count += 1
            print('Sent {} to remote {}\n'.format(data, self.client_id))
            dstr = json.dumps(data)
            await self.conn.write(dstr)
            await asyncio.sleep(tout)  # time for outage detection
            if not self.conn.status():  # Meassage may have been lost
                await self.conn.write(dstr)  # Re-send: will wait until outage clears
            await asyncio.sleep(5 - tout)


def run():
    loop = asyncio.get_event_loop()
    app = App(loop, 'qos')
    try:
        loop.run_until_complete(server.run(loop, {'qos'}, False, PORT, TIMEOUT))
    except KeyboardInterrupt:
        print('Interrupted')
    finally:
        print('Closing sockets')
        server.Connection.close_all()


if __name__ == "__main__":
    run()
