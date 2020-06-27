#! /usr/bin/env python3
# -*- coding: utf-8 -*-

# s_app_cp.py Server-side application demo
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
from micropython_iot import server
from .local import PORT, TIMEOUT


class App:
    def __init__(self, client_id):
        self.client_id = client_id  # This instance talks to this client
        self.conn = None  # Connection instance
        self.tx_msg_id = 0
        self.dupes = 0  # Incoming dupe count
        self.rxbuf = []
        self.missing = 0
        self.last = 0
        asyncio.create_task(self.start())

    async def start(self):
        print('Client {} Awaiting connection.'.format(self.client_id))
        self.conn = await server.client_conn(self.client_id)
        asyncio.create_task(self.reader())
        asyncio.create_task(self.writer())

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

async def main():
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
