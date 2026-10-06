"""FLOODGUARD AI application package."""

import asyncio
import sys

# The selector event loop is preferred on Windows.
#
# This originally existed for paho-mqtt, whose blocking socket thread is
# incompatible with ProactorEventLoop's `loop.add_writer` (unavailable there).
# With MQTT gone there is no such constraint, but the selector loop is also the
# right default for this app: the workload is high-fanout Socket.IO and many
# short-lived HTTPS calls, both of which behave better on it. Keeping the pin
# avoids a Windows/macOS/Linux behavioural split in the event loop.
#
# Must run before any event loop is created. Importing this package happens
# before uvicorn calls asyncio.run(), so it takes effect in time.
if sys.platform == "win32":
    _policy = asyncio.get_event_loop_policy()
    if not isinstance(_policy, asyncio.WindowsSelectorEventLoopPolicy):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())