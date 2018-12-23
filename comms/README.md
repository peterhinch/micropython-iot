# Communication between ESP8266 clients

This demo is of a remote control switch. Pressing a button on one client lights
the LED on another.

# Files

 1. `s_comms_cp.py` The server application. Run under CPython 3.5+ or
 MicroPython.
 2. `c_comms_tx.py` Transmitting client. Expects a switch to ground on GPIO0.
 Requires `primitives.py` from this repo and `aswitch.py` from
 [this repo](https://github.com/peterhinch/micropython-async).
 3. `c_comms_rx.py` Receiving client. LED on GPIO2 displays result.
 4. `local_rx.py` Template for receiver's `local.py`.
 5. `local_tx.py` Ditto for transmitter.

# Running the demo

On the clients ensure that `client.py`, `primitives.py` and `local.py` are
installed and that `local.py` has been edited for local network setup.

On the server, if using MicroPython, ensure `primitives.py` is on the path.
