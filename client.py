# client.py Client class for resilient asynchronous IOT communication link.

# Released under the MIT licence.
# Copyright (C) Peter Hinch, Kevin Köck 2018

# After sending ID now pauses before sending further data to allow server to
# initiate read task.

import gc

gc.collect()
import usocket as socket
import uasyncio as asyncio
gc.collect()

import network
import utime

gc.collect()
from . import gmid, isnew, launch, Event, Lock  # __init__.py
getmid = gmid()  # Message ID generator
gc.collect()


class Client:
    def __init__(self, loop, my_id, server, port, timeout,
                 connected_cb=None, connected_cb_args=None,
                 verbose=False, led=None):
        self._loop = loop
        self._my_id = my_id if my_id.endswith('\n') else '{}{}'.format(my_id, '\n')
        self._server = server
        self._port = port
        self._to = timeout  # Client and server timeout
        self._tim_short = timeout // 10
        self._tim_ka = timeout // 2  # Keepalive interval
        self._concb = connected_cb
        self._concbargs = () if connected_cb_args is None else connected_cb_args
        self._verbose = verbose
        self._led = led

        self._sta_if = network.WLAN(network.STA_IF)
        self._sta_if.active(True)
        ap = network.WLAN(network.AP_IF)
        ap.active(False)
        gc.collect()

        self._evfail = Event(100)  # 100ms pause
        self._evread = Event()  # Respond fast to incoming
        self._evsend = Event(100)
        self._wrlock = Lock(100)
        self._lock = Lock(100)

        self.connects = 0  # Connect count for test purposes/app access
        self._sock = None
        self._ok = False  # Set after 1st successful read
        self._rxmid0 = False  # Set if mid == 0 received after server reboot
        gc.collect()
        loop.create_task(self._run(loop))

    # **** API ****
    def __iter__(self):  # App can await a connection
        while not self._ok:
            yield from asyncio.sleep_ms(500)

    __await__ = __iter__

    def status(self):
        return self._ok

    async def readline(self):
        await self._evread
        d = self._evread.value()
        self._evread.clear()
        return d

    async def write(self, buf, pause=True, qos=True):
        # Prepend message ID to a copy of buf
        fstr =  '{:02x}{}' if buf.endswith('\n') else '{:02x}{}\n'
        buf = fstr.format(next(getmid), buf)
        tsent = await self._do_write(buf)
        if qos:  # Retransmit if link has gone down
            self._loop.create_task(self._do_qos(buf))
        if pause:  # Control tx rate: <= 1 msg per timeout period
            dt = self._to - utime.ticks_diff(utime.ticks_ms(), tsent)
            if dt > 0:
                await asyncio.sleep_ms(dt)

    def close(self):
        self._verbose and print('Closing sockets.')
        if isinstance(self._sock, socket.socket):
            self._sock.close()

    # **** For subclassing ****

    async def bad_wifi(self):
        await asyncio.sleep(0)
        raise OSError('No initial WiFi connection.')

    async def bad_server(self):
        await asyncio.sleep(0)
        raise OSError('No initial server connection.')

    # **** API end ****

    # qos>0 Repeat tx if outage occurred after initial tx (1st may have been lost)
    async def _do_qos(self, line):
        while True:
            await asyncio.sleep_ms(self._to)
            if self._ok:
                return
            await self._do_write(line)
            self._verbose and print('Repeat', line, 'to server app')

    async def _do_write(self, line):
        async with self._wrlock:  # May be >1 user coro launching .write
            while self._evsend.is_set():  # _writer still busy
                await asyncio.sleep_ms(30)
            tsent = utime.ticks_ms()
            self._evsend.set(line)  # Cleared after apparently successful tx
            while self._evsend.is_set():
                await asyncio.sleep_ms(30)
        return tsent

    # Make an attempt to connect to WiFi. May not succeed.
    async def _connect(self, s):
        self._verbose and print('Connecting to WiFi')
        s.connect()  # Kevin: OSError trapping needed here?
        # Break out on fail or success.
        while s.status() == network.STAT_CONNECTING:
            await asyncio.sleep(1)
        t = utime.ticks_ms()
        self._verbose and print('Checking WiFi stability for {}ms'.format(2 * self._to))
        # Timeout ensures stable WiFi and forces minimum outage duration
        while s.isconnected() and utime.ticks_diff(utime.ticks_ms(), t) < 2 * self._to:
            await asyncio.sleep(1)

    async def _run(self, loop):
        # ESP8266 stores last good connection. Initially give it time to re-establish
        # that link. On fail, .bad_wifi() allows for user recovery.
        await asyncio.sleep(1)  # Didn't always start after power up
        s = self._sta_if
        s.connect()  # Kevin: OSError trapping needed here?
        for _ in range(4):
            await asyncio.sleep(1)
            if s.isconnected():
                break
        else:
            await self.bad_wifi()
        init = True
        while True:
            while not s.isconnected():  # Try until stable for 2*.timeout
                await self._connect(s)
            self._verbose and print('WiFi OK')
            self._sock = socket.socket()
            self._evfail.clear()
            _reader = self._reader()
            try:
                serv = socket.getaddrinfo(self._server, self._port)[0][-1]  # server read
                # If server is down OSError e.args[0] = 111 ECONNREFUSED
                self._sock.connect(serv)
                self._sock.setblocking(False)
                # Start reading before server can send: can't send until it
                # gets ID.
                loop.create_task(_reader)
                # Server reads ID immediately, but a brief pause is probably wise.
                await asyncio.sleep_ms(50)
                await self._send(self._my_id)  # Can throw OSError
            except OSError:
                if init:
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
                await self._evfail  # Pause until something goes wrong
                self._verbose and print(self._evfail.value())
                self._ok = False
                asyncio.cancel(_reader)
                asyncio.cancel(_writer)
                asyncio.cancel(_keepalive)
                await asyncio.sleep(1)  # wait for cancellation
                if self._concb is not None:
                    # apps might need to know if they lost connection to the server
                    launch(self._concb, False, *self._concbargs)
            finally:
                init = False
                self.close()  # Close socket
                s.disconnect()
                await asyncio.sleep_ms(self._to * 2)  # Ensure server detects outage
                while s.isconnected():
                    await asyncio.sleep(1)

    async def _reader(self):  # Entry point is after a (re) connect.
        c = self.connects  # Count successful connects
        self._evread.clear()  # No data read yet
        try:
            while True:
                line = await self._readline()  # OSError on fail
                # Discard dupes
                mid = int(line[0:2], 16)
                # mid == 0 : Server has power cycled
                if not mid:
                    isnew(-1)  # Clear down rx message record
                    if self._rxmid0:  # Server was reset previously
                        isnew(0)  # and user got msg. Disallow dupe.
                # _init : client has restarted. mid == 0 server power up
                if isnew(mid):
                    # Read succeeded: flag .readline
                    self._evread.set(line[2:].decode())
                    # Kevin: this logic is flawed at present because messages
                    # can be out of order. However with ACK's I think OO
                    # messages can be prevented
                    self._rxmid0 = mid == 0  # mid == 0 was sent to user
                if c == self.connects:
                    self.connects += 1  # update connect count
        except OSError:
            self._evfail.set('reader fail')  # ._run cancels other coros

    async def _writer(self):  # (re)started:
        # Wait until something is received from the server before we send.
        t = self._tim_short
        while not self._ok:
            await asyncio.sleep_ms(t)
        await asyncio.sleep_ms(self._to // 3)  # conservative
        try:
            while True:
                await self._evsend
                async with self._lock:
                    await self._send(self._evsend.value())
                self._verbose and print('Sent data', self._evsend.value())
                self._evsend.clear()  # Sent unless other end has failed and not yet detected
        except OSError:
            self._evfail.set('writer fail')

    async def _keepalive(self):
        try:
            while True:
                await asyncio.sleep_ms(self._tim_ka)
                async with self._lock:
                    await self._send(b'\n')
        except OSError:
            self._evfail.set('keepalive fail')

    # Read a line from nonblocking socket: reads can return partial data which
    # are joined into a line. Blank lines are keepalive packets which reset
    # the timeout: _readline() pauses until a complete line has been received.
    async def _readline(self):
        line = b''
        start = utime.ticks_ms()
        while True:
            if line.endswith(b'\n'):
                self._ok = True  # Got at least 1 packet
                if len(line) > 1:
                    return line
                line = b''
                start = utime.ticks_ms()  # Blank line is keepalive
                if self._led is not None:
                    self._led(not self._led())
            d = self._sock.readline()
            if d == b'':
                raise OSError
            if d is None:  # Nothing received: wait on server
                await asyncio.sleep_ms(100)
            elif line == b'':
                line = d
            else:
                line = b''.join((line, d))
            if utime.ticks_diff(utime.ticks_ms(), start) > self._to:
                raise OSError

    async def _send(self, d):  # Write a line to socket.
        start = utime.ticks_ms()
        nts = len(d)  # Bytes to send
        ns = 0  # No. sent
        while ns < nts:
            n = self._sock.send(d)  # OSError if client closes socket
            ns += n
            if ns < nts:  # Partial write: trim data and pause
                d = d[n:]
                await asyncio.sleep_ms(20)
            if utime.ticks_diff(utime.ticks_ms(), start) > self._to:
                raise OSError
