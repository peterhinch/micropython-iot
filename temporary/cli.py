# cli.py Test of socket. Run on Pyboard D

import gc

gc.collect()
import usocket as socket
import uasyncio as asyncio
import ujson as json
import utime as time
import errno

gc.collect()
import network
import ubinascii
import machine

MY_ID = ubinascii.hexlify(machine.unique_id()).decode()
PORT = 8888
SERVER = '192.168.178.60'
ACK = -1


async def run(loop):
    s = network.WLAN()
    print('Waiting for WiFi')  # ESP8266 with stored connection
    while not s.isconnected():
        await asyncio.sleep_ms(200)
    print('WiFi OK')
    sock = socket.socket()
    try:
        serv = socket.getaddrinfo(SERVER, PORT)[0][-1]  # server read
        # If server is down OSError e.args[0] = 111 ECONNREFUSED
        sock.connect(serv)
    except OSError:
        print('Connect fail.')
        return
    sock.setblocking(False)
    loop.create_task(reader(sock))
    loop.create_task(writer(sock))
    loop.create_task(simulate_async_delay())


async def simulate_async_delay():
    while True:
        await asyncio.sleep(0)
        time.sleep(0.05)  # 0.2 eventually get long delays


async def reader(sock):
    print('Reader start')
    ack = [ACK, 0, 'Ack from client {!s}.'.format(MY_ID)]
    last = -1
    lastack = -1
    while True:
        line = await readline(sock)
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


async def writer(sock):
    print('Writer start')
    data = [0, 0, 'Message from client {!s}.'.format(MY_ID)]
    while True:
        for _ in range(4):
            d = '{}\n'.format(json.dumps(data))
            await send(sock, d.encode('utf8'))
            data[1] += 1
        await asyncio.sleep_ms(1030)  # ???


async def readline(sock):
    line = b''
    while True:
        if line.endswith(b'\n'):
            return line.decode()
        d = sock.readline()
        if d == b'':
            raise OSError
        if d is not None:  # Something received
            line = b''.join((line, d))
        await asyncio.sleep(0)


async def send(sock, d):  # Write a line to socket.
    while d:
        try:
            ns = sock.send(d)
        except OSError as e:
            err = e.args[0]
            if err == errno.EAGAIN:  # Would block: try later
                print("EAGAIN send")
                await asyncio.sleep_ms(100)
        else:
            d = d[ns:]
            if d:  # Partial write: trim data and pause
                await asyncio.sleep_ms(20)


loop = asyncio.get_event_loop()
loop.create_task(run(loop))
loop.run_forever()
