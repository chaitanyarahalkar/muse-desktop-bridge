import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import executor_bridge as bridge
from muse_auth import Store
from musegadget import config


class CredentialBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.auth = self.root / "auth"
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.store = Store(self.auth)
        self.store.data.update(
            session={"access_token": "user-session"}, sdk_token="sdk-identifier",
            device={"device_id": "homelink-abcdef", "access_token": "device-access",
                    "refresh_token": "device-refresh"},
        )
        self.store.save()

    def test_import_matches_minted_identity_without_copying_user_session(self):
        with patch.object(bridge, "AUTH_DIR", self.auth), patch.dict(
            "os.environ", {"MUSEGADGET_STATE_DIR": str(self.runtime)}
        ):
            identity, sdk = bridge.import_credentials()
            pairing = config.load_json(config.PAIRING_FILE)
        self.assertEqual(identity.node_id, "homelink-abcdef")
        self.assertEqual(sdk, "sdk-identifier")
        self.assertEqual(pairing["access_token"], "device-access")
        self.assertNotIn("user-session", json.dumps(pairing))

    def test_rotation_sync_preserves_user_session_and_rejects_stale_writer(self):
        replacement = {"access_token": "new-access", "refresh_token": "new-refresh",
                       "access_token_saved_at": 123}
        with patch.object(bridge, "AUTH_DIR", self.auth):
            bridge.sync_rotated_pair("homelink-abcdef", "device-refresh", replacement)
            with self.assertRaises(RuntimeError):
                bridge.sync_rotated_pair("homelink-abcdef", "device-refresh", {
                    **replacement, "refresh_token": "stale-writer-token",
                })
        saved = Store(self.auth).data
        self.assertEqual(saved["device"]["refresh_token"], "new-refresh")
        self.assertEqual(saved["session"]["access_token"], "user-session")


if __name__ == "__main__":
    unittest.main()
