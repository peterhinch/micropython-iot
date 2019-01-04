# client.py Client class for resilient asynchronous IOT communication link.

# Released under the MIT licence.
# Copyright (C) Peter Hinch, Kevin Köck 2018

# After sending ID now pauses before sending further data to allow server to
# initiate read task.

import gc

gc.collect()
import usocket as socket
import uasyncio as asyncio

from . import primitives as asyn  # Stripped down version of asyn.py

import network
import utime

gc.collect()

type_gen = type((lambda: (yield))())  # Generator type


# If a callback is passed, run it and return.
# If a coro is passed initiate it and return.
# coros are passed by name i.e. not using function call syntax.
def launch(func, *tup_args):
    res = func(*tup_args)
    if isinstance(res, type_gen):
        loop = asyncio.get_event_loop()
        loop.create_task(res)

def nextmid(mid):
    mid = (mid + 1) & 0xff
    return mid if mid else 1

def isnew(mid, lst=bytearray(32)):
    if mid == -1:
        for idx in range(32):
            lst[idx] = 0
        return
    idx = mid >> 3
    bit = 1 << (mid & 7)
    res = not(lst[idx] & bit)
    lst[idx] |= bit
    lst[(idx + 16 & 0x1f)] = 0
    return res


class Client:
    def __init__(self, loop, my_id, server, port, timeout,
                 connected_cb=None, connected_cb_args=None,
                 verbose=False, led=None, qos=0):
        self.loop = loop
        self.timeout = timeout  # Server timeout
        self.verbose = verbose
        self.led = led
        self.qos = qos
        self.my_id = my_id if my_id.endswith("\n") else my_id+"\n"
        self._sta_if = network.WLAN(network.STA_IF)
        self._sta_if.active(True)
        ap = network.WLAN(network.AP_IF)
        ap.active(False)
        self.server = socket.getaddrinfo(server, port)[0][-1]  # server read
        gc.collect()
        self.evfail = asyn.Event(100)  # 100ms pause
        self.evread = asyn.Event(100)
        self.evsend = asyn.Event(100)
        self.wrlock = asyn.Lock(100)
        self.lock = asyn.Lock(100)
        self.connects = 0  # Connect count for test purposes/app access
        self._concb = connected_cb
        self._concbargs = () if connected_cb_args is None else connected_cb_args
        self.sock = None
        self.ok = False  # Set after 1st successful read
        self._mid = 0  # Message ID
        self._init = True
        gc.collect()
        loop.create_task(self._run(loop))

    # **** API ****
    def __iter__(self):  # App can await a connection
        while not self.ok:
            yield from asyncio.sleep_ms(500)

    __await__ = __iter__

    def status(self):
        return self.ok

    async def readline(self):
        await self.evread
        d = self.evread.value()
        self.evread.clear()
        return d

    # qos>0 Repeat tx if outage occurred after initial tx (1st may have been lost)
    async def repeat(self, line):
        await asyncio.sleep_ms(self.timeout)
        if self.ok:
            return

        async with self.wrlock:
            while self.evsend.is_set():  # _writer still busy
                await asyncio.sleep_ms(30)
            self.evsend.set(line)  # Cleared after apparently successful tx
            while self.evsend.is_set():
                await asyncio.sleep_ms(30)
        self.verbose and print('Repeat', line, 'to server app')

    async def write(self, buf, pause=True):
        # Prepend message ID to a copy of buf
        fstr =  '{:02x}{}' if buf.endswith('\n') else '{:02x}{}\n'
        buf = fstr.format(self._mid, buf)
        self._mid = nextmid(self._mid)

        async with self.wrlock:  # May be >1 user coro launching .write
            while self.evsend.is_set():  # _writer still busy
                await asyncio.sleep_ms(30)
            end = utime.ticks_add(self.timeout, utime.ticks_ms())
            self.evsend.set(buf)  # Cleared after apparently successful tx
            while self.evsend.is_set():
                await asyncio.sleep_ms(30)
        if self.qos:  # Retransmit if link has gone down
            self.loop.create_task(self.repeat(buf))
        if pause:
            dt = utime.ticks_diff(end, utime.ticks_ms())
            if dt > 0:
                await asyncio.sleep_ms(dt)  # Control tx rate: <= 1 msg per timeout period

    def close(self):
        self.verbose and print('Closing sockets.')
        if isinstance(self.sock, socket.socket):
            self.sock.close()

    # **** For subclassing ****

    async def bad_wifi(self):
        await asyncio.sleep(0)
        raise OSError('No initial WiFi connection.')

    async def bad_server(self):
        await asyncio.sleep(0)
        raise OSError('No initial server connection.')

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
        s.connect()
        for _ in range(4):
            await asyncio.sleep(1)
            if s.isconnected():
                break
        else:
            await self.bad_wifi()
        initialising = True
        while True:
            while not s.isconnected():  # Try until stable for 2*.timeout
                await self._connect(s)
            self.verbose and print('WiFi OK')
            self.sock = socket.socket()
            self.evfail.clear()
            _reader = self._reader()
            try:
                # If server is down OSError e.args[0] = 111 ECONNREFUSED
                self.sock.connect(self.server)
                self.sock.setblocking(False)
                # Start reading before server can send: can't send until it
                # gets ID.
                loop.create_task(_reader)
                # Server reads ID immediately, but a brief pause is probably wise.
                await asyncio.sleep_ms(50)
                await self._send(self.my_id)  # Can throw OSError
            except OSError:
                if initialising:
                    await self.bad_server()
            else:
                # Improved cancellation code contributed by Kevin Köck
                # Note _writer pauses before 1st tx
                _writer = self._writer()
                loop.create_task(_writer)
                _keepalive = self._keepalive()
                loop.create_task(_keepalive)
                if self._concb is not None:
                    # apps might need to know connection to the server acquired
                    launch(self._concb, True, *self._concbargs)
                await self.evfail  # Pause until something goes wrong
                self.verbose and print(self.evfail.value())
                self.ok = False
                asyncio.cancel(_reader)
                asyncio.cancel(_writer)
                asyncio.cancel(_keepalive)
                await asyncio.sleep(1)  # wait for cancellation
                if self._concb is not None:
                    # apps might need to know if they lost connection to the server
                    launch(self._concb, False, *self._concbargs)
