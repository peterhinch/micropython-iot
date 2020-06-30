#! /usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os

sys.path.insert(0, os.getcwd())

if len(sys.argv) == 1 or sys.argv[1] == "example":
    print("Standard example server")
    from examples import s_app_cp

    s_app_cp.run()
elif sys.argv[1] == "remote_control":
    print("Remote control example server")
    from example_remote_control import s_comms_cp

    s_comms_cp.run()
elif sys.argv[1] == "qos":
    print("QoS example server")
    from qos import s_qos_cp

    s_qos_cp.run()
else:
    print("Only these options are available:")
    print("example")
    print("remote_control")
    print("qos")
