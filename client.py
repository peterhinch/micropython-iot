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
from . import gmid, isnew, launch, Event, Lock, SetByte  # __init__.py
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
        self._wrlock = Lock(100)  # For user write coro conflict.
        self._s_lock = Lock(100)  # For internal send conflict.

        self.connects = 0  # Connect count for test purposes/app access
        self._sock = None
        self._ok = False  # Set after 1st successful read
        self._acks_pend = SetByte()  # ACKs which are expected to be received
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

    async def write(self, buf, qos=True):
        # Prepend message ID to a copy of buf
        fstr =  '{:02x}{}' if buf.endswith('\n') else '{:02x}{}\n'
        mid = next(getmid)
        self._acks_pend.add(mid)
        buf = fstr.format(mid, buf)
        await self._do_write(buf)
        if qos:
            await self._do_qos(mid, buf)

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

    # qos==2 Retransmit until matching ACK received
    async def _do_qos(self, mid, line):
        while True:
            while not self._ok:  # Wait for any outage to clear
                await asyncio.sleep_ms(self._tim_short)
            if await self._waitack(mid, self._to):  # How long before retransmit ???
                return  # Got ack, removed from list, all done
            await self._do_write(line)
            self._verbose and print('Repeat', line, 'to server app')

    async def _waitack(self, mid, t):
        tstart = utime.ticks_ms()  # Wait for ACK
        while mid in self._acks_pend:
            await asyncio.sleep_ms(50)
            if not self._ok or (utime.ticks_diff(utime.ticks_ms(), tstart) > t):
                self._verbose and print('waitack timeout', mid)
                return False  # No ACK received in time
        return True

    async def _do_write(self, line):
        async with self._wrlock:  # May be >1 user coro launching .write
            while self._evsend.is_set():  # _writer still busy
                await asyncio.sleep_ms(30)
            self._evsend.set(line)  # Cleared after apparently successful tx
            while self._evsend.is_set():
                await asyncio.sleep_ms(30)

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
                # No need for lock yet.
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
                mid = int(line[0:2], 16)
                if len(line) == 3:  # Got ACK: remove from expected list
                    self._acks_pend.discard(mid)  # qos0 acks are ignored
                    continue  # All done
                # Old message still pending. Discard new one peer will re-send.
                if self._evread.is_set():
                    continue
                # Message received & can be passed to user: send ack.
                self._loop.create_task(self._sendack(mid))
                # Discard dupes. mid == 0 : Server has power cycled
                if not mid:
                    isnew(-1)  # Clear down rx message record
                if isnew(mid):
                    self._evread.set(line[2:].decode())
                if c == self.connects:
                    self.connects += 1  # update connect count
        except OSError:
            self._evfail.set('reader fail')  # ._run cancels other coros

    async def _sendack(self, mid):
        async with self._s_lock:
            await self._send('{:02x}\n'.format(mid))

    async def _writer(self):  # (re)started:
        # Wait until something is received from the server before we send.
        t = self._tim_short
        while not self._ok:
            await asyncio.sleep_ms(t)
        try:
            while True:
                await self._evsend
                async with self._s_lock:
                    await self._send(self._evsend.value())
#                self._verbose and print('Sent data', self._evsend.value())
                self._evsend.clear()  # Sent unless other end has failed and not yet detected
        except OSError:
            self._evfail.set('writer fail')

    async def _keepalive(self):
        try:
            while True:
                await asyncio.sleep_ms(self._tim_ka)
                async with self._s_lock:
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
