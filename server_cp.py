# server_cp.py Server for IOT communications.

# Released under the MIT licence.
# Copyright (C) Peter Hinch 2018

# Maintains bidirectional full-duplex links between server applications and
# multiple WiFi connected clients. Each application instance connects to its
# designated client. Connections are resilient and recover from outages of WiFi
# and of the connected endpoint.
# This server and the server applications are assumed to reside on a device
# with a wired interface on the local network.

# Run under CPython 3.5+

import socket
import asyncio
import time
import select
import errno
from local import PORT, TIMEOUT

TO_SECS = TIMEOUT / 1000  # ms to seconds
TIM_SHORT = TO_SECS / 10  # Delay << timeout
TIM_TINY = 0.05  # Short delay avoids 100% CPU utilisation in busy-wait loops

# Read the node ID. This reads data one byte at a time: there isn't yet a
# Connection instance to store incoming data.
async def _readid(s):
    line = ''
    start = time.time()
    while True:
        try:
            d = s.recv(1).decode()
        except socket.error as e:
            err = e.args[0]
            if err == errno.EAGAIN or err == errno.EWOULDBLOCK:
                await asyncio.sleep(TIM_SHORT)
            else:
                raise OSError  # Reset by peer 104
        else:
            if d == '' or (time.time() - start) > TO_SECS:
                raise OSError  # Reset by peer or t/o
            line = ''.join((line, d))
            if len(line) and line.endswith('\n'):
                return line.rstrip()
        await asyncio.sleep(0)

# Server-side app waits for a working connection
async def client_conn(client_id):
    while True:
        if client_id in Connection.conns:
            c = Connection.conns[client_id]
            await c
            return c
        await asyncio.sleep(0.5)

# API: application calls server.run()
async def run(loop, nconns=10, verbose=False):
    addr = socket.getaddrinfo('0.0.0.0', PORT, 0, socket.SOCK_STREAM)[0][-1]
    s_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # server socket
    s_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s_sock.bind(addr)
    s_sock.listen(nconns)
    verbose and print('Awaiting connection.')
    poller = select.poll()
    poller.register(s_sock, select.POLLIN)
    while True:
        res = poller.poll(1)  # 1ms block
        if len(res):  # Only s_sock is polled
            c_sock, _ = s_sock.accept()  # get client socket
            c_sock.setblocking(False)
            try:
                client_id = await _readid(c_sock)
            except OSError:
                c_sock._close()
            else:
                verbose and print('Got connection from client', client_id)
                Connection.go(loop, client_id, verbose, c_sock, s_sock)
        await asyncio.sleep(0.2)

# A Connection persists even if client dies (minimise object creation).
# If client dies Connection is closed: ._close() flags this state by closing its
# socket and setting .sock to None (.ok() == False).
class Connection():
    conns = {}  # index: client_id. value: Connection instance
    server_sock = None

    @classmethod
    def go(cls, loop, client_id, verbose, c_sock, s_sock):
        if cls.server_sock is None:
            cls.server_sock = s_sock
        if client_id not in cls.conns:  # New client: instantiate Connection
            Connection(loop, client_id, verbose)
        cls.conns[client_id].sock = c_sock

    @classmethod
    def close_all(cls):
        for conn in cls.conns.values():
            conn._close()
        if cls.server_sock is not None:
            cls.server_sock.close()

    def __init__(self, loop, client_id, verbose):
        self.client_id = client_id
        self.verbose = verbose
        Connection.conns[client_id] = self
        self.lock = asyncio.Lock()
        self.sock = None  # Socket
        loop.create_task(self._keepalive())
        self.lines = []
        loop.create_task(self._read())

    async def _read(self):
        while True:
            await self
            buf = bytearray()
            start = time.time()
            while self.ok():
                try:
                    d = self.sock.recv(4096)
                except socket.error as e:
                    err = e.args[0]
                    if err == errno.EAGAIN or err == errno.EWOULDBLOCK:
                        if time.time() - start > TO_SECS:
                            self._close()
                        await asyncio.sleep(TIM_TINY)  # Limit CPU utilisation
                    else:
                         self._close()  # Reset by peer 104
                else:
                    start = time.time()  # Something was received
                    if d == '':  # Reset by peer
                        self._close()
                    elif d is not None:
                        buf.extend(d)
                        l = bytes(buf).decode().split('\n')
                        if len(l) > 1:  # Have at least 1 newline
                            self.lines.extend(l[:-1])
                            buf = bytearray(l[-1].encode('utf8'))
                await asyncio.sleep(0)

    def ok(self):
        return self.sock is not None

    def __await__(self):
        return self._is_ok().__await__()

    async def _is_ok(self):
        while not self.ok():
            await asyncio.sleep(TIM_SHORT)

    async def readline(self):
        while True:
            if self.verbose and not self.ok():
                print('Reader Client:', self.client_id, 'awaiting OK status')
            await self
            self.verbose and print('Reader Client:', self.client_id, 'OK')
            while self.ok():
                if len(self.lines):
                    line = self.lines.pop(0)
                    if len(line):  # Ignore keepalives
                        return line + '\n'
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
            end = time.time() + TO_SECS
            if not buf.endswith('\n'):
                buf = ''.join((buf, '\n'))
        while True:
            if self.verbose and not self.ok():
                print('Writer Client:', self.client_id, 'awaiting OK status')
            await self
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
        if not self.ok():
            raise OSError
        d = d.encode('utf8')
        start = time.time()
        while len(d):
            try:
                ns = self.sock.send(d)  # Raise OSError if client fails
            except socket.error as e:
                raise OSError
            else:
                d = d[ns:]
                if len(d):
                    await asyncio.sleep(TIM_SHORT)
                    if (time.time() - start) > TO_SECS:
                        raise OSError

    def _close(self):
        if self.sock is not None:
            self.verbose and print('fail detected')
            if self.sock is not None:
                self.sock.close()
                self.sock = None
