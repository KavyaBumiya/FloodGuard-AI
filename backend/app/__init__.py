"""FLOODGUARD AI application package."""

import asyncio
import sys

# MQTT uses paho's client directly (aiomqtt is not a dependency): paho owns its
# blocking socket with its own network thread, which works on Windows' default
# ProactorEventLoop. Some async helpers (e.g. loop.add_writer) are still not
# implemented on ProactorEventLoop, so the SelectorEventLoop is preferred.
#
# This must be set before any event loop is created. Importing this package
# happens before uvicorn calls asyncio.run(), so it takes effect in time.
if sys.platform == "win32":
    _policy = asyncio.get_event_loop_policy()
    if not isinstance(_policy, asyncio.WindowsSelectorEventLoopPolicy):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())