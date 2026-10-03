# Working on Muse Desktop Bridge

## Scope and layout

This is an unofficial macOS bridge to Meta Muse, combining an OTP authentication CLI with the official Muse Gadget SDK's executor and Noise transport.

- `muse-auth/muse_auth.py`: standard-library HTTPS auth and private state storage.
- `muse-auth/test_muse_auth.py`: auth state-machine and HTTP tests.
- `muse-executor/executor_bridge.py`: macOS executor wrapper, lifecycle, token synchronization, and round-trip test.
- `muse-executor/muse-executor`: launcher using the local `.venv`.
- `muse-executor/test_executor_bridge.py`: credential import/synchronization tests.
- `muse-executor/vendor/muse-gadget-sdk/`: unmodified upstream snapshot; see `THIRD_PARTY_NOTICES.md` and its nested `AGENTS.md`.

## Setup and checks

Use macOS, Python 3.13+, and `uv`:

```sh
./scripts/setup.sh
./scripts/test.sh
python3 scripts/check_secrets.py
```

The tests are local and do not require credentials or call Meta services. The secret guard scans staged/tracked Git blobs, so stage intended changes before the final check. Enable `.githooks/pre-commit` with `git config core.hooksPath .githooks`.

Use `sh -n` on changed shell scripts. Run tests appropriate to the change; preserve existing auth and bridge regression coverage. If pairing/Noise internals change, follow the upstream instructions for compatibility testing too.

## Credentials and private data

- Never commit `.state/`, `.runtime/`, actual SDK/session/device/VM credentials, OTPs, raw HTTP captures, logs, APKs, or decompiled app source.
- Keep examples generic: `person@example.com`, `homelink-abcdef`, and obviously synthetic test credentials. Do not embed a developer's device ID, VM/chat IDs, email, username, or absolute home path.
- Do not print raw state or token-bearing server responses while debugging. Report presence flags, status codes, and safe field names.
- Keep state directories owner-only and credentials owner-readable/writable. Preserve atomic saves and redirect rejection.
- Use tests with mock transports before live calls. Only perform account login, OTP sending, device mint/refresh, or a Muse-invoked command when the user asks for the relevant live operation.
- The executor runs as the current macOS user and is not an account-level sandbox. Do not describe file permissions as isolating credentials from commands running as that same user.

## Protocol invariants

- Temporary login bearer, Muse user session, Gadget SDK token, device pair, and VM bearer are distinct credential types.
- Device mint uses the Muse session bearer, with `device_id` and `sdk_token` in JSON.
- Device refresh uses `hatch_refresh:` exactly once. Save only complete replacement pairs.
- The phone-login device ID and gadget node ID are different. The bridge expects a stable lowercase `homelink-` plus six hex digits.
- Omit absent OTP `region`; the server rejects JSON `null` for that field.
- Account selection/2FA must finish before treating login as authenticated. Report checkpoints and registration-completion branches explicitly.
- Only one process should rotate a pair. Stop the executor before auth `setup`, `mint`, or `refresh`; preserve stale-writer detection.
- Keep SDK wire registration as `platform: linux`, `device_family: homehub`; macOS is described in user-facing metadata. Never advertise ESP32 OTA capabilities.
- `/chat/stream` acknowledges delivery. It does not return the conversational reply. `session_id`, not `chat_id`, selects a chat.

## Editing conventions

Keep the auth CLI dependency-free. Prefer small changes to the bridge over patching vendored code. Preserve upstream copyright and license notices; pin and document SDK updates. Keep macOS behavior and limitations accurately documented in the README. Avoid adding background autostart or live test calls to setup/CI.

Before handing back work, summarize changes, the checks actually run, and any unverified behavior. For publication, follow the host environment's repository-destination rules and verify the staged content, not just the working tree.
