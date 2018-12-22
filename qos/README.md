# Improving Quality of Service

In MQTT parlance the link operates at qos==0: there is no guarantee of packet
delivery. Normally when an outage occurs transmission is delayed until
connectivity resumes. Packet loss will occur if, at the time when a message is
sent, an outage has occurred but has not yet been detected by the sender. In
this case the sender puts the packet into a socket whose peer is lost.

The desired outcome is qos==2. In this case delivery is guaranteed, and there
is also a guarantee that duplicate messages are discarded i.e. every
transmitted message will be received and processed exactly once. Note that, in
this context, the "guarantee" is conditional on any outage eventually ending.
Further there is no guarantee of timeliness: if an outage lasts an hour
messages will be delayed accordingly.

The conventional approach to guaranteed delivery is by sending acknowledge
packets, however some complexity results because the acknowledge packets
themselves are subject to uncertain delivery.

The characteristics of this interface enable a simpler solution, implemented in
application code. Every message includes an incrementing `message_id` in the
data. This enables the receiving coro to discard duplicate messages. The
sending coro works as follows:

```python
    async def writer(self, conn):
        dstr = json.dumps(self.data)  # If an outage occurs now
        await conn.write(dstr)  # message may be lost
        await asyncio.sleep(local.TIMEOUT / 1000)  # time for outage detection
        if not conn.status():  # Message may have been lost
            await conn.write(dstr)
```
Consider the following cases:
 1. Outage occurs and is detected before the initial write. The write waits
 until the outage is cleared and proceeds successfully. On completion `status`
 returns `True` and no retransmission will occur.
 2. Outage precedes initial write but is not yet detected. The initial write
 puts data into a socket whose peer is lost. The delay ensures that, by the
 time `status` is called, the outage will have been detected. Retransmission
 occurs and the message is received once. (Actual retransmission is delayed by
 the library code until the outage is cleared).
 3. Initial write succeeds. Outage occurs during the `sleep` period. In this
 case the message is sent again (delayed as above). The recipient rejects it
 because the `message_id` is that of the previous message.

The following demos illustrate this technique:
 1. `c_qos.py` Client-side application.
 2. `s_qos_cp.py` Server-side application: run under CPython 3.5+.

It is perhaps worth noting that the discarding of duplicates is based on
packets being sent in order. If there is more than one coro sending data,
packets may be received out of order. Detecting duplicates becomes more
difficult.
