# Muse authentication CLI

From the repository root:

```sh
python3 muse-auth/muse_auth.py setup
```

The standard-library client prompts for email/phone, OTP, account/2FA choices if required, and a Gadget SDK token. Codes and tokens use hidden input. The SDK token can also be provided through `MUSE_SDK_TOKEN` and is saved privately for subsequent mint/refresh operations.

Use `setup --region US` for an optional phone-country hint. Use the generated gadget ID for the desktop bridge, or provide `--device-id homelink-abcdef` when you intentionally need a preexisting matching identity.

## Commands

| Command | Purpose |
| --- | --- |
| `setup` | Interactive OTP login followed by device-token mint |
| `login` | Login without minting |
| `start` | Start native login and save a temporary bearer |
| `options` | Fetch available login options |
| `send-otp` | Prompt for email/phone and request a code |
| `confirm-otp` | Prompt for the code and complete pending challenges |
| `select-account ACCOUNT_REF` | Resume a server-offered account selection |
| `two-factor METHOD_ID [--send-code]` | Send or confirm a pending second-factor challenge |
| `exchange-frl` | Prompt for an existing Meta FRL credential and exchange it for a Muse session |
| `mint` | Mint device credentials using a logged-in Muse session |
| `refresh` | Rotate the device pair |
| `vms` | Fetch and privately save VM credentials |
| `status` | Display credential-presence flags and flow status |

For example:

```sh
python3 muse-auth/muse_auth.py status
python3 muse-auth/muse_auth.py vms
```

If an OTP request succeeded but confirmation failed, retry `confirm-otp`. `send-otp` requests another code. HTTP requests are not automatically retried. Checkpoints and account-registration requirements must be completed in the Muse app before signing in again.

## State

Credentials live in `muse-auth/.state/state.json`, with `0700` directory and `0600` file permissions. Writes are atomic. HTTP errors save the raw body privately; terminal output shows status, endpoint, and safe schema-error fields. OTP codes are not deliberately persisted by the client; raw error responses must still be treated as sensitive.

Use `--state-dir /path/to/private-state` before the command to isolate standalone auth work. The desktop bridge reads the default repo-local state directory.

While the executor is running, it rotates tokens and synchronizes the pair here. Stop it before auth `setup`, `mint`, or `refresh`, and avoid concurrent auth writers.

See the [protocol overview](../docs/protocol.md) for endpoint and token roles.
