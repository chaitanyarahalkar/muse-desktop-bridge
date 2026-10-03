"""Protocol and credential-state checks; no Meta requests are made by tests."""

import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch

import muse_auth as m


SDK_TOKEN = "mgst_" + "A" * 43


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, base, path, body=None, bearer=None):
        self.calls.append((base, path, copy.deepcopy(body), bearer))
        expected_path, response = self.responses.pop(0)
        if path != expected_path:
            raise AssertionError(f"Expected {expected_path}, got {path}")
        if isinstance(response, Exception):
            raise response
        return response


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = m.Store(Path(self.temporary.name) / "private")

    def test_otp_to_device_rotation_uses_distinct_credentials(self):
        http = FakeHttp([
            ("/hatch/auth/start", {"access_token": "temp-login", "id_for_aymh": "aymh"}),
            ("/hatch/auth/login_options", {"password_login_available": True}),
            ("/hatch/auth/send_otp", {"auth_state": "otp-state", "flow": "login"}),
            ("/hatch/auth/confirm_otp", {"abra_access_token": "user-session",
                                      "abra_user_id": "user-id", "frl_access_token": "frl"}),
            ("/device_token/mint", {"access_token": "device-access", "refresh_token": "hatch_refresh:refresh-1"}),
            ("/device_token/refresh", {"payload": {"access_token": "device-access-2",
                                                 "refresh_token": "hatch_refresh:refresh-2"}}),
        ])
        client = m.Client(self.store, http)
        client.start()
        client.options()
        client.send_otp("person@example.com")
        self.assertEqual(client.confirm_otp("123456"), "authenticated")
        client.mint(SDK_TOKEN, "  HOMELINK-ABCDEF  ")
        client.refresh()

        self.assertEqual([call[3] for call in http.calls],
                         [None, "temp-login", "temp-login", "temp-login", "user-session",
                          "hatch_refresh:refresh-1"])
        fields = [call[2] for call in http.calls[:4]]
        self.assertEqual(len({body["waterfall_id"] for body in fields}), 1)
        self.assertEqual(len({body["device_id"] for body in fields}), 1)
        self.assertEqual(fields[3]["auth_state"], "otp-state")
        self.assertTrue(fields[3]["supports_account_selection"])
        self.assertEqual(http.calls[4][2], {"device_id": "homelink-abcdef", "sdk_token": SDK_TOKEN})
        self.assertNotEqual(fields[0]["device_id"], http.calls[4][2]["device_id"])
        saved = json.loads(self.store.path.read_text())
        self.assertEqual(saved["device"]["access_token"], "device-access-2")
        self.assertNotIn("123456", self.store.path.read_text())
        self.assertNotIn("temp-login", self.store.path.read_text())
        self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.store.directory.stat().st_mode), 0o700)

    def test_account_selection_and_two_factor_must_finish_before_mint(self):
        http = FakeHttp([
            ("/hatch/auth/select_account", {"two_factor_required": True, "two_factor_state": "2fa",
                                          "two_factor_methods": [{"method_id": "totp", "method_type": "TOTP"}],
                                          "abra_access_token": "not-yet-authorized", "abra_user_id": "id"}),
            ("/hatch/auth/two_factor", {"abra_access_token": "authorized", "abra_user_id": "id"}),
        ])
        client = m.Client(self.store, http)
        client.state.update(waterfall_id="flow", login={"temp_token": "temp"})
        client.accept_credentials({"account_selection_required": True, "selection_state": "select",
                                   "accounts": [{"account_ref": "one"}, {"account_ref": "two"}]})
        with self.assertRaises(m.FlowError):
            client.select_account("unoffered")
        self.assertEqual(client.select_account("two"), "two_factor_required")
        self.assertNotIn("session", client.state)
        with self.assertRaises(m.FlowError):
            client.mint(SDK_TOKEN)
        self.assertEqual(client.two_factor("totp", "654321"), "authenticated")
        self.assertEqual(http.calls[0][2]["selection_state"], "select")
        self.assertEqual(http.calls[1][2]["two_factor_state"], "2fa")

    def test_send_otp_omits_absent_region_and_preserves_explicit_hint(self):
        # Regression: the live server rejected region:null with HTTP 400.
        for region, expected in ((None, None), ("", None), ("  ", None), (" us ", "US")):
            with self.subTest(region=region):
                http = FakeHttp([("/hatch/auth/send_otp", {"auth_state": "otp-state"})])
                client = m.Client(self.store, http)
                client.state.update(waterfall_id="flow", login={"temp_token": "temp"})
                client.send_otp("person@example.com", region)
                # Check the JSON wire representation, including absent vs null.
                body = json.loads(json.dumps(http.calls[0][2]))
                if expected is None:
                    self.assertNotIn("region", body)
                else:
                    self.assertEqual(body["region"], expected)
                self.assertEqual(http.calls[0][3], "temp")

    def test_schema_error_shows_field_without_echoing_server_secrets(self):
        body = {"title": "JSON Schema Validation Error", "detail":
                "JSON field 'region'. Expected type 'string'. echoed-secret-value"}
        client = m.Client(self.store, FakeHttp([("/hatch/auth/send_otp", m.ApiError(400, body))]))
        client.state.update(waterfall_id="flow", login={"temp_token": "temp"})
        with self.assertRaises(m.ApiError) as caught:
            client.send_otp("person@example.com")
        message = str(caught.exception)
        self.assertIn("/hatch/auth/send_otp", message)
        self.assertIn("'region' must have type 'string'", message)
        self.assertNotIn("echoed-secret-value", message)
        self.assertEqual(json.loads(self.store.path.read_text())["last_error"]["body"], body)

    def test_failed_or_incomplete_refresh_preserves_working_pair(self):
        original = {"device_id": "homelink-123abc", "access_token": "working",
                    "refresh_token": "hatch_refresh:still-working"}
        for response in ({"access_token": "incomplete"},
                         m.ApiError(401, {"title": "Authentication Error"})):
            with self.subTest(response=type(response).__name__):
                self.store.data["device"] = copy.deepcopy(original)
                self.store.save()
                http = FakeHttp([("/device_token/refresh", response)])
                with self.assertRaises(m.FlowError):
                    m.Client(self.store, http).refresh()
                self.assertEqual(json.loads(self.store.path.read_text())["device"], original)
                self.assertEqual(http.calls[0][3], "hatch_refresh:still-working")

    def test_failed_bootstrap_preserves_existing_login_context(self):
        self.store.data.update(waterfall_id="existing-flow", login={"temp_token": "existing-temp"})
        self.store.save()
        http = FakeHttp([("/hatch/auth/start", m.ApiError(503, {"error": "unavailable"}))])
        with self.assertRaises(m.ApiError):
            m.Client(self.store, http).start()
        saved = json.loads(self.store.path.read_text())
        self.assertEqual(saved["waterfall_id"], "existing-flow")
        self.assertEqual(saved["login"]["temp_token"], "existing-temp")

    def test_frl_exchange_uses_returned_muse_credential(self):
        http = FakeHttp([("/hatch/login", {"access_token": "muse-token", "abra_user_id": 42})])
        client = m.Client(self.store, http)
        client.exchange_frl("frl-token")
        self.assertEqual(http.calls[0][2], {"frl_access_token": "frl-token"})
        self.assertEqual(http.calls[0][3], "frl-token")
        self.assertEqual(client.state["session"]["access_token"], "muse-token")

    def test_http_redirect_is_not_followed_with_credentials(self):
        paths = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                paths.append(self.path)
                self.send_response(302)
                self.send_header("Location", "/redirect-target")
                self.end_headers()

            def do_GET(self):
                paths.append(self.path)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with patch.object(m, "AUTH_BASE", base):
                with self.assertRaises(m.ApiError) as error:
                    m.Http().request(base, "/start", {}, "private-token")
            self.assertEqual(error.exception.status, 302)
            self.assertEqual(paths, ["/start"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
