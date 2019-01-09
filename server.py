# server_cp.py Server for IOT communications.

# Released under the MIT licence.
# Copyright (C) Peter Hinch 2019

# Maintains bidirectional full-duplex links between server applications and
# multiple WiFi connected clients. Each application instance connects to its
# designated client. Connections are resilient and recover from outages of WiFi
# and of the connected endpoint.
# This server and the server applications are assumed to reside on a device
# with a wired interface on the local network.

# Run under CPython 3.5+ or MicroPython Unix build
import sys
from . import gmid, isnew  # __init__.py

upython = sys.implementation.name == 'micropython'
if upython:
    import usocket as socket
    import uasyncio as asyncio
    import utime as time
    import uselect as select
    import uerrno as errno
    from . import Lock, Event
else:
    import socket
    import asyncio
    import time
    import select
    import errno
    Lock = asyncio.Lock
    Event = asyncio.Event

TIM_TINY = 0.05  # Short delay avoids 100% CPU utilisation in busy-wait loops

# Read the node ID. There isn't yet a Connection instance.
# CPython does not have socket.readline. Return 1st string received
# which starts with client_id.

# Note re OSError: did detect errno.EWOULDBLOCK. Not supported in MicroPython.
# In cpython EWOULDBLOCK == EAGAIN == 11.
async def _readid(s, to_secs):
    data = ''
    start = time.time()
    while True:
        try:
            d = s.recv(4096).decode()
        except OSError as e:
            err = e.args[0]
            if err == errno.EAGAIN:
                if (time.time() - start) > to_secs:
                    raise OSError  # Timeout waiting for data
                else:
                    # Waiting for data from client. Limit CPU overhead. 
                    await asyncio.sleep(TIM_TINY)
            else:
                raise OSError  # Reset by peer 104
        else:
            if d == '':
                raise OSError  # Reset by peer or t/o
            data = '{}{}'.format(data, d)
            if data.find('\n') != -1:  # >= one line
                return data


# API: application calls server.run()
# Allow 2 extra connections. This is to cater for error conditions like
# duplicate or unexpected clients. Accept the connection and have the
# Connection class produce a meaningful error message.
async def run(loop, expected, verbose=False, port=8123, timeout=1500):
    addr = socket.getaddrinfo('0.0.0.0', port, 0, socket.SOCK_STREAM)[0][-1]
    s_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # server socket
    s_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s_sock.bind(addr)
    s_sock.listen(len(expected) + 2)
    verbose and print('Awaiting connection.', port)
    poller = select.poll()
    poller.register(s_sock, select.POLLIN)
    to_secs = timeout / 1000  # ms -> secs
    while True:
        res = poller.poll(1)  # 1ms block
        if len(res):  # Only s_sock is polled
            c_sock, _ = s_sock.accept()  # get client socket
            c_sock.setblocking(False)
            try:
                data = await _readid(c_sock, to_secs)
            except OSError:
                c_sock.close()
            else:
                Connection.go(loop, to_secs, data, verbose, c_sock, s_sock,
                              expected)
        await asyncio.sleep(0.2)


