import json
import unittest
import urllib.error
import urllib.request

from trade_bot.health import HealthConfig, HealthMonitor
from trade_bot.healthserver import HealthServer


class ExplodingMonitor:
    def snapshot(self):
        raise RuntimeError("snapshot blew up")


def get(port, path="/healthz"):
    """Fetch a path, returning (status, parsed body) for 2xx and 5xx alike."""
    url = f"http://127.0.0.1:{port}{path}"
    # A proxy in the environment must not intercept a loopback probe.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


class HealthServerTests(unittest.TestCase):
    def serve(self, monitor):
        server = HealthServer(monitor, port=0).start()
        self.addCleanup(server.stop)
        return server

    def build_monitor(self, **config):
        settings = {"notify_on_start": False, "watchdog_timeout": 300}
        settings.update(config)
        return HealthMonitor(None, HealthConfig(**settings))

    def test_healthy_returns_200_and_json(self):
        server = self.serve(self.build_monitor())
        status, body = get(server.port)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertIn("uptime", body)

    def test_unhealthy_returns_503(self):
        monitor = self.build_monitor()
        monitor.register("exchange", lambda: False)
        server = self.serve(monitor)
        status, body = get(server.port)
        self.assertEqual(status, 503)
        self.assertEqual(body["status"], "unhealthy")
        self.assertEqual(body["checks"][0]["status"], "failed")

    def test_all_health_paths_are_served(self):
        server = self.serve(self.build_monitor())
        for path in ("/healthz", "/health", "/"):
            self.assertEqual(get(server.port, path)[0], 200, path)

    def test_query_strings_are_tolerated(self):
        server = self.serve(self.build_monitor())
        self.assertEqual(get(server.port, "/healthz?source=uptimerobot")[0], 200)

    def test_unknown_paths_are_404(self):
        server = self.serve(self.build_monitor())
        self.assertEqual(get(server.port, "/secrets")[0], 404)

    def test_a_broken_monitor_still_answers_the_probe(self):
        server = self.serve(ExplodingMonitor())
        status, body = get(server.port)
        self.assertEqual(status, 503)
        self.assertIn("snapshot blew up", body["detail"])

    def test_a_stalled_loop_is_reported_as_unhealthy_over_http(self):
        class Clock:
            now = 0.0

            def __call__(self):
                return self.now

        clock = Clock()
        monitor = HealthMonitor(
            None, HealthConfig(notify_on_start=False, watchdog_timeout=60), clock=clock
        )
        monitor.beat()
        clock.now += 600
        server = self.serve(monitor)
        status, body = get(server.port)
        self.assertEqual(status, 503)
        self.assertIn("no tick", body["note"])

    def test_stop_releases_the_port(self):
        server = HealthServer(self.build_monitor(), port=0).start()
        port = server.port
        server.stop()
        with self.assertRaises(Exception):
            get(port)

    def test_start_is_idempotent(self):
        server = self.serve(self.build_monitor())
        self.assertEqual(server.port, server.start().port)


if __name__ == "__main__":
    unittest.main()
