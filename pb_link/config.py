# config.py Config file for Pyboard IOT client

# Copyright (c) Peter Hinch 2018
# Released under the MIT licence. Full text in root of this repository.

# Modify this file for local network, server, pin usage

# config list is shared by Pyboard and by server
# config elements by list index:
# 0. Port (integer).
# 1. Server IP (string).
# 2. Server timeout in ms (int). Must == TIMEOUT in server's local.py.
# 3. Send reports every N seconds (0: never) (int).
# 4. SSID (str).
# 5. Password (str).
# Use empty string ('') in  SSID and PW to only connect to the WLAN which the
# ESP8266 already "knows".

config = [8123, '192.168.0.42', 1500, 10, 'misspiggy', '6163VMiqSTyx']

try:
    from pyb import I2C  # Only pyb supports slave mode
    from machine import Pin  # rst Pin must be instantiated by machine
except ImportError:  # Running on server
    pass
else:  # Pyboard configuration
    # I2C instance may be hard or soft.
    _i2c = I2C(1, mode=I2C.SLAVE)
    # Pins are arbitrary.
    _syn = Pin('X11')
    _ack = Pin('Y8')
    # Reset tuple (Pin, level, pulse length in ms).
    # Reset ESP8266 with a 0 level for 200ms.
    _rst = (Pin('X12'), 0, 200)
    hardware = [_i2c, _syn, _ack, _rst]
