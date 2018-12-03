# client_w.py Demo of a resilient asynchronous full-duplex ESP8266 client

# Released under the MIT licence.
# Copyright (C) Peter Hinch 2018
import sys
sys.path.pop(0)  # Ignore frozen bytecode for RAM tests

import gc
gc.collect()
print(101, gc.mem_free())
import usocket as socket
import uasyncio as asyncio
import network
import utime
# Up to here RAM used by imports is virtually zero
try:
    import primitives as asyn  # Stripped-down asyn.py
except ImportError:
    import asyn
# Get local config. ID is string of form '1\n'
from local import *
gc.collect()
print(106, gc.mem_free())

class Client():
    def __init__(self, loop, verbose, led):
        gc.collect()
        print(200, gc.mem_free())
        self.timeout = TIMEOUT  # Server timeout from local.py
        self.verbose = verbose
        self.led = led
        self._sta_if = network.WLAN(network.STA_IF)
        self._sta_if.active(True)
        gc.collect()
        print(201, gc.mem_free())
        ap = network.WLAN(network.AP_IF)
        ap.active(False)
        self.server = socket.getaddrinfo(SERVER, PORT)[0][-1]  # server read
        gc.collect()
        print(202, gc.mem_free())
        self.evfail = asyn.Event(100)  # 100ms pause
        self.evread = asyn.Event(100)
        self.evsend = asyn.Event(100)
        self.lock = asyn.Lock(100)
        self.connects = 0  # Connect count for test purposes/app access
        self.sock = None
        self.ok = False
        gc.collect()
        print(1, gc.mem_free())
        loop.create_task(self._run(loop))

# **** API ****
    def __iter__(self):  # App can await a connection
        while not self.ok:
            yield from asyncio.sleep_ms(500)

    def status(self):
        return self.ok

    async def readline(self):
        ev = self.evread
        await ev
        d = ev.value()
        ev.clear()
        return d  # None on failure

    async def write(self, buf):
        ev = self.evsend
        ev.set(buf)
        while ev.is_set():
            await asyncio.sleep_ms(100)
        return not self.evfail.is_set()  # False if data not sent. This is crude: link may have gone down before we wrote to sock

    def close(self):
        self.verbose and print('Closing sockets.')
        if isinstance(self.sock, socket.socket):
            self.sock.close()

# **** API end ****

    # Make an attempt to connect to WiFi. May not succeed.
    async def _connect(self, s):
        self.verbose and print('Connecting to WiFi')
        s.connect()  # ESP8266 remembers connection.
        # Break out on fail or success.
        while s.status() == network.STAT_CONNECTING:
            await asyncio.sleep(1)
        t = utime.ticks_ms()
        self.verbose and print('Checking WiFi stability for {}ms'.format(2 * self.timeout))
        # Timeout ensures stable WiFi and forces minimum outage duration
        while s.isconnected() and utime.ticks_diff(utime.ticks_ms(), t) < 2 * self.timeout:
            await asyncio.sleep(1)

    async def _run(self, loop):
        s = self._sta_if
        while True:
            while not s.isconnected():  # Try until stable for 2*server timeout
                await self._connect(s)
            self.verbose and print('WiFi OK')
            self.sock = socket.socket()
            try:
                self.sock.connect(self.server)
                self.sock.setblocking(False)
                await self._send(MY_ID)  # Can throw OSError
            except OSError:
                pass
            else:
                self.evfail.clear()
                gc.collect()
                print('2', gc.mem_free())
                loop.create_task(asyn.Cancellable(self._reader)())
                loop.create_task(asyn.Cancellable(self._writer)())
                loop.create_task(asyn.Cancellable(self._keepalive)())
                gc.collect()
                print('3', gc.mem_free())
                self.ok = True  # TODO is this right? Should we wait for timeout+ ? Or event from ._readline
                await self.evfail  # Pause until something goes wrong
                self.ok = False
                await asyn.Cancellable.cancel_all()
                self.close()  # Close sockets
                self.verbose and print('Fail detected. Coros stopped, disconnecting.')
            s.disconnect()
            await asyncio.sleep(1)
            while s.isconnected():
                await asyncio.sleep(1)

    @asyn.cancellable
    async def _reader(self):  # Entry point is after a (re) connect.
        c = self.connects  # Count successful connects
        try:
            while True:
                r = await self._readline()  # OSError on fail
                self.evread.set(r)  # Read succeded: flag .readline
                if c == self.connects:
                    self.connects += 1  # update connect count
        except OSError:
            pass
        self.evfail.set()
        self.evread.set(None)  # Flag fail to .readline

    @asyn.cancellable
    async def _writer(self):
        try:
            while True:
                await self.evsend
                async with self.lock:
                    await self._send(self.evsend.value())
                self.verbose and print('Sent data', self.evsend.value())
                self.evsend.clear()
                await asyncio.sleep(5)
        except OSError:
            pass
        self.evfail.set()
        self.evsend.clear()

    @asyn.cancellable
    async def _keepalive(self):
        tim = self.timeout * 2 // 3  # Ensure  >= 1 keepalives in server t/o
        try:
            while True:
                await asyncio.sleep_ms(tim)
                async with self.lock:
                    await self._send('\n')
        except OSError:
            pass
        self.evfail.set()

    # Read a line from nonblocking socket: reads can return partial data which
    # are joined into a line. Blank lines are keepalive packets which reset
    # the timeout: _readline() pauses until a complete line has been received.
    async def _readline(self):
        line = b''
        start = utime.ticks_ms()
        while True:
            if line.endswith(b'\n'):
                if len(line) > 1:
                    return line
                line = b''
                start = utime.ticks_ms()  # Blank line is keepalive
                if self.led is not None:
                    self.led(not self.led())
            await asyncio.sleep_ms(100)  # nonzero wait seems empirically necessary
            d = self.sock.readline()
            if d == b'':
                raise OSError
            if d is not None:
                line = b''.join((line, d))
            if utime.ticks_diff(utime.ticks_ms(), start) > self.timeout:
                raise OSError
 
    async def _send(self, d):  # Write a line to either socket.
        start = utime.ticks_ms()
        while len(d):
            ns = self.sock.send(d)  # OSError if client fails
            d = d[ns:]  # Possible partial write
            await asyncio.sleep_ms(100)
            if utime.ticks_diff(utime.ticks_ms(), start) > self.timeout:
                raise OSError
