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
    from . import Lock
else:
    import socket
    import asyncio
    import time
    import select
    import errno
    Lock = asyncio.Lock

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
async def run(loop, expected, verbose=False, port=8123, timeout=2000):
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
        if res:  # Only s_sock is polled
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
            while cls._expected:
                await asyncio.sleep(0.5)
        else:
            while not set(cls._conns.keys()).issuperset(peers):
                await asyncio.sleep(0.5)
        return conn

    @classmethod
    def close_all(cls):
        for conn in cls._conns.values():
            conn._close('Connection {} closed by application'.format(conn._cl_id))
        if cls._server_sock is not None:
            cls._server_sock.close()

    def __init__(self, loop, to_secs, c_sock, client_id, init_str, verbose):
        self._loop = loop
        self._to_secs = to_secs
        self._tim_short = self._to_secs / 10
        self._tim_ka = self._to_secs / 4  # Keepalive interval
        self._sock = c_sock  # Socket
        self._cl_id = client_id
        self._verbose = verbose
        self._newlist = bytearray(32)  # Per-client de-dupe list
        Connection._conns[client_id] = self
        try:
            Connection._expected.remove(client_id)
        except KeyError:
            print('Unknown client {} has connected. Expected {}.'.format(
                client_id, Connection._expected))

        self._getmid = gmid()  # Message ID generator
        # ._wr_pause set after initial or subsequent client connection. Cleared
        # after 1st keepalive received. We delay sending anything other than
        # keepalives while ._wr_pause is set
        self._wr_pause = True
        self._await_client = True  # Waiting for 1st received line.
        self._wlock = Lock()  # Write lock
        self._lines = []  # Buffer of received lines
        self._acks_pend = set()  # ACKs which are expected to be received
        loop.create_task(self._read(init_str))
        loop.create_task(self._keepalive())

    def _reconnect(self, c_sock):
        self._sock = c_sock
        self._wr_pause = True
        self._await_client = True

    # Have received 1st data packet from client. Launched by ._read
    async def _client_active(self):
        await asyncio.sleep(0.2)  # Let ESP get out of bed.
        self._wr_pause = False

    def status(self):
        return self._sock is not None

    __call__ = status

    def __await__(self):
        if upython:
            while not self():
                yield int(self._tim_short * 1000)
        else: 
            # CPython: Meet requirement for generator in __await__
            # https://github.com/python/asyncio/issues/451
            yield from self._status_coro().__await__()

    __iter__ = __await__  # MicroPython compatibility.

    async def _status_coro(self):
        while not self():
            await asyncio.sleep(self._tim_short)

    async def readline(self):
        l = self._readline()
        if l is not None:
            return l
        # Must wait for data
        while True:
            if not self():  # Outage
                self._verbose and print('Client:', self._cl_id, 'awaiting connection')
                await self._status_coro()
                self._verbose and print('Client:', self._cl_id, 'connected')
            while self():
                l = self._readline()
                if l is not None:
                    return l
                await asyncio.sleep(TIM_TINY)  # Limit CPU utilisation

    # Immediate return. If a non-duplicate line is ready return it.
    def _readline(self):
        while self._lines:
            line = self._lines.pop(0)
            # Discard dupes: get message ID
            mid = int(line[0:2], 16)
            # mid == 0 : client has power cycled. Clear list of mid's.
            if not mid:
                isnew(-1, self._newlist)
            if isnew(mid, self._newlist):
                return '{}{}'.format(line[2:], '\n')

    async def _read(self, istr):
        while True:
            # Start (or restart after outage). Do this promptly.
            # Fast version of await self._status_coro()
            while self._sock is None:
                await asyncio.sleep(TIM_TINY)
            start = time.time()
            while self():
                try:
                    d = self._sock.recv(4096)  # bytes object
                except OSError as e:
                    err = e.args[0]
                    if err == errno.EAGAIN:  # Would block: try later
                        if time.time() - start > self._to_secs:
                            self._close('_read timeout')  # Unless it timed out.
                        else:
                            # Waiting for data from client. Limit CPU overhead.
                            await asyncio.sleep(TIM_TINY)
                    else:
                        self._close('_read reset by peer 104')
                else:
                    start = time.time()  # Something was received
                    if self._await_client:  # 1st item after (re)start
                        self._await_client = False  # Enable write after delay
                        self._loop.create_task(self._client_active())
                    if d == b'':  # Reset by peer
                        self._close('_read reset by peer')
                        continue
                    d = d.lstrip(b'\n')  # Discard leading KA's
                    if d == b'':  # Only KA's
                        continue

                    istr += d.decode()  # Add to any partial message
                    # Strings from this point
                    l = istr.split('\n')
                    istr = l.pop()  # '' unless partial line
                    self._process_str(l)

    # Given a list of received lines remove any ka's from middle. Split into
    # messages and ACKs. Put messages into ._lines and remove ACKs from
    # ._acks_pend. Note messages in ._lines have no trailing \n.
    def _process_str(self, l):
        l = [x for x in l if x]  # Discard ka's
        self._acks_pend -= {int(x, 16) for x in l if len(x) == 2}
        lines = [x for x in l if len(x) != 2]  # Lines received
        if lines:
            self._lines.extend(lines)
            for line in lines:
                self._loop.create_task(self._sendack(int(line[0:2], 16)))

    async def _sendack(self, mid):
        async with self._wlock:
            await self._send('{:02x}\n'.format(mid))

    async def _keepalive(self):
        while True:
            await self._vwrite(None)
            await asyncio.sleep(self._tim_ka)

    async def write(self, line, qos=True, wait=True):
        if qos and wait:
            while self._acks_pend:
                await asyncio.sleep(TIM_TINY)
        fstr =  '{:02x}{}' if line.endswith('\n') else '{:02x}{}\n'
        mid = next(self._getmid)
        self._acks_pend.add(mid)
        # ACK will be removed from ._acks_pend by ._read
        line = fstr.format(mid, line)  # Local copy
        await self._vwrite(line)  # Write verbatim
        if not qos:  # Don't care about ACK. All done.
            return
        # qos: pause until ACK received
        while True:
            await self._status_coro()  # Wait for outage to clear
            if await self._waitack(mid):
                return  # Got ack, removed from ._acks_pend, all done
            # Either timed out or an outage started
            await self._vwrite(line)  # Waits for outage to clear
            self._verbose and print('Repeat', line[2:], 'to server app')

    # When ._read receives an ACK it is discarded from ._acks_pend. Wait for
    # this to occur (or an outage to start). Currently use system timeout.
    async def _waitack(self, mid):
        tstart = time.time()  # Wait for ACK
        while mid in self._acks_pend:
            await asyncio.sleep(TIM_TINY)
            if not self() or ((time.time() - tstart) > self._to_secs):
                self._verbose and print('waitack timeout', mid)
                return False  # Outage or ACK not received in time
        return True

    # Verbatim write: add no message ID.
    async def _vwrite(self, line):
        ok = False
        while not ok:
            if self._verbose and not self():
                print('Writer Client:', self._cl_id, 'awaiting OK status')
            await self._status_coro()
            if line is None:
                line = '\n'  # Keepalive. Send now: don't care about loss
            else:
                # Aawait client ready after initial or subsequent connection
                while self._wr_pause:
                    await asyncio.sleep(self._tim_short)

            async with self._wlock:  # >1 writing task?
                ok = await self._send(line)  # Fail clears status

    # Send a string. Return True on apparent success, False on failure.
    async def _send(self, d):
        if not self():
            return False
        d = d.encode('utf8')  # Socket requires bytes
        start = time.time()
        while d:
            try:
                ns = self._sock.send(d)  # Raise OSError if client fails
            except OSError as e:
                err = e.args[0]
                if err == errno.EAGAIN:  # Would block: try later
                    await asyncio.sleep(0.1)
                    continue
                break
            else:
                d = d[ns:]
                if d:
                    await asyncio.sleep(self._tim_short)
                    if (time.time() - start) > self._to_secs:
                        break
        else:
            # The 0.2s delay is necessary otherwise messages can be lost if the
            # app attempts to send them in quick succession. Also occurs on
            # Pyboard D despite completely different hardware.
            # Is it better to return immediately and delay subsequent writes?
            # Should the delay be handled at a higher level?
            #await asyncio.sleep(0.2)  # Disallow rapid writes: result in data loss
            return True  # Success
        self._close('Write fail: closing connection.')
        return False

    def __getitem__(self, client_id):  # Return a Connection of another client
        return Connection._conns[client_id]

    def _close(self, reason=''):
        if self._sock is not None:
            self._verbose and print('fail detected')
            self._verbose and reason and print('Reason:', reason)
            self._sock.close()
            self._sock = None

# API aliases
client_conn = Connection.client_conn
wait_all = Connection.wait_all
