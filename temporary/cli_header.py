# cli.py Test of socket. Run on Pyboard D

import gc

gc.collect()
import usocket as socket
import uasyncio as asyncio
import ujson as json
import errno

gc.collect()
import network
import ubinascii
import machine
import uio

MY_ID = ubinascii.hexlify(machine.unique_id()).decode()
PORT = 8888
SERVER = '192.168.178.60'
ACK = -1


# Create message ID's. Initially 0 then 1 2 ... 254 255 1 2
def gmid():
    mid = 0
    while True:
        yield mid
        mid = (mid + 1) & 0xff
        mid = mid if mid else 1


getmid = gmid()


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


async def reader(sock):
    try:
        print('Reader start')
        last = -1
        while True:
            line = await readline(sock)
            message = uio.StringIO(line)
            preheader = bytearray(ubinascii.unhexlify(message.read(10)))
            try:
                data = json.load(message)
            except Exception:
                data = message.read()
            finally:
                message.close()
                del message
            mid = preheader[0]
            print('Got', data)
            if last >= 0 and data[0] - last - 1:
                raise OSError('Missed message')
            last = data[0]
    except Exception as e:
        raise e
    finally:
        print("Reader stopped")
        try:
            print("Closing socket")
            sock.close()
        except:
            pass


async def writer(sock):
    print('Writer start')
    data = [0, 'Message from client {!s}.'.format(MY_ID)]
    try:
        while True:
            mid = next(getmid)
            d = json.dumps(data)
            preheader = bytearray(5)
            preheader[0] = mid
            preheader[1] = 0
            preheader[2] = (len(d) & 0xFF) - (1 if d.endswith(b"\n") else 0)
            preheader[3] = (len(d) >> 8) & 0xFF  # allows for 65535 message length
            preheader[4] = 0  # special internal usages, e.g. for esp_link or ACKs
            preheader = ubinascii.hexlify(preheader).decode()
            d = '{}{}\n'.format(preheader, d)
            await send(sock, d.encode('utf8'))
            data[0] += 1
            await asyncio.sleep_ms(253)  # ???
    except Exception as e:
        raise e
    finally:
        print("Writer stopped")


async def readline(sock):
    line = b''
    while True:
        if line.endswith(b'\n'):
            return line.decode()
        d = sock.readline()
        if d == b'':
            print("Connection closed")
            raise OSError
        if d is not None:  # Something received
            line = b''.join((line, d))
        await asyncio.sleep(0)


async def send(sock, d):  # Write a line to socket.
    print("Sending", d)
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
