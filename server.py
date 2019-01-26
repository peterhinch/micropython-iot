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
    import ubinascii
else:
    import socket
    import asyncio
    import time
    import select
    import errno
    import binascii as ubinascii

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
        line, init_str = data.split('\n', 1)
        preheader = bytearray(ubinascii.unhexlify(line[:10].encode()))
        mid = preheader[0]
        if mid != 0x2C:
            c_sock.close()
            verbose and print("Wrong protocol")
            return
        client_id = line[10:]
        print("Got client_id", preheader, client_id)
        verbose and print('Got connection from client', client_id)
        if preheader[4] == 0xFF:
            verbose and print("Reconnected client", client_id)
        if cls._server_sock is None:  # 1st invocation
            cls._server_sock = s_sock
            cls._expected.update(expected)
        if client_id in cls._conns:  # Old client, new socket
            if cls._conns[client_id].status():
                print('Duplicate client {} ignored.'.format(client_id))
                c_sock.close()
            else:  # Reconnect after failure
                cls._conns[client_id]._reconnect(c_sock)
        else:  # New client: instantiate Connection
            Connection(loop, to_secs, c_sock, client_id, init_str, verbose)

    # Server-side app waits for a working connection
    @classmethod
    async def client_conn(cls, client_id):
        while True:
            if client_id in cls._conns:
                c = cls._conns[client_id]
                # await c 
                # works but under CPython produces runtime warnings. So do:
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
        self._verbose = verbose
        self._newlist = bytearray(32)  # Per-client de-dupe list
        Connection._conns[client_id] = self
        try:
            Connection._expected.remove(client_id)
        except KeyError:
            print('Unknown client {} has connected. Expected {}.'.format(
                client_id, Connection._expected))

        # ._wr_pause set after initial or subsequent client connection. Cleared
        # after 1st keepalive received. We delay sending anything other than
        # keepalives while ._wr_pause is set
        self._wr_pause = True
        self._await_client = True  # Waiting for 1st received line.
        self._getmid = gmid()  # Generator for message ID's
        self._wlock = Lock()  # Write lock
        self._lines = []  # Buffer of received lines
        self._acks_pend = set()  # ACKs which are expected to be received
        self._ack_mid = -1  # last received ACK mid
        self._tx_mid = -1  # sent mid, used for keeping messages in order

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
                yield self._tim_short
        else:  # CPython: Meet requirement for generator in __await__
            return self._status_coro().__await__()

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
                h, l = self._readline()
                if l is not None:
                    return h, l
                await asyncio.sleep(TIM_TINY)  # Limit CPU utilisation

    # Immediate return. If a non-duplicate line is ready return it.
    def _readline(self):
        while self._lines:
            header, line = self._lines.pop(0)
            return header, line
        return None, None

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
                            self._close()  # Unless it timed out.
                        else:
                            # Waiting for data from client. Limit CPU overhead.
                            await asyncio.sleep(TIM_TINY)
                    else:
                        self._close()  # Reset by peer 104
                else:
                    start = time.time()  # Something was received
                    if self._await_client:  # 1st item after (re)start
                        self._await_client = False  # Enable write after delay
                        self._loop.create_task(self._client_active())
                    if d == b'':  # Reset by peer
                        self._close()
                        continue
                    d = d.lstrip(b'\n')  # Discard leading KA's
                    if d == b'':  # Only KA's
                        continue
                    istr += d.decode()  # Add to any partial message
                    # Strings from this point
                    l = istr.split('\n')
                    istr = l.pop()  # '' unless partial line
                    self._process_str(l)  # Discard ka's

    def _process_str(self, l):
        l = [x for x in l if x]  # Discard ka's
        assert len(l) > 0, 'Zero length string in ._process_str.'
        ret = []
        for i in range(0, len(l)):  # should actually always have only one entry
            line = l[i]
            if len(line):  # Ignore keepalives
                # Discard dupes: get message ID
                preheader = bytearray(ubinascii.unhexlify(line[:10].encode()))
                mid = preheader[0]
                if preheader[4] == 0x2C:  # ACK
                    self._ack_mid = mid
                    print("Got ACK mid", mid)
                    continue
                if not mid:
                    isnew(-1, self._newlist)
                if isnew(mid, self._newlist):
                    if preheader[1] != 0:
                        header = bytearray(ubinascii.unhexlify(line[10:10 + preheader[1] * 2].encode()))
                        line = line[10 + preheader[1] * 2:]
                    else:
                        header = None
                        line = line[10:]
                    print("Got message", preheader, header, line)
                    ret.append((header, line))  # API change, also line is not new-line terminated
                else:
                    print("Dumped dupe mid", mid)
                if preheader[4] & 0x01 == 1:  # qos==True, send ACK even if dupe
                    preheader[1] = preheader[2] = preheader[3] = 0
                    preheader[4] = 0x2C  # ACK
                    fstr = "{}\n"
                    buf = fstr.format(ubinascii.hexlify(preheader).decode())
                    self._loop.create_task(self._sendack(buf, mid=preheader[0]))
                    # ACK does not get qos as server will resend message if outage occurs

    async def _sendack(self, buf, mid):
        await self._vwrite(buf, qos=False, mid=mid)

    async def _keepalive(self):
        while True:
            await self._vwrite(None, mid=None, qos=False)
            await asyncio.sleep(self._tim_ka)

    async def write(self, header, line, qos=True):
        if header is not None:
            if type(header) != bytearray:
                raise TypeError("Header has to be bytearray")
        if len(line) > 65535:
            raise ValueError("Message longer than 65535")
        preheader = bytearray(5)
        preheader[0] = next(self._getmid)
        preheader[1] = 0 if header is None else len(header)
        preheader[2] = len(line) & 0xFF
        preheader[3] = (len(line) >> 8) & 0xFF  # allows for 65535 message length
        preheader[4] = 0  # special internal usages, e.g. for esp_link
        if qos:
            preheader[4] |= 0x01  # qos==True, request ACK
        mid = preheader[0]
        preheader = ubinascii.hexlify(preheader).decode()
        while self._tx_mid + 1 != mid:
            await asyncio.sleep(0.05)
        fstr = "{}{}{}" if line.endswith("\n") else "{}{}{}\n"
        buf = fstr.format(preheader, "" if header is None else ubinascii.hexlify(header).decode(), line)
        await self._vwrite(buf, mid, qos)
        self._verbose and print('Sent data', buf)

    async def _vwrite(self, buf, mid, qos):  # Verbatim write: add no message ID
        if buf is None:
            print("vwrite", buf, mid, qos)
        while True:
            ok = False
            while not ok:
                if self._verbose and self._sock is None:
                    print('Writer Client:', self._cl_id, 'awaiting OK status')
                while self._sock is None:
                    await asyncio.sleep(0.05)
                if buf is None:
                    buf = '\n'  # Keepalive. Send now: don't care about loss

                async with self._wlock:  # >1 writing task?
                    ok = await self._send(buf)  # Fail clears status
            self._tx_mid += 1  # Let next task write before receiving ACK
            self._acks_pend.add(mid)
            if qos:
                end = time.time() + self._to_secs
                while mid in self._acks_pend and time.time() < end:
                    await asyncio.sleep(0.05)
                if mid in self._acks_pend:
                    self._verbose and print("Timeout or ack mid mismatch, closing connection")
                    self._close()
                    await asyncio.sleep(1)
                    continue
            break

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
            await asyncio.sleep(0.2)  # Disallow rapid writes: result in data loss
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
