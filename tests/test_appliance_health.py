import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE = Path(__file__).parents[1] / "installer" / "activation_health.py"
SPEC = importlib.util.spec_from_file_location("activation_health", MODULE)
health = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(health)


class ActivationHealthTests(unittest.TestCase):
    def test_inspect_parses_state_without_requesting_environment(self):
        def runner(command, **kwargs):
            self.assertEqual(command[:3], ["docker", "inspect", "--format"])
            self.assertNotIn("Config", command)
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps({"Running": True, "Health": {"Status": "healthy"}}),
                "",
            )

        state = health.inspect_container("ksi-cctv", runner=runner)

        self.assertTrue(state["Running"])

    def test_requires_engine_web_health_and_running_gateway(self):
        states = {
            "ksi-cctv": {"Running": True, "Health": {"Status": "healthy"}},
            "ksi-cctv-web": {"Running": True, "Health": {"Status": "healthy"}},
            "ksi-cctv-gateway": {"Running": True},
            "ksi-cctv-https": {"Running": True, "Health": {"Status": "healthy"}},
        }
        self.assertTrue(health.healthy(states))
        states["ksi-cctv-web"]["Health"]["Status"] = "unhealthy"
        self.assertFalse(health.healthy(states))

    def test_https_probe_uses_site_host_and_requires_success_response(self):
        class Response:
            status = 200

            @staticmethod
            def getheader(name):
                return health.REQUIRED_HTTPS_HEADERS.get(name)

        class Connection:
            def __init__(self):
                self.headers = None

            def request(self, method, path, headers):
                self.headers = headers

            def getresponse(self):
                return Response()

            def close(self):
                pass

        connection = Connection()
        with patch.dict("os.environ", {"KSI_CCTV_SITE_ADDRESS": "cctv.local"}):
            self.assertTrue(health.https_ready(lambda: connection))
        self.assertEqual(connection.headers, {"Host": "cctv.local"})

        class MissingHeaderResponse(Response):
            @staticmethod
            def getheader(name):
                return None if name == "X-Frame-Options" else Response.getheader(name)

        connection.getresponse = lambda: MissingHeaderResponse()
        with patch.dict("os.environ", {"KSI_CCTV_SITE_ADDRESS": "cctv.local"}):
            self.assertFalse(health.https_ready(lambda: connection))

    def test_http_probe_requires_exact_https_redirect(self):
        class Response:
            status = 308

            @staticmethod
            def getheader(name):
                return "https://cctv.local/login" if name == "Location" else None

        class Connection:
            def request(self, method, path, headers):
                self.requested = (method, path, headers)

            def getresponse(self):
                return Response()

            def close(self):
                pass

        connection = Connection()
        with patch.dict("os.environ", {"KSI_CCTV_SITE_ADDRESS": "cctv.local"}):
            self.assertTrue(health.http_redirect_ready(lambda: connection))
        self.assertEqual(connection.requested, ("GET", "/login", {"Host": "cctv.local"}))

        Response.getheader = staticmethod(
            lambda name: "http://cctv.local/login" if name == "Location" else None
        )
        with patch.dict("os.environ", {"KSI_CCTV_SITE_ADDRESS": "cctv.local"}):
            self.assertFalse(health.http_redirect_ready(lambda: connection))


if __name__ == "__main__":
    unittest.main()
