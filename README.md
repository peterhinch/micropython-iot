Notes on Unix version.

# Introduction

This library provides a resilient full duplex communication link between a WiFi
connected board and a server on the wired LAN. The board may be an ESP8266,
ESP32 or other target including the Pyboard D. The design is such that the code
can run for indefinite periods. Temporary WiFi or server outages are tolerated
without message loss.

The API is simple and consistent between client and server applications,
comprising `write` and `readline` methods. The `ujson` library enables various
Python objects to be exchanged. Guaranteed message delivery is available.

This project is a collaboration between Peter Hinch and Kevin Köck.

# 0. MicroPython IOT application design

IOT (Internet of Things) systems commonly comprise a set of endpoints on a WiFi
network. Internet access is provided by an access point (AP) linked to a
router. Endpoints run an internet protocol such as MQTT or HTTP and normally
run continuously. They may be located in places which are hard to access:
reliability is therefore paramount. Security is also a factor for endpoints
exposed to the internet.

Under MicroPython the available hardware for endpoints is limited. Testing has
been done on the ESP8266, ESP32 and the Pyboard D.

The ESP8266 remains as a readily available inexpensive device which, with care,
is capable of long term reliable operation. It does suffer from limited
resources, in particular RAM. Achieving resilient operation in the face of WiFi
or server outages is not straightforward: see
[this document](https://github.com/peterhinch/micropython-samples/tree/master/resilient).
The approach advocated here simplifies writing robust ESP8266 IOT applications
by providing a communications channel with inherent resilience.

The usual arrangement for MicroPython internet access is as below.
![Image](./images/block_diagram_orig.png)

Running internet protocols on ESP8266 nodes has the following drawbacks:
 1. It can be difficult to ensure resilience in the face of outages of WiFi and
 of the remote endpoint.
 2. Running TLS on the ESP8266 is demanding in terms of resources: establishing
 a connection can take 30s.
 3. There are potential security issues for internet-facing nodes.
 4. The security issue creates a requirement periodically to install patches to
 firmware or to libraries. This raises the issue of physical access.
 5. Internet applications can be demanding of RAM.

This document proposes an approach where multiple remote nodes communicate with
a local server. This runs CPython or MicroPython code and supports the internet
protocol required by the application. The server and the remote nodes
communicate using a simple protocol based on the exchange of lines of text. The
server can run on a Linux box such as a Raspberry Pi; this can run 24/7 at
minimal running cost.

![Image](./images/block_diagram.png)

Benefits are:
 1. Security is handled on a device with an OS. Updates are easily accomplished.
 2. The text-based protocol minimises the attack surface presented by nodes.
 3. The protocol is resilient in the face of outages of WiFi and of the server:
 barring errors in the application design, crash-free 24/7 operation is a
 realistic prospect.
 4. The amount of code running on the remote is smaller than that required to
 run a resilient internet protocol such as [this MQTT version](https://github.com/peterhinch/micropython-mqtt.git).
 5. The server side application runs on a relatively powerful machine. Even
 minimal hardware such as a Raspberry Pi has the horsepower easily to support
 TLS and to maintain concurrent links to multiple client nodes. Use of
 threading is feasible.
 6. The option to use CPython on the server side enables access to the full
 suite of Python libraries including internet modules.

The principal drawback is that in addition to application code on the ESP8266
node, application code is also required on the PC to provide the "glue" linking
the internet protocol with each of the client nodes. In many applications this
code may be minimal.

There are use-cases where conectivity is entirely local, for example logging
locally acquired data or using some nodes to control and monitor others. In
such cases no internet protocol is required and the server side application
merely passes data between nodes and/or logs data to disk.

This architecture can be extended to non-networked clients such as the Pyboard
V1.x. This is described and diagrammed [here](./README.md#9-extension-to-the-pyboard).

# 1. Contents

This repo comprises code for resilent full-duplex connections between a server
application and multiple clients. Each connection is like a simplified socket,
but one which persists through outages and offers guaranteed message delivery.

 0. [MicroPython IOT application design](./README.md#0-microPython-iot-application-design)  
 1. [Contents](./README.md#1-contents)  
 2. [Design](./README.md#2-design)  
  2.1 [Protocol](./README.md#21-protocol)  
 3. [Files and packages](./README.md#3-files-and-packages)  
  3.1 [Installation](./README.md#31-installation)  
  3.2 [Usage](./README.md#32-usage)
 4. [Client side applications](./README.md#4-client-side-applications)  
  4.1 [The Client class](./README.md#41-the-client-class)  
   4.1.1 [Initial Behaviour](./README.md#411-initial-behaviour)  
   4.1.2 [Watchdog Timer](./README.md#412-watchdog-timer)  
 5. [Server side applications](./README.md#5-server-side-applications)  
  5.1 [The server module](./README.md#51-the-server-module)  
 6. [Ensuring resilience](./README.md#6-ensuring-resilience) Guidelines for application design.   
 7. [Quality of service](./README.md#7-quality-of-service) Guaranteeing message delivery.  
  7.1 [The qos argument](./README.md#71-the-qos-argument)  
  7.2 [The wait argument](./README.md#71-the-wait-argument) Concurrent writes of qos messages.  
 8. [Performance](./README.md#8-performance)  
  8.1 [Latency and throughput](./README.md#81-latency-and-throughput)  
  8.2 [Client RAM utilisation](./README.md#82-client-ram-utilisation)  
 9. [Extension to the Pyboard](./README.md#9-extension-to-the-pyboard)  
 10. [How it works](./README.md#10-how-it-works)  
  10.1 [Interface and client module](./README.md#101-interface-and-client-module)  
  10.2 [Server module](./README.md#102-server-module)  

# 2. Design

The code is asynchronous and based on `asyncio`. Client applications on the
remote import `client.py` which provides the interface to the link. The server
side application uses `server.py`.

Messages are required to be complete lines of text. They typically comprise an
arbitrary Python object encoded using JSON. The newline character ('\n') is not
allowed within a message but is optional as the final character.

Guaranteed message delivery is supported. This is described in
[section 7](./README.md#7-quality-of-service). Performance limitations are
discussed in [section 8](./README.md#8-performance).

## 2.1 Protocol

Client and server applications use `readline` and `write` methods to
communicate: in the case of an outage of WiFi or the connected endpoint, the
method will pause until the outage ends. While the system is tolerant of
runtime server and WiFi outages, this does not apply on initialisation. The
server must accessible before clients are started.

The link status is determined by periodic exchanges of keepalive messages. This
is transparent to the application. If a keepalive is not received within a user
specified timeout an outage is declared. On the client the WiFi is disconnected
and a reconnection procedure is initiated. On the server the connection is
closed and it awaits a new connection.

Each client has a unique ID which is an arbitrary string. In the demo programs
this is stored in `local.py`. The ID enables the server application to
determine which physical client is associated with an incoming connection.

###### [Contents](./README.md#1-contents)

# 3. Files and packages

This repo has been updated for `uasyncio` V3. This is incorporated in daily
builds of firmware and will be available in release builds later than V1.12.
Server code may be run under CPython V3.8 or above. It may be run under
MicroPython (Unix build), but at the time of writing this requires
[this fix](https://github.com/micropython/micropython/issues/6109#issuecomment-639376529)
to incorporate `uasyncio`.

Directory `iot`:
 1. `client.py` / `client.mpy` Client module. The ESP8266 has insufficient RAM
 to compile `client.py` so the precompiled `client.mpy` should be used. See
 note below.
 2. `server.py` Server module. (runs under CPython 3.5+ or MicroPython 1.10+).
Directory `iot/primitives`:
 1. `__init__.py` Functions common to `Client` and `Server`.
 2. `switch.py` Debounced switch interface. Used by `remote` demo.
Optional directories containing Python packages:
 1. `iot/examples` A simple example. Up to four clients communicate with a
 single server instance.
 2. `iot/remote` Demo uses the library to enable one client to control another.
 This may need adapting for your hardware.
 3. `iot/qos` Demonstrates and tests the qos (quality of service) feature, see
 [Quality of service](./README.md#7-quality-of-service).
 4. `iot/pb1` Contians packages enabling a Pyboard V1.x to communicate with the
 server via an ESP8266 connected by I2C. See [documentation](./pb_link/README.md).

NOTE: The file `client.mpy` works with daily builds at the time of writing. The
bytecode format changes occasionally. If an application throws a bytecode error
it is necessary to cross-compile `client.py` with the associated version of
`mpy-cross`. Or raise an issue and I will post an update.

## 3.1 Installation

This section describes the installation of the library and the demos. The
ESP8266 has limited RAM: there are specific recommendations for installation on
that platform.

#### Existing users

It is recommended to remove the old version and re-install as below.

There have been API changes to accommodate the new `uasyncio` version: the
event loop argument is no longer required or accepted in `Client` and `Server`
constructors. The directory structure has changed, requiring minor changes to
`import` statements.

#### Firmware/Dependency

On ESP8266, RAM can be saved by building firmware from source, freezing
`client.py` as bytecode. If this is not done, it is necessary to
[cross compile](https://github.com/micropython/micropython/tree/master/mpy-cross)
`client.py`. The file `client.mpy` is provided for those unable to do this. If
freezing, create an `iot` directory in your modules directory and copy
`iot/client.py` and the directory `iot/primitives` and contents there.

Pre-requisites: firmware must be a current daily build or a release build after
V1.12. If upgrading, particularly on an ESP8266, it is wise to erase flash
prior to installtion. In particular this will ensure the use of littlefs with
its associated RAM saving.

This repository is a python package, consequently on the client the directory
structure must be retained. The following installs all demos on the target.

On your PC move to a directory of your choice and clone the repository there:
```
git clone https://github.com/peterhinch/micropython-iot
```
Installation consists of copying the `iot` directory and contents to an `iot` 
directory on the boot device. On ESP8266 or ESP32 the boot device is`/pyboard`.
On the Pyboard D it will be `/flash` or `/sd` depending on whether an SD card
is fitted.

Copying may be done using any tool but I recommend
[rshell](https://github.com/dhylands/rshell). If this is used start in the
directory on your PC containing the clone, start `rshell` and issue (adapting
the boot device for your platform):
```
cp -r iot /pyboard/iot/
```
On ESP8266, unless frozen, it is necessary to delete `client.py` to force the
use of `client.mpy`:
```
rm /pyboard/iot/client.py
```

#### Preconditions for demos

The demo programs store client configuration data in a file `local.py`. Each
demo has its own `local.py` located in the directory of the demo code. This
contains the following constants which should be edited to match local
conditions. Remove the `use_my_local` hack designed for my WiFi privacy.:

```python
MY_ID = '1'  # Client-unique string.
SERVER = '192.168.0.10'  # Server IP address.
SSID = 'use_my_local'  # Insert your WiFi credentials
PW = 'PASSWORD'
PORT = 8123
TIMEOUT = 2000
# The following may be deleted
if SSID == 'use_my_local':
    from iot.examples.my_local import *
```

The ESP8266 can store WiFi credentials in flash memory. If desired, ESP8266
clients can be initialised to connect to the local network prior to running
the demos. In this case the SSID and PW variables may optionally be empty
strings (`SSID = ''`).

Note that the server-side examples below specify `python3` in the run command.
In every case `micropython` may be substituted to run under the Unix build of
MicroPython.

## 3.2 Usage

#### The main demo

This illustrates up to four clients communicating with the server. The demo
expects the clients to have ID's in the range 1 to 4: if using multiple clients
edit each one's `local.py` accordingly.

On the server navigate to the parent directory of `iot` and run:
```
python3 -m iot.examples.s_app_cp
```
or
```
micropython -m iot.examples.s_app_cp
```
On each client run:
```
import iot.examples.c_app
```

#### The remote control demo

This shows one ESP8266 controlling another. The transmitter should have a
pushbutton between GPIO 0 and gnd, both should have an LED on GPIO 2.

On the server navigate to the parent directory of `iot` and run:
```
python3 -m iot.remote.s_comms_cp
```
or
```
micropython -m iot.remote.s_comms_cp
```

On the esp8266 run (on transmitter and receiver respectively):

```
import iot.remote.c_comms_tx
import iot.remote.c_comms_rx
```

#### The standard qos (Quality of service) demo

This test program verifies that each message (in each direction) is received
exactly once. On the server navigate to the parent directory of `iot` and run:
```
python3 -m iot.qos.s_qos_cp
```
or
```
micropython -m iot.qos.s_qos_cp
```
On the client, after editing `/pyboard/qos/local.py`, run:
```
import iot.qos.c_qos
```

#### The fast qos demo

This tests the option of concurrent `qos` writes. This is an advanced feature
discussed in [section 7.1](./README.md#71-the-wait-argument). To run the demo,
on the server navigate to the parent directory of `iot` and run:
```
python3 -m iot.qos.s_qos_fast
```
or
```
micropython -m iot.qos.s_qos_fast
```
On the client, after editing `/pyboard/qos/local.py`, run:
```
import iot.qos.c_qos_fast
```

#### Troubleshooting the demos

If `local.py` specifies an SSID, on startup the demo programs will pause
indefinitely if unable to connect to the WiFi. If `SSID` is an empty string the
assumption is an ESP8266 with stored credentials; if this fails to connect an
`OSError` will be thrown. An `OSError` will also be thrown if initial
connectivity with the server cannot be established.

###### [Contents](./README.md#1-contents)

# 4. Client side applications

A client-side application instantiates a `Client` and launches a coroutine
which awaits it. After the pause the `Client` has connected to the server and
communication can begin. This is done using `Client.write` and
`Client.readline` methods.

Every client ha a unique ID (`MY_ID`) typically stored in `local.py`. The ID
comprises a string subject to the same constraint as messages:

Messages comprise a single line of text; if the line is not terminated with a
newline ('\n') the client library will append it. Newlines are only allowed as
the last character. Blank lines will be ignored.

A basic client-side application has this form:
```python
import uasyncio as asyncio
import ujson
from iot import client
import local  # or however you configure your project


class App:
    def __init__(self, verbose):
        self.cl = client.Client(local.MY_ID, local.SERVER,
                                local.PORT, local.SSID, local.PW,
                                local.TIMEOUT, conn_cb=self.state, 
                                verbose=verbose)
        asyncio.create_task(self.start())

    async def start(self):
        await self.cl  # Wait until client has connected to server
        asyncio.create_task(self.reader())
        await self.writer()  # Wait forever

    def state(self, state):  # Callback for change in connection status
        print("Connection state:", state)

    async def reader(self):
        while True:
            line = await self.cl.readline()  # Wait until data received
            data = ujson.loads(line)
            print('Got', data, 'from server app')

    async def writer(self):
        data = [0, 0]
        count = 0
        while True:
            data[0] = count
            count += 1
            print('Sent', data, 'to server app\n')
            await self.cl.write(ujson.dumps(data))
            await asyncio.sleep(5)

    def close(self):
        self.cl.close()

app = None
async def main():
    global app  # For closure by finally clause
    app = App(True)
    await app.start()  # Wait forever

try:
    asyncio.run(main())
finally:
    app.close()  # Ensure proper shutdown e.g. on ctrl-C
    asyncio.new_event_loop()
```
If an outage of server or WiFi occurs, the `write` and `readline` methods will
pause until connectivity has been restored. The server side API is similar.

###### [Contents](./README.md#1-contents)

## 4.1 The Client class

The constructor has a substantial number of configuration options but in many
cases defaults may be accepted for all but the first five.

Constructor args:
 1. `my_id` The client id.
 2. `server` The server IP-Adress to connect to.
 3. `port=8123` The port the server listens on.
 4. `ssid=''` WiFi SSID. May be blank for ESP82666 with credentials in flash.
 5. `pw=''` WiFi password. 
 6. `timeout=2000` Connection timeout in ms. If a connection is unresponsive
 for longer than this period an outage is assumed.
 7. `conn_cb=None` Callback or coroutine that is called whenever the connection
 changes.
 8. `conn_cb_args=None` Arguments that will be passed to the *connected_cb*
 callback. The callback will get these args preceeded by a `bool` indicating
 the new connection state.
 9. `verbose=False` Provides optional debug output.
 10. `led=None` If a `Pin` instance is passed it will be toggled each time a
 keepalive message is received. Can provide a heartbeat LED if connectivity is
 present. On Pyboard D a `Pin` or `LED` instance may be passed.
 10. `wdog=False` If `True` a watchdog timer is created with a timeout of 20s.
 This will reboot the board if it crashes - the assumption is that the
 application will be restarted via `main.py`.

Methods (asynchronous):
 1. `readline` No args. Pauses until data received. Returns a line.
 2. `write` Args: `buf`, `qos=True`, `wait=True`. `buf` holds a line of text.  
 If `qos` is set, the system guarantees delivery. If it is clear messages may
 (rarely) be lost in the event of an outage.__
 The `wait` arg determines the behaviour when multiple concurrent writes are
 launched with `qos` set. See [Quality of service](./README.md#7-quality-of-service).

The following asynchronous methods are described in Initial Behaviour below. In
most cases they can be ignored.
 3. `bad_wifi`
 4. `bad_server`

Methods (synchronous):
 1. `status` Returns `True` if connectivity is present. May also be read using
 function call syntax (via `__call__`).
 2. `close` Closes the socket. Should be called in the event of an exception
 such as a `ctrl-c` interrupt. Also cancels the WDT in the case of a software
 WDT.

Bound variable:
 1. `connects` The number of times the `Client` instance has connected to WiFi.
 This is maintained for information only and provides some feedback on the
 reliability of the WiFi radio link.

The `Client` class is awaitable. If
```python
await client_instance
```
is issued, the coroutine will pause until connectivity is (re)established.

Applications which always `await` the `write` method do not need to check or
await the client status: `write` will pause until it can complete. If `write`
is launched using `create_task` it is essential to check status otherwise
during an outage unlimited numbers of coroutines will be created.

The client buffers up to 20 incoming messages. To avoid excessive queue growth
applications should have a single coroutine which spends most of its time
awaiting incoming data.

###### [Contents](./README.md#1-contents)

### 4.1.1 Initial Behaviour

When an application instantiates a `Client` it attemps to connect to WiFi and
then to the server. Initial connection is handled by the following `Client`
asynchronous bound methods (which may be modified by subclassing):

 1. `bad_wifi` No args.
 2. `bad_server` No args. Awaited if server refuses an initial connection.

Note that, once a server link has been initially established, these methods
will not be called: reconnection after outages of WiFi or server are automatic.

The `bad_wifi` coro attempts to connect using the WiFi credentials passed to
the constructor. This will pause until a connection has been achieved. The
`bad_server` coro raises an `OSError`. Behaviour of either of these may be
modified by subclassing.

Platforms other than ESP8266 launch `bad_wifi` unconditionally on startup. In
the case of an ESP8266 which has WiFi credentials stored in flash it will first
attempt to connect using that data, only launching `bad_wifi` if this fails in
a timeout period. This is to minimise flash wear.

### 4.1.2 Watchdog Timer

This option provides a last-ditch protection mechanism to keep a client running
in the event of a crash. The ESP8266 can (rarely) crash, usually as a result of
external electrical disturbance. The WDT detects that the `Client` code is no
longer running and issues a hard reset. Note that this implies a loss of
program state. It also assumes that `main.py` contains a line of code which
will restart the application.

Debugging code with a WDT can be difficult because bugs or software interrupts
will trigger unexpected resets. It is recommended not to enable this option
until the code is stable.

On the ESP8266 the WDT uses a sofware timer: it can be cancelled which
simplifies debugging. See `examples/c_app.py` for the use of the `close` method
in a `finally` clause.

The WDT on the Pyboard D is a hardware implementation: it cannot be cancelled.
It may be necessary to use safe boot to bypass `main.py` to access the code.

###### [Contents](./README.md#1-contents)

# 5. Server side applications

A typical example has an `App` class with one instance per physical client
device. This enables instances to share data via class variables. Each instance
launches a coroutine which acquires a `Connection` instance for its individual
client (specified by its client_id). This process will pause until the client
has connected with the server. Communication is then done using the `readline`
and `write` methods of the `Connection` instance.

Messages comprise a single line of text; if the line is not terminated with a
newline (`\n`) the server library will append it. Newlines are only allowed as
the last character. Blank lines will be ignored.

A basic server-side application has this form:
```python
import asyncio
import json
from iot import server
import local  # or however you want to configure your project

class App:
    def __init__(self, client_id):
        self.client_id = client_id  # This instance talks to this client
        self.conn = None  # Will be Connection instance
        self.data = [0, 0, 0]  # Exchange a 3-list with remote
        asyncio.create_task(self.start())

    async def start(self):
        # await connection from the specific EP8266 client
        self.conn = await server.client_conn(self.client_id)
        asyncio.create_task(self.reader())
        asyncio.create_task(self.writer())

    async def reader(self):
        while True:
            # Next line will pause for client to send a message. In event of an
            # outage it will pause for its duration.
            line = await self.conn.readline()
            self.data = json.loads(line)
            print('Got', self.data, 'from remote', self.client_id)

    async def writer(self):
        count = 0
        while True:
            self.data[0] = count
            count += 1
            print('Sent', self.data, 'to remote', self.client_id, '\n')
            await self.conn.write(json.dumps(self.data))  # May pause in event of outage
            await asyncio.sleep(5)

async def main():
    clients = {1, 2, 3, 4}
    apps = [App(n) for n in clients]  # Accept 4 clients with ID's 1-4
    await server.run(clients, True, local.PORT, local.TIMEOUT)  # Verbose

def run():
    try:
        asyncio.run(main())
    except KeyboardInterrupt:  # Delete this if you want a traceback
        print('Interrupted')
    finally:
        server.Connection.close_all()
        asyncio.new_event_loop()

if __name__ == "__main__":
    run()
```

## 5.1 The server module

Server-side applications should create and run a `server.run` task. This runs
forever and takes the following args:
 1. `expected` A set of expected client ID strings.
 2. `verbose=False` If `True` output diagnostic messages.
 3. `port=8123` TCP/IP port for connection. Must match clients.
 4. `timeout=2000` Timeout for outage detection in ms. Must match the timeout
 of all `Client` instances.

The `expected` arg causes the server to produce a warning message if an
unexpected client connects, or if multiple clients have the same ID (this will
cause tears before bedtime).

The module is based on the `Connection` class. A `Connection` instance provides
a communication channel to a specific client. The `Connection` instance for a
given client is a singleton and is acquired by issuing
```python
conn = await server.client_conn(client_id)
```
This will pause until connectivity has been established. It can be issued at
any time: if the `Connection` has already been instantiated, that instance will
be returned. The `Connection` constructor should not be called by applications.

The `Connection` instance:

Methods (asynchronous):
 1. `readline` No args. Pauses until data received. Returns a line.
 2. `write` Args: `buf`, `qos=True`, `wait=True`. `buf` holds a line of text.  
 If `qos` is set, the system guarantees delivery. If it is clear messages may
 (rarely) be lost in the event of an outage.__
 The `wait` arg determines the behaviour when multiple concurrent writes are
 launched with `qos` set. See [Quality of service](./README.md#7-quality-of-service).

Methods (synchronous):
 1. `status` Returns `True` if connectivity is present. The connection state
 may also be retrieved using function call syntax (via `.__call__`).
 2. `__getitem__` Enables the `Connection` of another client to be retrieved
 using list element access syntax. Will throw a `KeyError` if the client is
 unknown (has never connected).

Class Method (synchronous):
 1. `close_all` No args. Closes all sockets: call on exception (e.g. ctrl-c).

The `Connection` class is awaitable. If
```python
await connection_instance
```
is issued, the coroutine will pause until connectivity is (re)established.

Applications which always `await` the `write` method do not need to check or
await the server status: `write` will pause until it can complete. If `write`
is launched using `create_task` it is essential to check status otherwise
during an outage unlimited numbers of coroutines will be created.

The server buffers incoming messages but it is good practice to have a coro
which spends most of its time waiting for incoming data.

Server module coroutines:

 1. `run` Args: `expected` `verbose=False` `port=8123` `timeout=2000`
 This is the main coro and starts the system. 
 `expected` is a set containing the ID's of all clients.  
 `verbose` causes debug messages to be printed.  
 `port` is the port to listen to.  
 `timeout` is the number of ms that can pass without a keepalive until the 
  connection is considered dead.
 2. `client_conn` Arg: `client_id`. Pauses until the sepcified client has
 connected. Returns the `Connection` instance for that client.
 3. `wait_all` Args: `client_id=None` `peers=None`. See below.

The `wait_all` coroutine is intended for applications where clients communicate
with each other. Typical user code cannot proceed until a given set of clients
have established initial connectivity.

`wait_all`, where a `client_id` is specified, behaves as `client_conn` except
that it pauses until further clients have also connected. If a `client_id` is
passed it will returns that client's `Connection` instance. If `None` is passed
the  assumption is that the current client is already connected and the coro
returns `None`.

The `peers` argument defines which clients it must await: it must either be
`None` or a set of client ID's. If a set of `client_id` values is passed, it
pauses until all clients in the set have connected. If `None` is passed, it
pauses until all clients specified in `run`'s `expected` set have connected.

It is perhaps worth noting that the user application can impose a timeout on
this by means of `asyncio.wait_for`.

###### [Contents](./README.md#1-contents)

# 6. Ensuring resilience

There are two principal ways of provoking `LmacRxBlk` errors and crashes.
 1. Failing to close sockets when connectivity is lost.
 2. Feeding excessive amounts of data to a socket after connectivity is lost:
 this causes an overflow to an internal ESP8266 buffer.

These modules aim to address these issues transparently to application code,
however it is possible to write applications which violate 2.

There is a global `TIMEOUT` value defined in `local.py` which should be the
same for the server and all clients. Each end of the link sends a `keepalive`
(KA) packet (an empty line) at a rate guaranteed to ensure that at least one KA
will be received in every `TIMEOUT` period. If it is not, connectivity is
presumed lost and both ends of the interface adopt a recovery procedure.

If an application always `await`s a write with `qos==True` there is no risk of
Feeding excess data to a socket: this is because the coroutine does not return
until the remote endpoint has acknowledged reception.

On the other hand if multiple messages are sent within a timeout period with
`qos==False` there is a risk of buffer overflow in the event of an outage.

###### [Contents](./README.md#1-contents)

# 7. Quality of service

In the presence of a stable WiFi link TCP/IP should ensure that packets sent
are received intact. In the course of extensive testing with the ESP8266 we
found that (very rarely) packets were lost. It is not known whether this
behavior is specific to the ESP8266. Another mechanism for message loss is the
case where a message is sent in the interval between an outage occurring and it
being detected. This is likely to occur on all platforms.

The client and server modules avoid message loss by the use of acknowledge
packets: if a message is not acknowledged within a timeout period it is
retransmitted. This implies duplication where the acknowledge packet is lost.
Receive message de-duplication is employed to provide a guarantee that the
message will be delivered exactly once. While delivery is guaranteed,
timeliness is not. Messages are inevitably delayed for the duration of a WiFi
or server outage where the `write` coroutine will pause for the duration.

Guaranteed delivery involves a tradeoff against throughput and latency. This is
managed by optional arguments to `.write`, namely `qos=True` and `wait=True`.

## 7.1 The qos argument

Message integrity is determined by the `qos` argument. If `False` message
delivery is not guaranteed. A use-case for disabling `qos` is in applications
such as remote control. If the user presses a button and nothing happens they
would simply repeat the action. Such messages are always sent immediately: the
application should limit the rate at which they can be sent, particularly on
ESP8266 clients, to avoid risk of buffer overflow.

With `qos` set, the message will be delivered exactly once.

Where successive `qos` messages are sent there may be a latency issue. By
default the transmission of a `qos` message will be delayed until reception
of its predecessor's acknowledge. Consequently the `write` coroutine will
pause, introducing latency. This serves two purposes. Firstly it ensures that
messages are received in the order in which they were sent (see below).

Secondly consider the case where an outage has occurred but has not yet been
detected. The first message is written, but no acknowledge is received.
Subsequent messages are delayed, precluding the risk of ESP8266 buffer
overflows. The interface resumes operation after the outage has cleared.

## 7.2 The wait argument

This default can be changed with the `wait` argument to `write`. If `False` a
`qos` message will be sent immediately, even if acknowledge packets from
previous messages are pending. Applications should be designed to limit the
number of such `qos` messages sent in quick succession: on ESP8266 clients
buffer overflows can occur.

In testing in 2019 the ESP32 was not resilient under these circumstances; this
appears to have been fixed in current firmware builds. Nevertheless setting
`wait=False` potentially risks resilience. If used, applications should be
tested to verify quality of service in the presence of WiFi outages.

If messages are sent with `wait=False` there is a chance that they may not be
received in the order in which they were sent. As described above, in the event
of `qos` message loss, retransmission occurs after a timeout period has
elapsed. During that timeout period the application may have successfully sent
another non-waiting `qos` message resulting in out of order reception.

The demo programs `qos/c_qos_fast.py` (client) and `qos/s_qos_fast.py` issue
four `write` operations with `wait=False` in quick succession. This number is
probably near the maximum on an ESP8266. Note the need explicitly to check for
connectivity before issuing the `write`: this is to avoid spawning large
numbers of coroutines during an outage.

In summary specifying `wait=False` should be considered an "advanced" option
requiring testing to prove that resilence is maintained.

###### [Contents](./README.md#1-contents)

# 8. Performance

## 8.1 Latency and throughput

The interface is intended to provide low latency: if a switch on one node
controls a pin on another, a reasonably quick response can be expected. The
link is not designed for high throughput because of the buffer overflow issue
discussed in [section 6](./README.md#6-ensuring-resilence). This is essentially
a limitation of the ESP8266 device: more agressive use of the `wait` arg may be
possible on platforms such as the Pyboard D.

In practice latency on the order of 100-200ms is normal; if an outage occurs
latency will inevitably persist for the duration.

**TIMEOUT**

This defaults to 2s. On `Client` it is a constructor argument, on the server
it is an arg to `server.run`. Its value should be common to all clients and
the sever. It determines the time taken to detect an outage and the frequency
of `keepalive` packets. This time was chosen on the basis of measured latency
periods on WiFi networks. It may be increased at the expense of slower outage
detection. Reducing it may result in spurious timeouts with unnecessary WiFi
reconnections.

## 8.2 Client RAM utilisation

On ESP8266 with a current (June 2020) daily build the demo reports over 20KB
free. Free RAM of 25.9KB was achieved with compiled firmware with frozen 
bytecode as per [Installation](./README.md#31-installation).

###### [Contents](./README.md#1-contents)

# 9. Extension to the Pyboard

This extends the resilient link to MicroPython targets lacking a network
interface; for example the Pyboard V1.x. Connectivity is provided by an ESP8266
running a fixed firmware build: this needs no user code.

The interface between the Pyboard and the ESP8266 uses I2C and is based on the
[existing I2C module](https://github.com/peterhinch/micropython-async/tree/master/v3/as_drivers/i2c).

![Image](./images/block_diagram_pyboard.png)

Resilient behaviour includes automatic recovery from WiFi and server outages;
also from ESP8266 crashes.

See [documentation](./pb_link/README.md).

# 10. How it works

## 10.1 Interface and client module

The `client` module was designed on the expectation that client applications
will usually be simple: acquiring data from sensors and periodically sending it
to the server and/or receiving data from the server and using it to control
devices. Developers of such applications probably don't need to be concerned
with the operation of the module.

There are ways in which applications can interfere with the interface's
operation either by blocking or by attempting to operate at excessive data
rates. Such designs can produce an erroneous appearance of poor WiFi
connectivity.

Outages are detected by a timeout of the receive tasks at either end. Each peer
sends periodic `keepalive` messages consisting of a single newline character,
and each peer has a continuously running read task. If no message is received
in the timeout period (2s by default) an outage is declared.

From the client's perspective an outage may be of the WiFi or the server. In
practice WiFi outages are more common: server outages on a LAN are typically
caused by the developer testing new code. The client assumes a WiFi outage. It
disconnects from the network for long enough to ensure that the server detects
the outage. It then attempts repeatedly to reconnect. When it does so, it
checks that the connection is stable for a period (it might be near the limit
of WiFi range).

If this condition is met it attempts to reconnect to the server. If this
succeeds the client runs. Its status becomes `True` when it first receives data
from the server.

A client or server side application which blocks or hogs processor time can
prevent the timely transmission of `keepalive` messages. This will cause the
server to declare an outage: the consequence is a sequence of disconnect
and reconnect events even in the presence of a strong WiFi signal.

## 10.2 Server module

Server-side applications communicate via a `Connection` instance. This is
unique to a client. It is instantiated when a specified client first connects
and exists forever. During an outage its status becomes `False` for the
duration. The `Connection` instance is retrieved as follows, with the
`client_conn` method pausing until initial connectivity has been achieved:
```python
import server
# Class details omitted
    self.conn = await server.client_conn(self.client_id)
```
Each client must have a unique ID. When the server detects an incoming
connection on the port it reads the client ID from the client. If a
`Connection` instance exists for that ID its status is updated, otherwise a
`Connection` is instantiated.

The `Connection` has a continuously running coroutine `._read` which reads data
from the client. If an outage occurs it calls the `._close` method which closes
the socket, setting the bound variable `._sock` to `None`. This corresponds to
a `False` status. The `._read` method pauses until a new connection occurs. The
aim here is to read data from ESP8266 clients as soon as possible to minimise
risk of buffer overflows.

The `Connection` detects an outage by means of a timeout in the `._read`
method: if no data or `keepalive` is received in that period an outage is
declared, the socket is closed, and the `Connection` status becomes `False`.

The `Connection` has a `._keepalive` method. This regularly sends `keepalive`
messages to the client. Application code which blocks the scheduler can cause
this not to be scheduled in a timely fashion with the result that the client
declares an outage and disconnects. The consequence is a sequence of disconnect
and reconnect events even in the presence of a strong WiFi signal.
