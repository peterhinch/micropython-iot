# Improving Quality of Service

In MQTT parlance the link operates at qos==0: there is no guarantee of packet
delivery. If an outage is detected before transmission, the `write` will pause
until connectivity resumes. Packet loss will occur if, at the time when a
message is sent, an outage has occurred but has not yet been detected by the
sender. In this case the sender puts the packet into a socket whose peer is
lost.

The desired outcome is qos==2. In this case delivery is guaranteed, and there
is also a guarantee that every transmitted message will be received and
processed exactly once. Note that, in this context, the "guarantee" is
conditional on any outage eventually ending. Further there is no guarantee of
timeliness: if an outage lasts an hour messages will be delayed accordingly.

The conventional approach to guaranteed delivery is by sending acknowledge
packets, however some complexity results because the acknowledge packets
themselves are subject to uncertain delivery.

The characteristics of this interface enable a simpler solution, implemented in
client and server modules. Every message includes a hidden incrementing
`message_id` in the data. This enables the receiving coro to discard duplicate
messages; only unique messages are forwarded to the user. This is achieved as
follows.

After writing the data the sending coro launches another which pauses for the
timeout interval. It then checks whether the interface is up: if so, it quits.
If the interface is down, there is a chance that the message was not sent
(if the outage occurred before the data was actually sent). In that case the
message is retransmitted; this won't complete until the interface to the server
is re-established.

Consider the following cases:
 1. Outage occurs and is detected before the initial write. The write waits
 until the outage is cleared and proceeds successfully. On completion the
 interface is good and no retransmission will occur.
 2. Outage precedes initial write but is not yet detected. The initial write
 puts data into a socket whose peer is lost. The delay ensures that, by the
 time the interface is tested, the outage will have been detected.
 Retransmission  occurs and the message is received once.
 3. Initial write succeeds. Outage occurs during the delay period. In this
 case the message is sent again (delayed as above). The recipient rejects it
 because the `message_id` is that of the previous message.

The scheme has one snag. If multiple coros are writing and an outage occurs,
it is possible for multiple coros to be waiting to retransmit when the outage
clears. In this instance the order in which retransmission occurs is not
guaranteed and messages may be received out of order.

The following demos test this technique by counting missing or duplicate
messages encountered at either end of the link:
 1. `c_qos.py` Client-side application.
 2. `s_qos_cp.py` Server-side application: run under CPython 3.5+ or
 MicroPython.

