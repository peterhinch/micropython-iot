# server.py Server for IOT communications.

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

class Lock():
    def __init__(self, delay=0):
        self._locked = False
        self.delay = delay

    def locked(self):
        return self._locked

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, *args):
        self.release()
        await asyncio.sleep(0)

    async def acquire(self):
        while True:
            if self._locked:
                await asyncio.sleep(self.delay)
            else:
                self._locked = True
                break

    def release(self):
        if not self._locked:
            raise RuntimeError('Attempt to release a lock which has not been set')
        self._locked = False


# Global list of open sockets. Enables application to close any open sockets in
# the event of error.
socks = []
buf = bytearray(4096)
# Read a line from a nonblocking socket. Nonblocking reads and writes can
# return partial data.
# Timeout: client is deemed dead if this period elapses without receiving data.
# This seems to be the only way to detect a WiFi failure, where the client does
# not get the chance explicitly to close the sockets.
# Note: on WiFi connected devices sleep_ms(0) produced unreliable results.
async def readid(s, timeout):
    timeout /= 1000  # ms -> s
    line = ''
    start = time.time()
    print('readid')
    while True:
        if line.endswith('\n'):
            if len(line) > 1:
                return line
            line = ''
            start = time.time()  # A blank line is just a  keepalive
        await asyncio.sleep(300)
        d = b''
        try:
            d = s.recv(1)
            print('readid got', d)
        except socket.error as e:
            err = e.args[0]
            if err == errno.EAGAIN or err == errno.EWOULDBLOCK:
                pass
            else:
                print('readid error')
                raise OSError
        d = d.decode()
        if d == '':
            raise OSError
        if d is not None:
            line = ''.join((line, d))
        if (time.time() - start) > timeout:
            raise OSError


# Server-side app waits for a working connection
async def client_conn(client_id):
    while True:
        if client_id in Connection.conns:
            c = Connection.conns[client_id]
            while not c.ok():
                await asyncio.sleep(0.5)
            return c
        await asyncio.sleep(0.5)

# API: application calls server.run()
# Not using uasyncio.start_server because of https://github.com/micropython/micropython/issues/4290
async def run(loop, nconns=10, verbose=False):
    addr = socket.getaddrinfo('0.0.0.0', PORT, 0, socket.SOCK_STREAM)[0][-1]
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    socks.append(s)
    s.bind(addr)
    s.listen(nconns)
    verbose and print('Awaiting connection.')
    poller = select.poll()
    poller.register(s, select.POLLIN)
    while True:
        res = poller.poll(100)
        for fn, ev in res:
            conn, addr = s.accept()
            conn.setblocking(False)
            try:
                idstr = await readid(conn, TIMEOUT)
                verbose and print('Got connection from client', idstr)
                socks.append(conn)
                Connection.go(loop, int(idstr), verbose, conn)
            except OSError:
                if conn is not None:
                    conn.close()
            await asyncio.sleep(200)
        await asyncio.sleep(200)

# A Connection persists even if client dies (minimise object creation).
# If client dies Connection is closed: .close() flags this state by closing its
# socket and setting .conn to None (.ok() == False).
class Connection():
    conns = {}  # index: client_id. value: Connection instance
    @classmethod
    def go(cls, loop, client_id, verbose, conn):
        if client_id not in cls.conns:  # New client: instantiate Connection
            Connection(loop, client_id, verbose)
        cls.conns[client_id].conn = conn
            
    def __init__(self, loop, client_id, verbose):
        self.client_id = client_id
        self.timeout = TIMEOUT
        self.verbose = verbose
        Connection.conns[client_id] = self
        # Startup timeout: cancel startup if both sockets not created in time
        self.lock = Lock(0.1)
        self.conn = None  # Socket
        loop.create_task(self._keepalive())
        self.lines = []
        loop.create_task(self._read())

    async def _read(self):
        while True:
            while self.conn is None:
                await asyncio.sleep(0.1)
            buf = bytearray()
            while True:
                try:
                    d = self.conn.recv(4096)
                except socket.error as e:
                    err = e.args[0]
                    if err == errno.EAGAIN or err == errno.EWOULDBLOCK:
                        await asyncio.sleep(0)
                        continue
                    else:
                        print('read error', err)  # Reset by peer 104
                        self.close()

                print('got', d)
                if d == '':
                    self.close()
                if d is None:
                    await asyncio.sleep(0)
                    continue

                buf.extend(d)
                l = bytes(buf).decode().split('\n')
                if len(l) > 1:  # Have at least 1 newline
                    self.lines.extend(l[:-1])
                    buf = bytearray(l[-1].encode('utf8'))
                await asyncio.sleep(0)
                if self.conn is None:
                    break

    def ok(self):
        return self.conn is not None

    async def readline(self):
        while True:
            if self.verbose and not self.ok():
                print('Reader Client:', self.client_id, 'awaiting OK status')
            while not self.ok():
                await asyncio.sleep(0.1)
            self.verbose and print('Reader Client:', self.client_id, 'OK')
            start = time.time()
            while time.time() - start < self.timeout:
                if len(self.lines):
                    line = self.lines.pop(0)
                    if line == '':  # Keepalive
                        start = time.time()
                    else:
                        return line + '\n'
                await asyncio.sleep(0.1)
            self.verbose and print('Read client disconnected: closing connection.')
            self.close()

    async def _keepalive(self):
        to = self.timeout * 2 // 3000
        while True:
            await self.write('\n')
            await asyncio.sleep(to)

    async def write(self, buf):
        while True:
            if self.verbose and not self.ok():
                print('Writer Client:', self.client_id, 'awaiting OK status')
            while not self.ok():
                await asyncio.sleep(0.1)
            self.verbose and print('Writer Client:', self.client_id, 'OK')
            try:
                async with self.lock:  # >1 writing task?
                    await self.send(buf)  # OSError on fail
                    print('sent', repr(buf))
                return
            except (OSError, AttributeError):
                self.verbose and print('Write client disconnected: closing connection.')
                self.close()

    async def send(self, d):
        if self.conn is None:
            raise OSError
        d = d.encode('utf8')
        timeout = self.timeout / 1000
        start = time.time()
        while len(d):
            ns = 0
            try:
                ns = self.conn.send(d)  # OSError if client fails
            except socket.error as e:
                print('Write socket error')
            d = d[ns:]
            await asyncio.sleep(0.1)  # See note above
            if (time.time() - start) > timeout:
                raise OSError

    def close(self):
        if self.conn is not None:
            if self.conn in socks:
                socks.remove(self.conn)
            self.conn.close()
            self.conn = None
