# __init__.py Common functions for uasyncio primitives

# Released under the MIT licence.
# Copyright (C) Peter Hinch 2019-2020

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

type_gen = type((lambda: (yield))())  # Generator type

# If a callback is passed, run it and return.
# If a coro is passed initiate it and return.
# coros are passed by name i.e. not using function call syntax.
def launch(func, *tup_args):
    res = func(*tup_args)
    if isinstance(res, type_gen):
        loop = asyncio.get_event_loop()
        loop.create_task(res)

def set_global_exception():
    def _handle_exception(loop, context):
        import sys
        sys.print_exception(context["exception"])
        sys.exit()
    loop = asyncio.get_event_loop()
    loop.set_exception_handler(_handle_exception)
