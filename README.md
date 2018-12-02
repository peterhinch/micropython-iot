# MicroPython IOT application design

IOT (Internet of Things) systems commonly comprise a set of endpoints on a WiFi
network with a single access point (AP). This provides internet connectivity
via a router. Endpoints run an internet protocol such as MQTT or HTTP.
Endpoints normally run continously and may be located in places which are
hard to access. Reliability is paramount. Security is also important if nodes
access the internet: in this case SSL/TLS is necessary.

At the time of writing (November 2018) the Pyboard D is not yet available. The
ESP32 is incapable of 24/7 operation owing to [this issue](https://github.com/micropython/micropython-esp32/issues/167).
The ESP8266 remains as a readily available inexpensive device which, with care,
is capable of long term reliable operation. It does suffer from limited
resources, in particular RAM.

Running full-fat internet protocols on each ESP8266 nodes has the following
drawbacks:
 1. It can be difficult to ensure resilience in the face of outages of WiFi and
 of the remote endpoint. See [this document](https://github.com/peterhinch/micropython-samples/tree/master/resilient).
 2. Running TLS on the ESP8266 is demanding in terms of resources: establishing
 a connection can take 30s.
 3. There are potential security issues if a node has access to the internet.
 4. The security issue creates a requirement periodically to install patches to
 firmware or to libraries. This raises the issue of physical access.

This document proposes an alternative where the ESP8266 nodes communicate with
a local server. This runs CPython code and supports the internet protocol. The
server and the ESP8266 nodes communicate using a simple protocol based on the
exchange of lines of text. The server can run on a Linux box such as a
Raspberry Pi; this can run 24/7 at a minimal cost in power. Benefits are:
 1. Security is handled on a device with an OS. Updates are easily accomplished.
 2. The text-based protocol minimises the attack surface presented by nodes.
 3. The protocol has been proven to be resilient in the face of outages of WiFi
 and of the server: barring errors in the application design, crash-free 24/7
 operation is a realistic prospect.
 4. The amount of code running on the ESP8266 is smaller than that required to
 run a resilient internet protocol such as [this MQTT version](https://github.com/peterhinch/micropython-mqtt.git).
 5. The server side application runs under CPython on a relatively powerful
 device having access to the full suite of Python libraries. Therefore such
 code is well suited to running an internet protocol. Hardware may be expected
 to have the horsepower easily to support TLS, and to maintain concurrent links
 to multiple client nodes.

The principal drawback is that in addition to application code on the ESP8266
node, server side application code is required to provide the "glue" linking
the internet protocol with each of the client nodes. In many applications this
code may be minimal. There is also a use-case where conectivity is entirely
local, for example logging locally acquired data or using some nodes to
control and monitor others. In such cases no internet protocol is required and
the server side application merely passes data between nodes.
