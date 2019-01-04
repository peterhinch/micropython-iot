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

upython = sys.implementation.name == 'micropython'
if upython:
    import usocket as socket
    import uasyncio as asyncio
    import utime as time
    import uselect as select
    import uerrno as errno
    from . import primitives
    Lock = primitives.Lock
else:
    import socket
    import asyncio
    import time
    import select
    import errno
    Lock = asyncio.Lock

def nextmid(mid):  # Next expected message ID
    mid = (mid + 1) & 0xff
    return mid if mid else 1  # ID 0 only after client power up.

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

# Read the node ID. There isn't yet a Connection instance.
# CPython does not have socket.readline. Return 1st string received
# which starts with client_id.

# Note re OSError: did detect errno.EWOULDBLOCK. Not supported in MicroPython.
# In cpython EWOULDBLOCK == EAGAIN == 11.
async def _readid(s):
    data = ''
    start = time.time()
    while True:
        try:
            d = s.recv(4096).decode()
        except OSError as e:
            err = e.args[0]
            if err == errno.EAGAIN:
                if (time.time() - start) > TO_SECS:
                    raise OSError  # Timeout waiting for data
                else:
                    # Waiting for data from client. Limit CPU overhead. 
                    await asyncio.sleep(TIM_TINY)
            else:
                raise OSError  # Reset by peer 104
        else:
            if d == '':
                raise OSError  # Reset by peer or t/o
            data = ''.join((data, d))
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
    global TO_SECS
    global TIMEOUT
    global TIM_SHORT
    global TIM_TINY
    TIMEOUT = timeout
    TO_SECS = timeout / 1000  # ms to seconds
    TIM_SHORT = TO_SECS / 10  # Delay << timeout
    TIM_TINY = 0.05  # Short delay avoids 100% CPU utilisation in busy-wait loops
    verbose and print('Awaiting connection.', port)
    poller = select.poll()
    poller.register(s_sock, select.POLLIN)
    while True:
        res = poller.poll(1)  # 1ms block
        if len(res):  # Only s_sock is polled
            c_sock, _ = s_sock.accept()  # get client socket
            c_sock.setblocking(False)
            try:
                data = await _readid(c_sock)
            except OSError:
                c_sock.close()
            else:
                client_id, init_str = data.split('\n', 1)
                verbose and print('Got connection from client', client_id)
                Connection.go(loop, client_id, init_str, verbose, c_sock,
                              s_sock, expected)
        await asyncio.sleep(0.2)


