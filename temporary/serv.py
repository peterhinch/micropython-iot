# Run under CPython 3.5+ or MicroPython Unix build

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
    import logging

    fh = logging.FileHandler("server.log", mode="w")
    fh.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    formatter = logging.Formatter("[%(asctime)-15s] %(message)s")
    ch.setFormatter(formatter)
    fh.setFormatter(formatter)
    log = logging.getLogger("")
    log.setLevel(logging.DEBUG)
    log.addHandler(ch)
    log.addHandler(fh)


    def print(*args):
        m = ""
        for arg in args:
            m += str(arg)
        log.debug(m)

PORT = 8888
ACK = -1


async def run(loop, concatenate):
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
            loop.create_task(writer(c_sock, concatenate))
            loop.create_task(simulate_async_delay())
        await asyncio.sleep(0.2)


async def simulate_async_delay():
    while True:
        await asyncio.sleep(0)
        time.sleep(0.05)  # 0.2 eventually get long delays


async def reader(sock):
    print('Reader start')
    ack = [ACK, 0, 'Ack from server.']
    istr = ''
    last = -1
    lastack = -1
    while True:
        try:
            d = sock.recv(4096)  # bytes object
        except OSError as e:
            err = e.args[0]
            if err == errno.EAGAIN:  # Would block: try later
                print("EAGAIN recv")
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
                if data[0] == ACK:
                    print('Got ack', data)
                    if lastack >= 0 and data[1] - lastack - 1:
                        raise OSError('Missed ack')
                    lastack = data[1]
                else:
                    d = '{}\n'.format(json.dumps(ack))
                    await send(sock, d.encode('utf8'))
                    ack[1] += 1
                    print('Got', data)
                    if last >= 0 and data[1] - last - 1:
                        raise OSError('Missed message')
                    last = data[1]


async def writer(sock, concatenate):
    print('Writer start')
    data = [0, 0, 'Message from server.']
    while True:
        d = ''
        for x in range(4):
            m = '{}\n'.format(json.dumps(data))
            if concatenate:  # Hit ESP8266 at highest possible rate
                d = '{}{}'.format(d, m)
            else:  # Small pause between sends
                await send(sock, m.encode('utf8'))
                await asyncio.sleep(0)
            data[1] += 1
        if concatenate:
            await send(sock, d.encode('utf8'))
        print('sent', repr(d))
        await asyncio.sleep(1)  # ???


async def send(sock, d):
    while d:
        try:
            ns = sock.send(d)  # Raise OSError if client fails
        except OSError as e:
            err = e.args[0]
            if err == errno.EAGAIN:  # Would block: try later
                print("EAGAIN send")
                await asyncio.sleep(0.1)
        else:
            d = d[ns:]
            if d:
                await asyncio.sleep(0.05)


loop = asyncio.get_event_loop()
loop.create_task(run(loop, concatenate=False))
loop.run_forever()
