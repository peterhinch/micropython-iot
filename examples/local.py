from sys import platform

if platform == "linux":
    MY_ID = "1"
else:
    import machine
    import ubinascii

    MY_ID = ubinascii.hexlify(machine.unique_id()).decode()  # '1\n'  # Client-unique \n terminated string
PORT = 8888
TIMEOUT = 5000  # ms. Share between client.py and server.py
# My boxes
SERVER = '192.168.178.60'  # Laptop
# SERVER = '192.168.0.33'  # Pi
SSID = ''  # 'MY_WIFI_SSID'
PW = ''  # ''PASSWORD'
