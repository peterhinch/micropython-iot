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
import uio


class Lock:
    def __init__(self, delay_ms=0):
        self._locked = False
        self.delay_ms = delay_ms

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
                await asyncio.sleep_ms(self.delay_ms)
            else:
                self._locked = True
                break

    def release(self):
        if not self._locked:
            raise RuntimeError('Attempt to release a lock which has not been set')
        self._locked = False


# Create message ID's. Initially 0 then 1 2 ... 254 255 1 2
def gmid():
    mid = 0
    while True:
        yield mid
        mid = (mid + 1) & 0xff
        mid = mid if mid else 1


getmid = gmid()
lock = Lock(20)

PORT = 8888
SERVER = '192.168.178.60'


def connect():
    sock = socket.socket()
    try:
        serv = socket.getaddrinfo(SERVER, PORT)[0][-1]  # server read
        # If server is down OSError e.args[0] = 111 ECONNREFUSED
        sock.connect(serv)
    except OSError:
        print('Connect fail.')
        return
    return sock


async def run(loop):
    s = network.WLAN()
    print('Waiting for WiFi')  # ESP8266 with stored connection
    while not s.isconnected():
        await asyncio.sleep_ms(200)
    print('WiFi OK')
    loop.create_task(simulate_async_delay())
    while True:
        sock = connect()
        if sock is None:
            time.sleep(1)
            print("Got no socket")
            continue
        wr = writer(sock)
        try:
            sock.setblocking(False)
            loop.create_task(wr)
            await reader(sock)
        except OSError as e:
            print("OSError", e)
        finally:
            asyncio.cancel(wr)
            sock.close()


async def simulate_async_delay():
    while True:
        await asyncio.sleep(0)
        time.sleep(0.05)  # 0.2 eventually get long delays


async def reader(sock):
    print('Reader start')
    last = -1
    while True:
        line = await readline(sock)
        if line == "\n":
            continue
        print("Got", line)
        message = uio.StringIO(line)
        preheader = bytearray(ubinascii.unhexlify(message.read(10)))
        mid = preheader[0]
        if preheader[4] & 0x2C == 0x2C:  # ACK
            print("Got ack mid", mid)
            continue  # All done
        if preheader[4] & 0x01 == 1:  # qos==True, send ACK even if dupe
            await sendack(sock, mid)
        try:
            data = json.load(message)
        except Exception:
            data = message.read()
        finally:
            message.close()
            del message
            gc.collect()
        print('Got', data)
        if data[1] == last:
            print("Dumped dupe", last)
            continue
        if last >= 0 and data[1] - last - 1:
            raise OSError('Missed message')
        last = data[1]


async def sendack(sock, mid):
    preheader = bytearray(5)
    preheader[0] = mid
    preheader[1] = preheader[2] = preheader[3] = 0
    preheader[4] = 0x2C  # ACK
    preheader = "{}\n".format(ubinascii.hexlify(preheader).decode())
    await send(sock, preheader)


async def writer(sock):
    print('Writer start')
    """
    import machine
    MY_ID = ubinascii.hexlify(machine.unique_id()).decode()
    preheader = bytearray(5)
    preheader[0] = 0x2C
    preheader[1] = 0
    preheader[2] = len(MY_ID) & 0xFF
    preheader[3] = (len(MY_ID) >> 8) & 0xFF  # allows for 65535 message length
    preheader[4] = 0xFF  # clean connection, shows if device has been reset or just a wifi outage
    preheader = ubinascii.hexlify(preheader)
    await send(sock, preheader)
    await send(sock, MY_ID)
    await send(sock, "\n")
    """
    data = [0, 0, 'Message from client.']
    while True:
        for _ in range(4):
            async with lock:
                mid = next(getmid)
                d = json.dumps(data)
                preheader = bytearray(5)
                preheader[0] = mid
                preheader[1] = 0
                preheader[2] = (len(d) & 0xFF) - (1 if d.endswith(b"\n") else 0)
                preheader[3] = (len(d) >> 8) & 0xFF  # allows for 65535 message length
                preheader[4] = 0  # special internal usages, e.g. for esp_link or ACKs
                preheader[4] |= 0x01  # qos==True, request ACK
                preheader = ubinascii.hexlify(preheader)
                message = preheader.decode() + d + "\n"
                await send(sock, message)
                # await send(sock, preheader)
                # await send(sock, d)
                # await send(sock, "\n")
                data[0] += 1
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
