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
    import ubinascii as binascii
    import uio as io
else:
    import socket
    import asyncio
    import select
    import errno
    import json
    import time
    import io
    import binascii
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
            m += "   "
        log.debug(m)

PORT = 8888


# Create message ID's. Initially 0 then 1 2 ... 254 255 1 2
def gmid():
    mid = 0
    while True:
        yield mid
        mid = (mid + 1) & 0xff
        mid = mid if mid else 1


getmid = gmid()

data_s = None


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
    global data_s
    print('Reader start')
    istr = ''
    last = -1
    try:
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
                if data_s is not None:
                    await send(sock, data_s)
                    print('sent', data_s)
                    data_s = None
                istr += d.decode()  # Add to any partial message
                # Strings from this point
                l = istr.split('\n')
                istr = l.pop()  # '' unless partial line
                for line in l:
                    message = io.StringIO(line)
                    try:
                        preheader = bytearray(binascii.unhexlify(message.read(10)))
                    except Exception as e:
                        print("Error reading preheader", e)
                        continue
                    try:
                        data = json.load(message)
                    except Exception:
                        data = message.read()
                    finally:
                        message.close()
                        del message
                    print('Got', preheader, data)
                    if last >= 0 and data[0] - last - 1:
                        raise OSError('Missed message')
                    last = data[0]
    except Exception as e:
        print(e)
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
    global data_s
    data = [0, 'Message from server.']
    try:
        while True:
            while data_s is not None:
                await asyncio.sleep(0.02)
            d = ''
            mid = next(getmid)
            d = json.dumps(data)
            preheader = bytearray(5)
            preheader[0] = mid
            preheader[1] = 0
            preheader[2] = (len(d) & 0xFF)
            preheader[3] = (len(d) >> 8) & 0xFF  # allows for 65535 message length
            preheader[4] = 0  # special internal usages, e.g. for esp_link or ACKs
            preheader[4] |= 0x01  # qos==True, request ACK
            preheader = binascii.hexlify(preheader)
            message = preheader.decode() + d + "\n"
            # await send(sock, message.encode('utf8'))
            # print('sent', message)
            data_s = message.encode('utf8')
            data[0] += 1
            # await asyncio.sleep(5)  # ???
    except Exception as e:
        raise e
    finally:
        print("Writer stopped")


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
loop.create_task(run(loop))
loop.run_forever()