# A Connection persists even if client dies (minimise object creation).
# If client dies Connection is closed: ._close() flags this state by closing its
# socket and setting .sock to None (.status() == False).
class Connection:
    _conns = {}  # index: client_id. value: Connection instance
    _expected = set()  # Expected client_id's
    _server_sock = None

    @classmethod
    def go(cls, loop, to_secs, data, verbose, c_sock, s_sock, expected):
        client_id, init_str = data.split('\n', 1)
        verbose and print('Got connection from client', client_id)
        if cls._server_sock is None:  # 1st invocation
            cls._server_sock = s_sock
            cls._expected.update(expected)
        if client_id in cls._conns:  # Old client, new socket
            if cls._conns[client_id].status():
                print('Duplicate client {} ignored.'.format(client_id))
                c_sock.close()
            else:  # Reconnect after failure
                cls._conns[client_id]._reconnect(c_sock)
        else: # New client: instantiate Connection
            Connection(loop, to_secs, c_sock, client_id, init_str, verbose)

    # Server-side app waits for a working connection
    @classmethod
    async def client_conn(cls, client_id):
        while True:
            if client_id in cls._conns:
                c = cls._conns[client_id]
                # await c 
                # works but under CPython produces runtime warnings. So do:
                await c._status_coro()
                return c
            await asyncio.sleep(0.5)

    # App waits for all expected clients to connect.
    @classmethod
    async def wait_all(cls, client_id=None, peers=None):
        conn = None
        if client_id is not None:
            conn = await client_conn(client_id)
        if peers is None:  # Wait for all expected clients
            while len(cls._expected):
                await asyncio.sleep(0.5)
        else:
            while not set(cls._conns.keys()).issuperset(peers):
                await asyncio.sleep(0.5)
        return conn

    @classmethod
    def close_all(cls):
        for conn in cls._conns.values():
            conn._close()
        if cls._server_sock is not None:
            cls._server_sock.close()

    def __init__(self, loop, to_secs, c_sock, client_id, init_str, verbose):
        self._loop = loop
        self._to_secs = to_secs
        self._tim_short = self._to_secs / 10
        self._tim_ka = self._to_secs / 2  # Keepalive interval
        self._sock = c_sock  # Socket
        self._cl_id = client_id
        self._init = True  # Server power-up
        self._verbose = verbose
        Connection._conns[client_id] = self
        try:
            Connection._expected.remove(client_id)
        except KeyError:
            print('Unknown client {} has connected. Expected {}.'.format(
                client_id, Connection._expected))

        # Event set after initial or subsequent client connection when 1st
        # keepalive received. We delay sending anything other than keepalives
        # this has occurred.
        self._client_up = Event(100) if upython else Event()
        self._getmid = gmid()  # Generator for message ID's
        self._wlock = Lock()  # Write lock
        self._lines = []  # Buffer of received lines
        loop.create_task(self._read(init_str))
        loop.create_task(self._keepalive())

    def _reconnect(self, c_sock):
        self._sock = c_sock
        self._client_up.clear()  # Not up until we receive

    # Have received 1st data packet from client.
    async def _client_active(self):
        await asyncio.sleep(0.2)  # Let ESP get out of bed.
        self._client_up.set()  # Now we can send data

    def status(self):
        return self._sock is not None
    
    __call__ = status

    def __await__(self):
        if upython:
            while not self():
                yield self._tim_short
        else:  # CPython: Meet requirement for generator in __await__
            return self._status_coro().__await__()

    __iter__ = __await__

    async def _status_coro(self):
        while not self():
            await asyncio.sleep(self._tim_short)

    async def readline(self):
        while True:
            if self._verbose and not self():
                print('Reader Client:', self._cl_id, 'awaiting OK status')
            await self._status_coro()
            self._verbose and print('Reader Client:', self._cl_id, 'OK')
            while self():
                if len(self._lines):
                    line = self._lines.pop(0)
                    # Discard dupes: get message ID
                    mid = int(line[0:2], 16)
                    # mid == 0 : client has power cycled
                    if not mid:
                        isnew(-1)
                    # _init : server has powered up
                    if self._init or not mid or isnew(mid):
                        self._init = False
                        return '{}{}'.format(line[2:], '\n')

                await asyncio.sleep(TIM_TINY)  # Limit CPU utilisation
            self._verbose and print('Read client disconnected: closing connection.')
            self._close()

    async def _read(self, init_str):
        while True:
            # Start (or restart after outage). Do this promptly.
            # Fast version of await self._status_coro()
            while self._sock is None:
                await asyncio.sleep(TIM_TINY)
            buf = bytearray(init_str.encode('utf8'))
            start = time.time()
            while self():
                try:
                    d = self._sock.recv(4096)
                except OSError as e:
                    err = e.args[0]
                    if err == errno.EAGAIN:  # Would block: try later
                        if time.time() - start > self._to_secs:
                            self._close()  # Unless it timed out.
                        else:
                            # Waiting for data from client. Limit CPU overhead.
                            await asyncio.sleep(TIM_TINY)
                    else:
                        self._close()  # Reset by peer 104
                else:
                    start = time.time()  # Something was received
                    if not self._client_up.is_set():  # And it's the 1st item
                        self._loop.create_task(self._client_active())  # Can write
                    if d == b'':  # Reset by peer
                        self._close()
                    buf.extend(d)
                    l = bytes(buf).decode().split('\n')
                    if len(l) > 1:  # Have at least 1 newline
                        last = l.pop()  # If not '' it's a partial line
                        self._lines.extend([x for x in l if x])  # Discard ka's
                        buf = bytearray(last.encode('utf8')) if last else bytearray()

    async def _keepalive(self):
        while True:
            await self._vwrite(None)
            await asyncio.sleep(self._tim_ka)

    # qos>0 Repeat tx if outage occurred after initial tx (1st may have been lost)
    async def _do_qos(self, buf):
        while True:
            await asyncio.sleep(self._to_secs)
            if self():
                return
            await self._vwrite(buf)
            self._verbose and print('Repeat', buf, 'to server app')

    async def write(self, line, pause=True, qos=True):
        fstr =  '{:02x}{}' if line.endswith('\n') else '{:02x}{}\n'
        buf = fstr.format(next(self._getmid), line)  # Local copy
        end = time.time() + self._to_secs
        await self._vwrite(buf)
        # Ensure qos by conditionally repeating the message
        if qos:
            self._loop.create_task(self._do_qos(buf))
        if pause:  # Throttle rate of non-keepalive messages
            dt = end - time.time()
            if dt > 0:
                await asyncio.sleep(dt)  # Control tx rate: <= 1 msg per timeout period

    async def _vwrite(self, buf):  # Verbatim write: add no message ID
        ok = False
        while not ok:
            if self._verbose and not self():
                print('Writer Client:', self._cl_id, 'awaiting OK status')
            await self._status_coro()
            if buf is None:
                buf = '\n'  # Keepalive. Send now: don't care about loss
            else:  # Check if client is ready after initial or subsequent
                await self._client_up.wait()  # connection

            async with self._wlock:  # >1 writing task?
                ok = await self._send(buf)  # Fail clears status

    # Send a string as bytes. Return True on apparent success, False on failure.
    async def _send(self, d):
        if not self():
            return False
        d = d.encode('utf8')
        start = time.time()
        while len(d):
            try:
                ns = self._sock.send(d)  # Raise OSError if client fails
            except OSError:
                break
            else:
                d = d[ns:]
                if len(d):
                    await asyncio.sleep(self._tim_short)
                    if (time.time() - start) > self._to_secs:
                        break
        else:
            return True  # Success
        self._verbose and print('Write fail: closing connection.')
        self._close()
        return False

    def __getitem__(self, client_id):  # Return a Connection of another client
        return Connection._conns[client_id]

    def _close(self):
        if self._sock is not None:
            self._verbose and print('fail detected')
            self._sock.close()
            self._sock = None

# API aliases
client_conn = Connection.client_conn
wait_all = Connection.wait_all
