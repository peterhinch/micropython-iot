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
from micropython_iot import server


class App:
    def __init__(self, loop, client_id):
        self.client_id = client_id  # This instance talks to this client
        self.conn = None  # Connection instance
        self.tx_msg_id = 0
        self.dupes = 0  # Incoming dupe count
        self.rxbuf = []
        self.missing = 0
        self.last = 0
        loop.create_task(self.start(loop))

    async def start(self, loop):
        print('Client {} Awaiting connection.'.format(self.client_id))
        self.conn = await server.client_conn(self.client_id)
        loop.create_task(self.reader())
        loop.create_task(self.writer())

    def count_missed(self):
        if len(self.rxbuf) >= 25:
            idx = 0
            while self.rxbuf[idx] < self.last + 10:
                idx += 1
            self.last += 10
            self.missing += 10 - idx
            self.rxbuf = self.rxbuf[idx:]
        return self.missing

    async def reader(self):
        print('Started reader')
        while True:
            line = await self.conn.readline()  # Pause in event of outage
            data = json.loads(line)
            rxmid = data[0]
            if rxmid in self.rxbuf:
                self.dupes += 1
            else:
                self.rxbuf.append(rxmid)
            print('Got {} from remote {}'.format(data, self.client_id))
            print('Dupes ignored {} local {} remote. '.format(self.dupes, data[3]), end='')
            print('Missed msg {} local {} remote.'.format(self.count_missed(), data[4]))

    # Send [ID, message count since last outage]
    async def writer(self):
        print('Started writer')
        count = 0
        while True:
            data = [self.tx_msg_id, count]
            self.tx_msg_id += 1
            count += 1
            print('Sent {} to remote {}\n'.format(data, self.client_id))
            await self.conn.write(json.dumps(data))
            await asyncio.sleep(5)


def run():
    loop = asyncio.get_event_loop()
    app = App(loop, 'qos')
    try:
        loop.run_until_complete(server.run(loop, {'qos'}, True))
    except KeyboardInterrupt:
        print('Interrupted')
    finally:
        print('Closing sockets')
        server.Connection.close_all()


if __name__ == "__main__":
    run()
