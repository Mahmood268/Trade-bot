"""A tiny HTTP endpoint so Docker or an uptime monitor can poll the bot.

``GET /healthz`` returns ``200`` with a JSON body when healthy and ``503``
when not, which is what Docker health checks, Kubernetes probes and services
like UptimeRobot expect.

Binds to loopback by default. Do not expose it directly - the body describes
your bot's internals. Put it behind a reverse proxy if it must be public.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

log = logging.getLogger(__name__)

HEALTH_PATHS = frozenset({"/healthz", "/health", "/"})

#: How often the serving thread checks whether it has been asked to stop.
SHUTDOWN_POLL_INTERVAL = 0.05


def _make_handler(monitor: Any) -> type[BaseHTTPRequestHandler]:
    class HealthHandler(BaseHTTPRequestHandler):
        server_version = "TradeBotHealth/1.0"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            # Probes poll constantly; routing them to DEBUG keeps logs usable.
            log.debug("health probe: " + format, *args)

        def _respond(self, *, body: bytes = b"", status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if body and self.command != "HEAD":
                self.wfile.write(body)

        def _health(self) -> None:
            if self.path.split("?")[0] not in HEALTH_PATHS:
                self._respond(body=b'{"error":"not found"}', status=404)
                return
            try:
                report = monitor.snapshot()
                payload = report.as_dict()
                status = 200 if report.ok else 503
            except Exception as exc:  # noqa: BLE001 - a probe must always get an answer
                log.exception("health snapshot failed")
                payload = {"status": "error", "detail": f"{type(exc).__name__}: {exc}"}
                status = 503
            self._respond(body=json.dumps(payload).encode(), status=status)

        do_GET = _health
        do_HEAD = _health

    return HealthHandler


class HealthServer:
    """Serves ``monitor``'s status over HTTP on a background thread."""

    def __init__(self, monitor: Any, *, host: str = "127.0.0.1", port: int = 8080) -> None:
        self._monitor = monitor
        self._host = host
        self._requested_port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int | None:
        """The bound port - useful when port 0 asked the OS to pick one."""
        return self._server.server_address[1] if self._server else None

    def start(self) -> "HealthServer":
        if self._server is not None:
            return self
        self._server = ThreadingHTTPServer(
            (self._host, self._requested_port), _make_handler(self._monitor)
        )
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            # serve_forever polls for the shutdown flag; the 0.5s default
            # would make every stop() take half a second.
            target=lambda: self._server.serve_forever(poll_interval=SHUTDOWN_POLL_INTERVAL),
            name="health-server",
            daemon=True,
        )
        self._thread.start()
        log.info("health endpoint on http://%s:%s/healthz", self._host, self.port)
        return self

    def stop(self, *, timeout: float = 5.0) -> None:
        server, self._server = self._server, None
        thread, self._thread = self._thread, None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread and thread.is_alive():
            thread.join(timeout=timeout)

    def __enter__(self) -> "HealthServer":
        return self.start()

    def __exit__(self, *exc_info: Any) -> None:
        self.stop()
