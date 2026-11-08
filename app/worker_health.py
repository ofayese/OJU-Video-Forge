"""Private RQ heartbeat probe used by Compose and operational health."""

from redis import Redis
from rq import Worker

from .core import REDIS_URL


def main():
    connection = Redis.from_url(REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
    workers = Worker.all(connection=connection)
    if not any(worker.get_state() in {"idle", "busy"} for worker in workers):
        raise SystemExit("No live render worker")


if __name__ == "__main__":
    main()