#                await asyncio.sleep(1)  # wait for cancellation
            finally:
                initialising = False
                self.close()  # Close socket
                s.disconnect()
                await asyncio.sleep(1)
                while s.isconnected():
                    await asyncio.sleep(1)

    async def _reader(self):  # Entry point is after a (re) connect.
        c = self.connects  # Count successful connects
        self.evread.clear()  # No data read yet
        try:
            while True:
                line = await self._readline()  # OSError on fail
                # Discard dupes
                mid = int(line[0:2], 16)
                # mid == 0 : client has power cycled
                if not mid:
                    isnew(-1)
                # _init : client has restarted. mid == 0 server power up
                if self._init or not mid or isnew(mid):
                    self._init = False
                    # Read succeeded: flag .readline
                    self.evread.set(''.join((line[2:].decode(), '\n')))
                if c == self.connects:
                    self.connects += 1  # update connect count
        except OSError:
            self.evfail.set('reader fail')  # ._run cancels other coros

    async def _writer(self):
        # Need a delay to let server initiate: it can take 0.1*timeout before
        # good status is detected so ensure rx is ready
        await asyncio.sleep_ms(self.timeout // 3)
        # Preclude any chance of rx timeout. Lock not needed yet,
        await self._send(b'\n')
        try:
            while True:
                await self.evsend
                async with self.lock:
                    await self._send(self.evsend.value())
                self.verbose and print('Sent data', self.evsend.value())
                self.evsend.clear()  # Sent unless other end has failed and not yet detected
        except OSError:
            self.evfail.set('writer fail')

    async def _keepalive(self):
        tim = self.timeout * 2 // 3  # Ensure  >= 1 keepalives in server t/o
        try:
            while True:
                await asyncio.sleep_ms(tim)
                async with self.lock:
                    await self._send(b'\n')
        except OSError:
            self.evfail.set('keepalive fail')

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
#                print('readline sock fail')
                raise OSError
            if d is None:  # Nothing received: wait on server
                await asyncio.sleep_ms(100)
            elif line == b'':
                line = d
            else:
                line = b''.join((line, d))
            if utime.ticks_diff(utime.ticks_ms(), start) > self.timeout:
#                print('readline timeout')
                raise OSError

    async def _send(self, d):  # Write a line to socket.
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
