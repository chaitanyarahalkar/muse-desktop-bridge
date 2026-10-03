# Protocol overview

The client combines reconstructed native-login schemas with the public Muse Gadget SDK. The inspected Android version was `9.0.0.23.178`; SDK behavior is pinned to `b1a3822995a51c0203cd1f3d72c1c656b8c3e620`. This describes observed behavior rather than a public third-party OAuth contract.

## Credential roles

| Credential | Purpose |
| --- | --- |
| Temporary native-login bearer | Authenticates OTP and pending login challenges |
| Muse user-session token | Authenticates device-token minting |
| Gadget SDK token | Identifies the integration in mint/refresh JSON |
| Device access token | Retrieves available Muse VMs |
| Device refresh token | Rotates the device access/refresh pair |
| Per-VM bearer | Authenticates the WebSocket upgrade for a specific VM |

The SDK token alone cannot replace a user-session bearer for minting.

## Native login

Requests use `https://hatch-api.meta.ai`:

1. `POST /hatch/auth/start`: phone `device_id` and a new `waterfall_id`; returns a temporary `access_token`.
2. `POST /hatch/auth/login_options`: authenticated with the temporary bearer.
3. `POST /hatch/auth/send_otp`: `contact_point`, optional nonempty `region`, and flow fields.
4. `POST /hatch/auth/confirm_otp`: `auth_state`, `otp_code`, and `supports_account_selection: true`.
5. Follow returned account-selection or second-factor challenges as needed.

Successful native login returns `abra_access_token` and `abra_user_id`, potentially with `frl_access_token` and `frl_account_id`. The ABRA access token is the Muse user-session bearer. Pending challenges take precedence over credential fields. Checkpoints or registration/consent completion are reported explicitly.

For an existing FRL credential, `POST /hatch/login` uses the token in both the bearer header and the `frl_access_token` JSON field; the response contains a Muse access token.

## Device credentials and VM discovery

Requests use `https://api.muse.ai`:

- `POST /device_token/mint`: Muse session bearer; JSON `device_id` and `sdk_token`.
- `POST /device_token/refresh`: bearer `hatch_refresh:<raw-refresh-token>`; the same gadget `device_id` and SDK token in JSON.
- `GET /fetch_vms`: device access bearer; returns VM metadata and per-VM credentials.

Refresh replaces both tokens only after receiving a complete pair. The phone-login device ID and gadget node ID are different identities. Keep the gadget ID stable across mint, refresh, and executor registration.

## Encrypted executor session

The SDK connects to `wss://hatch.metaaivm.com/v1/noise?vm_id=<vm_id>` with the VM bearer, completes Noise XX, and opens an encrypted, long-lived `POST /link-control` stream.

- Device → VM: `link.register` with device identity/capabilities.
- VM → device: `link.invoke` with a command and parameters.
- Device → VM: `link.result` with the command's result.

The control stream carries JSON messages prefixed by little-endian 32-bit lengths. Registration uses `platform: linux` and `device_family: homehub`, as expected by the SDK, while macOS-specific command descriptions identify the host environment.

## Device-originated chat

`LinkSession.send_chat()` sends a separate encrypted `POST /chat/stream` on the same authenticated transport:

```json
{
  "message": "Hello from my Mac",
  "output_modality": "text",
  "device_id": "homelink-abcdef"
}
```

Add `session_id` to select a side chat. The response is an acknowledgement; the conversational reply appears in Muse. Muse can respond to a message by issuing `link.invoke` back to the connected computer.

The local launcher reaches the background process through `muse-executor/.runtime/muse.sock`. It does not expose an inbound TCP port.

## Source references

- [`muse_auth.py`](../muse-auth/muse_auth.py): HTTPS payloads and state transitions.
- [`executor_bridge.py`](../muse-executor/executor_bridge.py): imported credentials, macOS adaptation, and local messaging.
- [`link_client.py`](../muse-executor/vendor/muse-gadget-sdk/linux/src/musegadget/link_client.py): register/invoke/result and chat protocol.
- [`service.py`](../muse-executor/vendor/muse-gadget-sdk/linux/src/musegadget/service.py): reconnect and refresh behavior.
- [`muse_api.py`](../muse-executor/vendor/muse-gadget-sdk/linux/src/musegadget/muse_api.py): VM discovery and refresh.
