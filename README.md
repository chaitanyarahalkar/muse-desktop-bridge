# Muse Desktop Bridge

**Connect your Mac to Meta Muse for bidirectional messaging and local command execution.**

An unofficial desktop integration built on Meta's [Muse Gadget SDK](https://github.com/facebookincubator/muse-gadget-sdk). Sign in with email/SMS OTP, mint device credentials, and connect a local executor to your Muse VM.

## What it does

- Email/SMS OTP login, with account selection and two-factor challenges.
- Device-token minting, refresh, and VM discovery.
- An encrypted Noise-over-WebSocket connection to your Muse VM.
- Muse-to-Mac shell commands, file reads/writes, and device-health checks.
- Mac-to-Muse chat messages over the same connection.
- Background start/stop/status commands and a live round-trip test.

The Mac opens an outbound connection. No publicly accessible inbound port or Bluetooth pairing is needed for this imported-token workflow.

## Quick start

### 1. Install

Requirements: **macOS**, **Python 3.13+** (tested on 3.13), [**uv**](https://docs.astral.sh/uv/getting-started/installation/), an existing Muse account with an available Muse VM, and a **Gadget SDK token** from [gadgets.muse.ai](https://gadgets.muse.ai/).

From the repository root:

```sh
./scripts/setup.sh
```

This installs the vendored SDK and pinned runtime/test dependencies into `muse-executor/.venv`. A new environment defaults to Python 3.13; `uv` can download that version if needed. To select a different interpreter for a new environment, use `PYTHON=python3.13 ./scripts/setup.sh`.

### 2. Sign in

```sh
python3 muse-auth/muse_auth.py setup
```

Enter your account's email or international phone number, the verification code, any account/2FA selection, and your Gadget SDK token in the terminal. Code and token input is hidden. A stable `homelink-xxxxxx` gadget identity is generated automatically.

The SDK token identifies the integration; it is **not** a substitute for a Muse user-session bearer. Login completes before the client mints the device pair.

### 3. Connect

```sh
./muse-executor/muse-executor start
./muse-executor/muse-executor status
```

`start` waits for confirmed registration. The device appears as **`<macOS username>'s Mac (Muse executor)`**.

The executor runs with your macOS account's permissions and exposes `system.run`, `file.read`, `file.write`, and `device.health`. It is not sandboxed from files your account can access, including local credential files. Run it as your normal account.

### 4. Try it

Ask Muse in the app:

> On my connected Mac, run `sw_vers` and tell me the result.

Or initiate a message locally:

```sh
./muse-executor/muse-executor send 'On this connected Mac, run sw_vers and report the result.'
```

`send` returns a delivery acknowledgement. Muse's conversational response appears in the app; executor requests and their results travel over the device connection.

For an automated read-only round trip:

```sh
./muse-executor/muse-executor test
```

This opens a side chat asking Muse to invoke `/usr/bin/uname -s` on the Mac, waits up to 120 seconds for the exact command, and checks for `Darwin` with exit code 0. The result is stored privately in `muse-executor/.runtime/self-test.json`.

## Everyday commands

```sh
./muse-executor/muse-executor status
./muse-executor/muse-executor stop
./muse-executor/muse-executor start
```

Use `./muse-executor/muse-executor run` for foreground operation while the background service is stopped. There is no login/reboot autostart.

```sh
python3 muse-auth/muse_auth.py status
python3 muse-auth/muse_auth.py vms
```

While the executor runs, it owns device-token rotation and synchronizes refreshed credentials into the auth client's state. **Stop it before running `setup`, `mint`, or `refresh` in the auth CLI.**

More: [authentication commands](muse-auth/README.md) · [executor commands](muse-executor/README.md) · [protocol overview](docs/protocol.md)

## How the connection works

```text
Email / SMS OTP
      │
      ▼
Muse user session + Gadget SDK token
      │  POST /device_token/mint
      ▼
Device access / refresh tokens
      │  GET /fetch_vms
      ▼
Per-VM bearer
      │  WebSocket + Noise XX handshake
      ▼
Mac executor  ◄──────────────────────►  Muse VM
             /link-control: invokes and results
             /chat/stream: device-originated messages
```

The localhost interface is an owner-accessible Unix socket. A local `send` request is forwarded to `LinkSession.send_chat()`, which builds an encrypted `POST /chat/stream` with the gadget `device_id`. Optional `session_id` selects a side chat.

## Credentials and repository hygiene

| Location | Contents |
| --- | --- |
| `muse-auth/.state/state.json` | Login/session state, SDK token, device pair, and fetched VM credentials |
| `muse-executor/.runtime/pairing.json` | Executor's device pair |
| `muse-executor/.runtime/` | Socket, identity, logs, process lock, and test results |

State directories use `0700`; credential files use `0600`. Credentials are stored as local JSON, not in the macOS Keychain. Private state, runtime artifacts, logs, environment files, captures, and virtual environments are ignored by Git.

Enable the repository's staged-file guard:

```sh
git config core.hooksPath .githooks
python3 scripts/check_secrets.py
```

The guard inspects the Git index, rejects private artifact paths, and checks common token formats. It prints finding types and paths rather than secret values. A Gitleaks configuration adds Muse SDK-token detection for broader scanning. Review staged diffs as well; a pattern check cannot identify every opaque credential. Use synthetic fixtures in tests and never attach raw state or API responses to issues.

## Development

```sh
./scripts/setup.sh
./scripts/test.sh
```

The suite covers the auth state machine and credential handling, bridge token synchronization, and the upstream SDK's Noise, pairing, transport, and executor implementation. Tests use fixtures and local mock servers; no Muse account is required.

The original macOS integration was verified end to end with device-token refresh, VM discovery, Noise registration, and a Muse-invoked `uname` command returning `Darwin`. Live credentials and run artifacts are intentionally absent from the repository.

```text
muse-auth/             Standard-library authentication CLI and tests
muse-executor/         macOS bridge, launcher, and bridge tests
  vendor/             Pinned upstream SDK source and its license/tests
docs/                 Protocol overview
scripts/              Installation, test runner, staged credential guard
AGENTS.md             Contributor and coding-agent instructions
```

## Compatibility and provenance

The desktop bridge targets macOS. The authentication request schemas were reconstructed from Muse Android `9.0.0.23.178`; this repository does not include the APK or decompiled app source. The native-login endpoints are first-party interfaces and may change.

The SDK is pinned to commit [`b1a3822995a51c0203cd1f3d72c1c656b8c3e620`](https://github.com/facebookincubator/muse-gadget-sdk/tree/b1a3822995a51c0203cd1f3d72c1c656b8c3e620). The wire registration retains the SDK's `platform: linux` and `device_family: homehub` values; the device name and command descriptions identify macOS. The SDK's Linux installer is not used by this bridge.

This project is not affiliated with or endorsed by Meta. Upstream SDK code is licensed under Apache-2.0; see [third-party notices](THIRD_PARTY_NOTICES.md).
