# macOS executor

Complete installation and authentication in the [root quick start](../README.md#quick-start), then use these commands from the repository root:

```sh
./muse-executor/muse-executor start
./muse-executor/muse-executor status
./muse-executor/muse-executor test
./muse-executor/muse-executor stop
```

`start` launches a background process and waits up to 60 seconds for registration. `run` instead stays in the foreground. Stop the background process before using `run`. No reboot/login autostart is installed.

## Messaging

```sh
./muse-executor/muse-executor send 'Hello from my connected Mac'
./muse-executor/muse-executor send 'Continue the task' --session-id YOUR_SESSION_ID
```

The output is a delivery acknowledgement. Muse's reply appears in the app. `test` creates a side chat asking Muse to run `/usr/bin/uname -s` on the connected Mac, and checks the executor result for `Darwin` and exit code 0.

## Execution environment

Commands run under the macOS account starting the bridge. The SDK exposes `system.run`, `file.read`, `file.write`, and `device.health`. Shell commands use `/bin/bash`, default to the user's home directory, and include Homebrew in PATH. They have the same filesystem permissions as that account.

The name is generated from the account: `<username>'s Mac (Muse executor)`. The SDK's supported `linux`/`homehub` wire metadata is retained; descriptions tell Muse that the actual environment is macOS.

## Runtime state

`.runtime/` contains private pairing data, identity, the Unix socket, process lock, PID, logs, and test results. It is excluded from Git. View logs locally:

```sh
tail -f muse-executor/.runtime/executor.log
```

The bridge imports device credentials from `../muse-auth/.state/state.json`. Its automatic token rotations are synchronized back there. Stop this executor before manually running auth `setup`, `mint`, or `refresh`.

macOS Unix socket paths must be shorter than 104 bytes. If startup reports a path-length error, place the checkout in a shorter path before signing in.

Use [AGENTS.md](../AGENTS.md) for development checks and [third-party notices](../THIRD_PARTY_NOTICES.md) for SDK provenance.
