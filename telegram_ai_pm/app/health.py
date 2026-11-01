"""Heartbeat refreshed by the event loop; checked by Docker without API requests."""
import asyncio
import os
import time
from pathlib import Path

PATH = Path(os.getenv('BOT_HEARTBEAT_FILE', '/tmp/pm-bot-heartbeat'))


async def heartbeat():
    while True:
        PATH.touch()
        await asyncio.sleep(15)


def healthy():
    try:
        return time.time() - PATH.stat().st_mtime < 60
    except OSError:
        return False


if __name__ == '__main__':
    raise SystemExit(0 if healthy() else 1)
