# 0. IOT design for clients lacking a LAN interface

This uses an ESP8266 to provide a resilient "socket-like" link between a non
networked device (the client) and a server-side application. The ESP8266 runs a
fixed configuration. All client-side application code resides on the client.

Communication between the client and the ESP8266 uses I2C. The client must be
capable of running I2C slave mode. This includes STM boards such as the
Pyboard. In this doc the client device is referred to as the Pyboard.

 0. [IOT design for clients lacking a LAN interface](./README.md#0-iot-design-for-clients-lacking-a-lan-interface)  
 1. [Wiring](./README.md#1-wiring)  
 2. [Files](./README.md#2-files)  
 3. [Running the demo](./README.md#3-running-the-demo)  
 4. [The Pyboard application](./README.md#4-the-pyboard-application)  
  4.1 [Configuration](./README.md#41-configuration)  
  4.2 [Application design](./README.md#42-application-design)  
  4.3 [Special messages](./README.md#43-special-messages)  
  4.4 [The AppBase class](./README.md#44-the-appbase-class)  
 5. [ESP8266 crash detection](./README.md#5-esp8266-crash-detection)  
 6. [Quality of service](./README.md#6-quality-of-service)  

# 1. Wiring

ESP8266 pin numbers are GPIO pins as used on the reference board. WeMos have
their own numbering scheme.

| Pyboard | ESP8266 | Notes    | WeMos pins |
|:-------:|:-------:|:--------:|:-----------|
|  gnd    |  gnd    |          |  gnd |
|  X9     |  0      | I2C scl  |  D3 |
|  X10    |  2      | I2C sda  |  D4 |
|  X11    |  5      | syn      |  D1 |
|  X12    |  rst    | reset    |  reset |
|  Y8     |  4      | ack      |  D2 |

Pyboard pins may be altered at will. The chosen pins enable hard I2C to be used
but soft I2C with arbitrary pins also works. The `sync` and `ack` wires provide
synchronisation and the `reset` line enables the Pyboard to reset the ESP8266
if it crashes and fails to respond.

I2C requires the devices to be connected via short links and to share a common
ground. The `sda` and `scl` lines require pullup resistors. The chosen ESP8266
pins are equipped with pullups on most boards. If for some reason external
pullups are needed, a typical value is 4.7KΩ to 3.3V.

The I2C bus employed here cannot be shared with other devices.

A common power supply is usual but not essential.

# 2. Installation

A Python package is used so directory structures must be maintained.

#### On the Pyboard

These instructions assume an installation to the SD card. If installing to
flash, substitute `flash` for `sd` below.

On the Pyboard create a directory `/sd/micropython_iot`. Copy the following
files to this directory:
 1. `__init__.py`

Edit `micropython_iot/pb_link/config.py` to match local conditions, notably
server IP address and WiFi credentials. WiFi credentials may be empty strings
if the ESP8266 has been initialised with a WiFi connection.

Create the directory  `/sd/micropython_iot/pb_link` and copy the following
files from `micropython_iot/pb_link` to it:
 1. `app_base.py`
 2. `asi2c_i.py`
 3. `config.py`
 4. `pb_client.py`
 5. `aswitch.py`

Start by issuing
```python
import micropython_iot.pb_link.pb_client
```

#### On the ESP8266

One option is to install the precompiled build. **TODO forthcoming**

Alternatively create a directory `/pyboard/micropython_iot`. Copy the following
files to this directory:
 1. `__init__.py`
 2. `client.mpy`

Copy the directory `esp_link` with its contents to `/pyboard/micropython_iot`.

Edit `/pyboard/main.py` to read:
```python
from micropython_iot.esp_link import esp_link
```

### Dependency

`uasyncio` is required on Pyboard and ESP8266. See the notes on this in the
[main readme](../README.md#31-installation).

# 3. Running the demo

Ensure `/sd/micropython_iot/pb_link/config.py` matches local conditions for
WiFi credentials and the server IP address.

On the server navigate to the parent directory of `micropython_iot` and run
```
python3 -m micropython_iot.pb_link.s_app
```

On the Pyboard run
```python
from micropython_iot.pb_link import pb_client
```

# 4. The Pyboard application

## 4.1 Configuration

There may be multiple Pyboards in a network, each using an ESP8266 with
identical firmware. The file `config.py` contains configuration details which
are common to all Pyboards which communicate with a single server. This will
require adapting for local conditions. The `config` list has the following
entries:

 0. Port (integer).
 1. Server IP (string).
 2. Server timeout in ms (int). Must == TIMEOUT in server's local.py.
 3. Send reports every N seconds (0: never) (`int`).
 4. SSID (str).
 5. Password (str).

If having a file with credential details is unacceptable an empty string ('')
may be used in the SSID and Password fields. In this case the ESP8266 will
attempt to connect to the WLAN which the device last used; if it fails there is
no means of recovery and the link will fail.

`config.py` also provides a `hardware` list. This contains `Pin` and `I2C `
details which may be changed. Pins are arbitrary and the I2C interface may be
changed, optionally to use soft I2C. The I2C interface may not be shared with
other devices. The `hardware` list comprises the following:

The `hardware` list comprises the following elements:
 0. `i2c`  The I2C interface object.
 1. `syn` A Pyboard `Pin` instance (synchronisation with ESP8266).
 2. `ack` Ditto.
 3. `rst` A reset tuple `(Pin, level, pulse_length)` where:
 `Pin` is the Pyboard Pin instance linked to ESP8266 reset.  
 `Level` is 0 for the ESP8266.  
 `pulse_length` A value of 200 is recommended (units are ms).

## 4.2 Application design

The code should create a class subclassed from `app_base.AppBase`. The base
class performs initialisation. When this is complete, a `.start` method is
called which the user class should implement.

Typically this will launch user coroutines and terminate, as in the demo.

The `AppBase` class has `.readline` and `.write` coroutines which comprise the
interface to the ESP8266 (and thence to the server). User coros communicate
thus:

```python
    line = await self.readline()  # Read a \n terminated line from server app
    await self.write(line)  # Write a line.
```
The `.write` method will append a newline to the line if not present. The line
should not contain internal newlines: it typically comprises a JSON encoded
Python object.

If the WiFi suffers an outage these methods may pause for the duration.

## 4.3 Special messages

The ESP8266 sends messages to the Pyboard in response to changes in server
status or under error conditions or because reports have been requested. These
trigger asynchronous bound methods which the user may override.

## 4.4 The AppBase class

Constructor args:
 1. `loop` The asyncio event loop.
 2. `conn_id` Connection ID. See below.
 3. `config` List retrieved from `config.py` as described above.
 4. `hardware` List retrieved from `config.py` defining the hardware interface.
 5. `verbose` Provide debug output.

Coroutines:
 1. `readline` Read a newline-terminated line from the server.
 2. `write` Write a line to the server. If a terminating newline is not present
 one will be supplied.
 3. `reboot` Physically reboot the ESP8266. The system will resynchronise and
 resume operation.

Synchronous methods:
 1. `close` Shuts down the Pyboard/ESP8266 interface.

Asynchronous bound methods. These may be overridden in derived classes:

 1. `bad_wifi` No args. Awaited if WiFi can't connect in 4 seconds from boot.
 2. `bad_server` No args. Awaited if server refuses an initial connection.
 3. `report` Regularly launched if reports are requested in the config.  It
 receives a 3-list as an arg: `[connect_count, report_no, mem_free]` which
 describes the ESP8266 status.
 4. `server_ok` Launched whenever the status of the link to the server changes,
 by a WiFi server outage starting or ending. Receives a single boolean arg `up`
 being the new status.

Behaviour of the `AppBase` default bound methods: the first two raise 
`OSError`s with a suitable message, the others print a message and quit.

If a WiFi or server outage occurs, `readline` and `write` coroutines will pause
for the duration.

The `conn_id` constructor arg defines the connection ID used by the server-side
application, ensuring that the Pyboard app communicates with its matching
server app. ID's must be newline-terminated and must not include other newlines.

Subclasses must define a synchronous `start` bound method. This takes no args.
Typically it launches user coroutines.

# 5. ESP8266 crash detection

The design of the ESP8266 communication link with the server is resilient and
crashes should not occur. But power outages are always possible. If a reset
wire is in place there are two levels of crash recovery.

If I2C communication fails due to an ESP8266 reboot or power cycle, the
underlying
[asynchronous link](https://github.com/peterhinch/micropython-async/blob/master/i2c/README.md)
will reboot the ESP8266 and re-synchronise without the need for explicit code.
This caters for the bulk of potential failures and can be verified by pressing
the ESP8266 reset button while the application is running.

The `esp_link.py` driver sends periodic keepalives to the Pyboard. The
`AppBase` pyboard client reboots the ESP8266 if these stop being received. This
cab be verified with a serial connection to the ESP8266 and issuing `ctrl-c`.

# 6. Quality of service

In MQTT parlance the Pyboard link operates at qos==2: messages will be delivered
exactly once. In rare circumstances immediately after an outage, messages may be
delivered out of order.

Note that if the ESP8266 actually crashes all bets are off. The system will
recover but message loss may occur. Two observations:
 1. In my extensive testing crashes only occurred owing to electrical causes
 (a poor quality power supply).
 2. Guaranteed qos > 0 may be achieved at application level using response
 messages. When designing such a system bear in mind that response messages are
 themselves subject to non-guaranteed delivery.
