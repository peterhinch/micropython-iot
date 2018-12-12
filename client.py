# client.py Client class for resilient asynchronous IOT communication link.

# Released under the MIT licence.
# Copyright (C) Peter Hinch 2018

import gc

gc.collect()
import usocket as socket
import uasyncio as asyncio
import primitives as asyn  # Stripped down version of asyn.py
import network
import utime

gc.collect()


class Client:
    def __init__(self, loop, my_id, server, port, timeout, verbose=False, led=None):
        self.timeout = timeout  # Server timeout from local.py
        self.verbose = verbose
        self.led = led
        self.my_id = my_id
        self._sta_if = network.WLAN(network.STA_IF)
        self._sta_if.active(True)
        ap = network.WLAN(network.AP_IF)
        ap.active(False)
        self.server = socket.getaddrinfo(server, port)[0][-1]  # server read
        gc.collect()
        self.evfail = asyn.Event(100)  # 100ms pause
        self.evread = asyn.Event(100)
        self.evsend = asyn.Event(100)
        self.lock = asyn.Lock(100)
        self.connects = 0  # Connect count for test purposes/app access
        self.sock = None
        self.ok = False  # Set after 1st successful read
        gc.collect()
        loop.create_task(self._run(loop))

    # **** API ****
    def __iter__(self):  # App can await a connection
        while not self.ok:
            yield from asyncio.sleep_ms(500)

    def status(self):
        return self.ok

    async def readline(self):
        await self.evread
        d = self.evread.value()
        self.evread.clear()
        return d

    async def write(self, buf, pause=True):
        end = utime.ticks_add(self.timeout, utime.ticks_ms())
        if not buf.endswith('\n'):
            buf = ''.join((buf, '\n'))
        self.evsend.set(buf)  # Cleared after apparently succesful tx
        while self.evsend.is_set():
            await asyncio.sleep_ms(30)
        if pause:
            dt = utime.ticks_diff(end, utime.ticks_ms())
            if dt > 0:
                await asyncio.sleep_ms(dt)  # Control tx rate: <= 1 msg per timeout period

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
            while not s.isconnected():  # Try until stable for 2*.timeout
                await self._connect(s)
            self.verbose and print('WiFi OK')
            self.sock = socket.socket()
            self.evfail.clear()
            try:
                self.sock.connect(self.server)
                self.sock.setblocking(False)
                await self._send(self.my_id)  # Can throw OSError
            except OSError:
                pass
            else:
                # Improved cancellation code contributed by Kevin Köck
                _reader = self._reader()
                loop.create_task(_reader)
                _writer = self._writer()
                loop.create_task(_writer)
                _keepalive = self._keepalive()
                loop.create_task(_keepalive)
                await self.evfail  # Pause until something goes wrong
                self.ok = False
                asyncio.cancel(_reader)
                asyncio.cancel(_writer)
                asyncio.cancel(_keepalive)
                await asyncio.sleep(1)  # wait for cancellation
                self.close()  # Close sockets
                self.verbose and print('Fail detected.')
            s.disconnect()
            await asyncio.sleep(1)
            while s.isconnected():
                await asyncio.sleep(1)

    async def _reader(self):  # Entry point is after a (re) connect.
        c = self.connects  # Count successful connects
        self.evread.clear()  # No data read yet
        try:
            while True:
                r = await self._readline()  # OSError on fail
                self.evread.set(r)  # Read succeded: flag .readline
                if c == self.connects:
                    self.connects += 1  # update connect count
        except OSError:
            self.evfail.set()  # ._run cancels other coros

    async def _writer(self):
        try:
            while True:
                await self.evsend
                async with self.lock:
                    await self._send(self.evsend.value())
                self.verbose and print('Sent data', self.evsend.value())
                self.evsend.clear()  # Sent unless other end has failed and not yet detected
        except OSError:
            self.evfail.set()

    async def _keepalive(self):
        tim = self.timeout * 2 // 3  # Ensure  >= 1 keepalives in server t/o
        try:
            while True:
                await asyncio.sleep_ms(tim)
                async with self.lock:
                    await self._send('\n')
        except OSError:
            self.evfail.set()

    # Read a line from nonblocking socket: reads can return partial data which
    # are joined into a line. Blank lines are keepalive packets which reset
    # the timeout: _readline() pauses until a complete line has been received.
    async def _readline(self):
        line = b''
        start = utime.ticks_ms()
        while True:
            if line.endswith(b'\n'):
                self.ok = True  # Got at least 1 packet
                if len(line) > 1:
                    return line
                line = b''
                start = utime.ticks_ms()  # Blank line is keepalive
                if self.led is not None:
                    self.led(not self.led())
            d = self.sock.readline()
            if d == b'':
                raise OSError
            if d is None:  # Nothing received: wait on server
                await asyncio.sleep_ms(100)
            elif line == b'':
                line = d
            else:
                line = b''.join((line, d))
            if utime.ticks_diff(utime.ticks_ms(), start) > self.timeout:
                raise OSError

    async def _send(self, d):  # Write a line to either socket.
        start = utime.ticks_ms()
        nts = len(d)  # Bytes to send
        ns = 0  # No. sent
        while ns < nts:
            n = self.sock.send(d)  # OSError if client closes socket
            ns += n
            if ns < nts:  # Partial write: trim data and pause
                d = d[n:]
                await asyncio.sleep_ms(20)
            if utime.ticks_diff(utime.ticks_ms(), start) > self.timeout:
                raise OSError
