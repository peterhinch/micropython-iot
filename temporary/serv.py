# Run under CPython 3.5+ or MicroPython Unix build
# Aims to detect missed messages on socket where connection is via WiFi

import sys
upython = sys.implementation.name == 'micropython'
if upython:
    import usocket as socket
    import uasyncio as asyncio
    import uselect as select
    import uerrno as errno
    import ujson as json
    import utime as time
else:
    import socket
    import asyncio
    import select
    import errno
    import json
    import time

PORT = 8123

async def run(loop):
    addr = socket.getaddrinfo('0.0.0.0', PORT, 0, socket.SOCK_STREAM)[0][-1]
    s_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # server socket
    s_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s_sock.bind(addr)
    s_sock.listen(5)
    print('Awaiting connection on', PORT)
    poller = select.poll()
    poller.register(s_sock, select.POLLIN)
    while True:
        res = poller.poll(1)
        if res:
            c_sock, _ = s_sock.accept()  # get client socket
            c_sock.setblocking(False)
            loop.create_task(reader(c_sock))
            loop.create_task(writer(c_sock))
        await asyncio.sleep(0.2)

async def reader(sock):
    print('Reader start')
    istr = ''
    last = -1
    while True:
        try:
            d = sock.recv(4096)  # bytes object
        except OSError as e:
            err = e.args[0]
            if err == errno.EAGAIN:  # Would block: try later
                await asyncio.sleep(0.05)
        else:
            if d == b'':  # Reset by peer
                raise OSError('Client fail.')
            istr += d.decode()  # Add to any partial message
            # Strings from this point
            l = istr.split('\n')
            istr = l.pop()  # '' unless partial line
            for line in l:
                data = json.loads(line)
                print('Got', data)
                if last >= 0 and data[0] - last -1:
                    raise OSError('Missed message')
                last = data[0]

async def writer(sock):
    print('Writer start')
    data = [0, 'Message from server.']
    while True:
        m = '{}\n'.format(json.dumps(data))
        await send(sock, m.encode('utf8'))
        data[0] += 1
        print('sent', m)
        await asyncio.sleep(0.25)  # ???

async def send(sock, d):
    while d:
        try:
            ns = sock.send(d)  # Raise OSError if client fails
        except OSError as e:
            err = e.args[0]
            if err == errno.EAGAIN:  # Would block: try later
                await asyncio.sleep(0.1)
        else:
            d = d[ns:]
            if d:
                await asyncio.sleep(0.05)

loop = asyncio.get_event_loop()
loop.create_task(run(loop))
loop.run_forever()