# A Connection persists even if client dies (minimise object creation).
# If client dies Connection is closed: ._close() flags this state by closing its
# socket and setting .sock to None (.status() == False).
class Connection:
    _conns = {}  # index: client_id. value: Connection instance
    _expected = set()  # Expected client_id's
    _server_sock = None

    @classmethod
    def go(cls, loop, client_id, init_str, verbose, c_sock, s_sock, expected):
        if cls._server_sock is None:  # 1st invocation
            cls._server_sock = s_sock
            cls._expected.update(expected)
        if client_id in cls._conns:  # Old client, new socket
            if cls._conns[client_id].status():
                print('Duplicate client {} ignored.'.format(client_id))
                c_sock.close()
            else:  # Reconnect after failure
                loop.create_task(cls._conns[client_id]._reconn(c_sock))
                cls._conns[client_id].sock = c_sock
        else: # New client: instantiate Connection
            Connection(loop, c_sock, client_id, init_str, verbose)

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

    def __init__(self, loop, c_sock, client_id, init_str, verbose):
        self.sock = c_sock  # Socket
        self.client_id = client_id
        self.verbose = verbose
        Connection._conns[client_id] = self
        try:
            Connection._expected.remove(client_id)
        except KeyError:
            print('Unknown client {} has connected. Expected {}.'.format(
                client_id, Connection._expected))

        self._init = True
        self._mid = 0  # Message ID
        self._lastwr = None  # Last message written
        self.lock = Lock()
        loop.create_task(self._keepalive())
        self.lines = []
        loop.create_task(self._read(init_str))

    async def _read(self, init_str):
        while True:
            # Start (or restart after outage). Do this promptly.
            # Fast version of await self._status_coro()
            while self.sock is None:
                await asyncio.sleep(TIM_TINY)
            buf = bytearray(init_str.encode('utf8'))
            start = time.time()
            while self.status():
                try:
                    d = self.sock.recv(4096)
                except OSError as e:
                    err = e.args[0]
                    if err == errno.EAGAIN:  # Would block: try later
                        if time.time() - start > TO_SECS:
                            self._close()  # Unless it timed out.
                        else:
                            # Waiting for data from client. Limit CPU overhead.
                            await asyncio.sleep(TIM_TINY)
                    else:
                        self._close()  # Reset by peer 104
                else:
                    start = time.time()  # Something was received
                    if d == b'':  # Reset by peer
                        self._close()
                    buf.extend(d)
                    l = bytes(buf).decode().split('\n')
                    if len(l) > 1:  # Have at least 1 newline
                        self.lines.extend(l[:-1])
                        buf = bytearray(l[-1].encode('utf8'))

    # Connection has reconnected after a server or WiFi outage
    async def _reconn(self, sock):
        try:
            # Grab lock before any other task to rewrite last message which may
            # have been lost.
            async with self.lock:  #  Other tasks are waiting on status
                self.sock = sock
                await asyncio.sleep(0.2)  # Give client time?
                await self._send(self._lastwr)  # OSError on fail
        except OSError:
            self.verbose and print('Write client disconnected: closing connection.')
            self._close()

    def status(self):
        return self.sock is not None

    def __await__(self):
        if upython:
            while not self.status():
                yield TIM_SHORT
        else:  # CPython: Meet requirement for generator in __await__
            return self._status_coro().__await__()

    __iter__ = __await__

    async def _status_coro(self):
        while not self.status():
            await asyncio.sleep(TIM_SHORT)

    async def readline(self):
        while True:
            if self.verbose and not self.status():
                print('Reader Client:', self.client_id, 'awaiting OK status')
            await self._status_coro()
            self.verbose and print('Reader Client:', self.client_id, 'OK')
            while self.status():
                if len(self.lines):
                    line = self.lines.pop(0)
                    if len(line):  # Ignore keepalives
                        # Discard dupes: get message ID
                        mid = int(line[0:2], 16)
                        # mid == 0 : client has power cycled
                        if not mid:
                            isnew(-1)
                        # _init : server has restarted
                        if self._init or not mid or isnew(mid):
                            self._init = False
                            return ''.join((line[2:], '\n'))

                await asyncio.sleep(TIM_TINY)  # Limit CPU utilisation
            self.verbose and print('Read client disconnected: closing connection.')
            self._close()

    async def _keepalive(self):
        to = TO_SECS * 2 / 3
        while True:
            await self.write('\n')
            await asyncio.sleep(to)

    async def write(self, buf, pause=True):
        if not buf.startswith('\n'):
            fstr =  '{:02x}{}' if buf.endswith('\n') else '{:02x}{}\n'
            buf = fstr.format(self._mid, buf)
            self._lastwr = buf
            self._mid = nextmid(self._mid)
            end = time.time() + TO_SECS

        while True:
            if self.verbose and not self.status():
                print('Writer Client:', self.client_id, 'awaiting OK status')
            await self._status_coro()
            self.verbose and print('Writer Client:', self.client_id, 'OK')
            try:
                async with self.lock:  # >1 writing task?
                    await self._send(buf)  # OSError on fail
                break
            except OSError:
                self.verbose and print('Write client disconnected: closing connection.')
                self._close()
        if pause and not buf.startswith('\n'):  # Throttle rate of non-keepalive messages
            dt = end - time.time()
            if dt > 0:
                await asyncio.sleep(dt)  # Control tx rate: <= 1 msg per timeout period

    async def _send(self, d):
        if not self.status():
            raise OSError
        d = d.encode('utf8')
        start = time.time()
        while len(d):
            try:
                ns = self.sock.send(d)  # Raise OSError if client fails
            except OSError:
                raise
            else:
                d = d[ns:]
                if len(d):
                    await asyncio.sleep(TIM_SHORT)
                    if (time.time() - start) > TO_SECS:
                        raise OSError

    def __getitem__(self, client_id):  # Return a Connection of another client
        return Connection._conns[client_id]

    def _close(self):
        if self.sock is not None:
            self.verbose and print('fail detected')
            if self.sock is not None:
                self.sock.close()
                self.sock = None

# API aliases
client_conn = Connection.client_conn
wait_all = Connection.wait_all
