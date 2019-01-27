# client.py Client class for resilient asynchronous IOT communication link.

# Released under the MIT licence.
# Copyright (C) Peter Hinch, Kevin Köck 2019

# After sending ID now pauses before sending further data to allow server to
# initiate read task.

import gc

gc.collect()
import usocket as socket
import uasyncio as asyncio

gc.collect()

import network
import errno
import utime
import ubinascii

gc.collect()
from . import gmid, isnew, launch, Event, Lock, SetByte  # __init__.py

getmid = gmid()  # Message ID generator
gc.collect()


class Client:
    def __init__(self, loop, my_id, server, port, timeout,
                 connected_cb=None, connected_cb_args=None,
                 verbose=False, led=None, in_order=False):
        """
        Create a client connection object
        :param loop: uasyncio loop
        :param my_id: client id
        :param server: server address/ip
        :param port: port the server app is running on
        :param timeout: connection timeout
        :param connected_cb: cb called when (dis-)connected to server
        :param connected_cb_args: optional args to pass to connected_cb
        :param verbose: debug output
        :param led: led output for showing connection state, heartbeat
        :param in_order: strictly send messages in order. Prevents multiple concurrent
        writes to ensure that all messages are sent in order even if an outage occurs.
        """
        self._loop = loop
        self._my_id = my_id
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
        self._s_lock = Lock(100)  # For internal send conflict.

        self.connects = 0  # Connect count for test purposes/app access
        self._sock = None
        self._ok = False  # Set after 1st successful read

        self._tx_mid = 0  # sent mid +1, used for keeping messages in order
        self._acks_pend = SetByte()  # ACKs which are expected to be received

        self._mcw = not in_order  # multiple concurrent writes.
        gc.collect()
        loop.create_task(self._run(loop))

    # **** API ****
    def __iter__(self):  # App can await a connection
        while not self._ok:
            yield from asyncio.sleep_ms(500)

    __await__ = __iter__

    def status(self) -> bool:
        """
        Returns the state of the connection
        :return: bool
        """
        return self._ok

    async def readline(self) -> str:
        """
        Reads one line
        :return: string
        """
        h, d = await self.read()
        return d

    async def read(self) -> (bytearray, str):
        """
        Reads one message containing header and line.
        Header can be None if not used.
        :return: header, line
        """
        await self._evread
        h, d = self._evread.value()
        self._evread.clear()
        return h, d

    async def writeline(self, buf, qos=True):
        """
        Write one line.
        :param buf: str/bytes
        :param qos: bool
        :return: None
        """
        await self.write(None, buf, qos)

    async def write(self, header, buf, qos=True):
        """
        Send a new message containing header and line
        :param header: optional user header, pass None if not used
        :param buf: string/byte/None, message to be sent
        :param qos: bool
        :return: None
        """
        if buf is None:
            buf = b''
        if len(buf) > 65535:
            raise ValueError("Message longer than 65535")
        mid = next(getmid)
        preheader = bytearray(5)
        preheader[0] = mid
        preheader[1] = 0 if header is None else len(header)
        preheader[2] = (len(buf) & 0xFF) - (1 if buf.endswith(b"\n") else 0)
        preheader[3] = (len(buf) >> 8) & 0xFF  # allows for 65535 message length
        preheader[4] = 0  # special internal usages, e.g. for esp_link or ACKs
        if qos:
            preheader[4] |= 0x01  # qos==True, request ACK
        preheader = ubinascii.hexlify(preheader)
        if header is not None:
            if type(header) != bytearray:
                raise TypeError("Header has to be bytearray")
            else:
                header = ubinascii.hexlify(header)
        while self._tx_mid != mid or self._ok is False:
            # keeps order even with multiple writes and waits for connection to be ok
            await asyncio.sleep_ms(50)
        await self._write(preheader, header, buf, qos, mid)

    def close(self):
        """
        Close the connection. Closes the socket.
        :return:
        """
        self._verbose and print('Closing sockets.')
        if isinstance(self._sock, socket.socket):
            self._sock.close()

    # **** For subclassing ****

    async def bad_wifi(self):
        """
        Called if the initial wifi connection is not possible.
        Subclass to implement a solution.
        :return:
        """
        await asyncio.sleep(0)
        raise OSError('No initial WiFi connection.')

    async def bad_server(self):
        """
        Called if the initial connection to the server was not possible.
        Subclass to implement a solution.
        :return:
        """
        await asyncio.sleep(0)
        raise OSError('No initial server connection.')

    # **** API end ****

    async def _write(self, preheader, header, buf, qos, mid, ack=False):
        if buf is None:
            buf = b""
        repeat = False
        while True:
            # After an outage wait until something is received from server
            # before we send.
            while not self._ok:
                await asyncio.sleep_ms(self._tim_short)
            try:
                async with self._s_lock:
                    if qos:  # ACK is qos False, so no need to check
                        self._acks_pend.add(mid)
                    await self._send(preheader)
                    if header is not None:
                        await self._send(header)
                    await self._send(buf)
                    if buf.endswith(b"\n") is False:
                        await self._send(b"\n")
                self._verbose and print('Sent data', preheader, header, buf, qos)
            except OSError:
                self._evfail.set('writer fail')
                # Wait for a response to _evfail
                while self._ok:
                    await asyncio.sleep_ms(self._tim_short)
                continue
            if ack is False and repeat is False and self._mcw is True:
                # on repeat does not wait for ticket or modify it
                # allows next write to start before receiving ACK
                self._tx_mid += 1
                if self._tx_mid == 256:
                    self._tx_mid = 1
            repeat = True
            if qos is False:
                return True
            else:
                st = utime.ticks_ms()
                while mid in self._acks_pend and utime.ticks_diff(utime.ticks_ms(), st) < self._to:
                    await asyncio.sleep_ms(50)
                if mid in self._acks_pend:  # wait for ACK for one timeout period
                    print(utime.ticks_ms(), "ack not received")
                    self._evfail.set('timeout ACK')  # timeout, reset connection and try again
                    while self._ok:
                        await asyncio.sleep_ms(self._tim_short)
                    continue
                if self._mcw is False:
                    # if mcw are not allowed, let next coro write only after receiving an ACK
                    # to ensure that all qos messages are kept in order.
                    self._tx_mid += 1
                    if self._tx_mid == 256:
                        self._tx_mid = 1
                return True

    # Make an attempt to connect to WiFi. May not succeed.
    async def _connect(self, s):
        self._verbose and print('Connecting to WiFi')
        s.connect()
        # Break out on fail or success.
        while s.status() == network.STAT_CONNECTING:
            await asyncio.sleep(1)
        t = utime.ticks_ms()
        self._verbose and print('Checking WiFi stability for {}ms'.format(2 * self._to))
        # Timeout ensures stable WiFi and forces minimum outage duration
        # Also ensures that the server recognizes the outage
        while s.isconnected() and utime.ticks_diff(utime.ticks_ms(), t) < 2 * self._to:
            await asyncio.sleep(1)

    async def _run(self, loop):
        # ESP8266 stores last good connection. Initially give it time to re-establish
        # that link. On fail, .bad_wifi() allows for user recovery.
        await asyncio.sleep(1)  # Didn't always start after power up
        s = self._sta_if
        s.connect()
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
            except OSError as e:
                if e.args[0] in (errno.ECONNABORTED, errno.ECONNRESET, errno.ECONNREFUSED):
                    if init:
                        await self.bad_server()
                    self._sock.close()
                    await asyncio.sleep(1)  # prevents spamming "WIFI OK" if verbose and server down
                    continue  # temporary server outage?
            self._sock.setblocking(False)
            # Start reading before server can send: can't send until it
            # gets ID.
            loop.create_task(_reader)
            # Server reads ID immediately, but a brief pause is probably wise.
            await asyncio.sleep_ms(50)
            preheader = bytearray(5)
            preheader[0] = 0x2C  # mid but in this case protocol identifier but will receive an ACK with mid 0x2C
            preheader[1] = 0  # header length
            preheader[2] = len(self._my_id) & 0xFF
            preheader[3] = (len(self._my_id) >> 8) & 0xFF  # allows for 65535 message length
            preheader[4] = 0xFF  # clean connection, shows if device has been reset or just a wifi outage
            preheader = ubinascii.hexlify(preheader)
            try:
                await self._send(preheader)
                await self._send(self._my_id)
                await self._send(b"\n")
            except OSError:
                if init:  # another application could run on this port and reject messages
                    await self.bad_server()
            else:
                _keepalive = self._keepalive()
                loop.create_task(_keepalive)
                if self._concb is not None:
                    # apps might need to know connection to the server acquired
                    launch(self._concb, True, *self._concbargs)
                await self._evfail  # Pause until something goes wrong
                self._verbose and print(self._evfail.value())
                self._ok = False
                asyncio.cancel(_reader)
                asyncio.cancel(_keepalive)
                if self._concb is not None:
                    # apps might need to know if they lost connection to the server
                    launch(self._concb, False, *self._concbargs)
                await asyncio.sleep(1)  # wait for cancellation but notify application before
            finally:
                init = False
                self.close()  # Close socket
                s.disconnect()
                # await asyncio.sleep_ms(self._to * 2)  # Ensure server detects outage.
                # server should detect outage if client reconnects or no ACKs are received in qos.
                # Also reconnect takes 3s which is more than enough.
                while s.isconnected():
                    await asyncio.sleep(1)

    async def _reader(self):  # Entry point is after a (re) connect.
        c = self.connects  # Count successful connects
        self._evread.clear()  # No data read yet
        try:
            while True:
                preheader, header, line = await self._readline()  # OSError on fail
                mid = preheader[0]
                if preheader[4] == 0x2C:  # ACK
                    self._verbose and print("Got ack mid", mid)
                    self._acks_pend.discard(mid)
                    continue  # All done
                # Discard dupes. mid == 0 : Server has power cycled
                if not mid:
                    isnew(-1)  # Clear down rx message record
                if isnew(mid):
                    # Old message still pending. Discard new one peer will re-send.
                    if self._evread.is_set():
                        if preheader[4] & 0x01 == 1:  # only qos are re-send
                            self._verbose and print("Dumping new message", header, line)
                            continue
                        else:
                            self._verbose and print("Dumping old message", self._evread.value())
                    self._evread.set((header, line))
                if preheader[4] & 0x01 == 1:  # qos==True, send ACK even if dupe
                    self._loop.create_task(self._sendack(mid))
                if c == self.connects:
                    self.connects += 1  # update connect count
        except OSError:
            self._evfail.set('reader fail')  # ._run cancels other coros

    async def _sendack(self, mid):
        if self._ok is False:
            return
        preheader = bytearray(5)
        preheader[0] = mid
        preheader[1] = preheader[2] = preheader[3] = 0
        preheader[4] = 0x2C  # ACK
        await self._write(ubinascii.hexlify(preheader), None, None, qos=False, mid=mid, ack=True)
        # ACK does not get qos as server will resend message if outage occurs

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
    async def _readline(self) -> (bytearray, bytearray, str):
        line = None
        preheader = None
        header = None
        start = utime.ticks_ms()
        while True:
            # get the length of the message part to read exactly what is needed.
            if preheader is None:
                cnt = 10
            elif header is None and preheader[1] != 0:
                cnt = preheader[1] * 2
            elif line is None:
                cnt = (preheader[3] << 8) | preheader[2]
                if cnt == 0:
                    line = b""
                    cnt = 1  # new-line termination missing
            else:
                cnt = 1  # only newline-termination missing
            d = await self._read_small(cnt, start)
            if d is None:  # keepalive or EOL
                self._ok = True
                if line is not None:  # EOL
                    return preheader, header, line.decode()
                line = None
                preheader = None
                header = None
                start = utime.ticks_ms()  # Blank line is keepalive
                if self._led is not None:
                    self._led(not self._led())
                continue
            if preheader is None:
                try:
                    preheader = bytearray(ubinascii.unhexlify(d))
                except ValueError:
                    print("Error converting preheader:", d)
                    continue
            elif header is None and preheader[1] != 0:
                try:
                    header = bytearray(ubinascii.unhexlify(d))
                except ValueError:
                    print("Error converting header:", d)
                    continue
            elif line is None:
                line = d
            else:
                raise OSError  # got unexpected characters instead of \n

    async def _read_small(self, cnt, start) -> bytes:
        """
        Read exactly the amount of bytes requested.
        Saves RAM allocations and conversion time.
        :param cnt: int
        :param start: float, starting time for timeout
        :return: bytes
        """
        m = b''
        rcnt = cnt
        while True:
            try:
                d = self._sock.recv(rcnt)
            except OSError as e:
                if e.args[0] == errno.EAGAIN:
                    await asyncio.sleep_ms(25)
                    continue
                else:
                    raise OSError
            if d == b'':
                raise OSError
            if d is None:  # Nothing received: wait on server
                await asyncio.sleep_ms(25)
            elif d == b"\n":
                return None  # either EOF or keepalive
            elif d.startswith(b"\n"):  # keepalive at the start of the message
                d = d[1:]
                m = b''.join((m, d))
            else:
                m = b''.join((m, d))
            if len(m) == cnt:
                return m
            else:
                rcnt = cnt - len(m)
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
