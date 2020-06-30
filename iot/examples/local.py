MY_ID = '1'  # Client-unique string
SERVER = '192.168.0.10'
SSID = 'use_my_local' # Put in your WiFi credentials
PW = 'PASSWORD'
PORT = 8123
TIMEOUT = 2000

# The following may be deleted
if SSID == 'use_my_local':
    from iot.examples.my_local import *
