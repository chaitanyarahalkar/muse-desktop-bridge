#!/usr/bin/env python3
"""Connect this Mac to Muse using the existing local authentication state."""

from __future__ import annotations

import argparse
import asyncio
import copy
import fcntl
import json
import logging
import os
from pathlib import Path
import re
import select
import signal
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / ".runtime"
SOCKET = RUNTIME / "muse.sock"
AUTH_DIR = ROOT.parent / "muse-auth" / ".state"
TEST_COMMAND = "/usr/bin/uname -s"

sys.path.insert(0, str(ROOT.parent / "muse-auth"))
from muse_auth import Store  # noqa: E402
from musegadget import config  # noqa: E402
from musegadget import executor as sdk_executor  # noqa: E402
from musegadget import service as sdk_service  # noqa: E402
from musegadget.executor import Account, Executor  # noqa: E402
from musegadget.identity import Identity  # noqa: E402

log = logging.getLogger("muse.mac")


def prepare_runtime():
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    RUNTIME.chmod(0o700)
    if len(os.fsencode(SOCKET)) >= 104:
        raise RuntimeError("Executor directory path is too long for a macOS Unix socket.")
    os.environ["MUSEGADGET_STATE_DIR"] = str(RUNTIME)
    os.environ["MUSEGADGET_SOCKET"] = str(SOCKET)


def import_credentials():
    source = Store(AUTH_DIR)
    device = source.data.get("device", {})
    node = device.get("device_id", "")
    match = re.fullmatch(r"homelink-([0-9a-f]{6})", node)
    if not match or not device.get("access_token") or not device.get("refresh_token"):
        raise RuntimeError("Complete muse_auth.py setup first; a homelink device token pair is required.")
    suffix = match[1]
    mac = "02:00:00:" + ":".join(suffix[i:i + 2] for i in (0, 2, 4))
    identity = Identity(mac)
    if identity.node_id != node:
        raise RuntimeError("SDK identity does not match the token's device ID.")
    config.save_json(config.IDENTITY_FILE, {"mac": mac})
    config.save_json(config.PAIRING_FILE, {
        "access_token": device["access_token"],
        "refresh_token": device["refresh_token"],
        "token_type": "device",
        "api_url_v2": "https://api.muse.ai",
        "noise_host": sdk_service.DEFAULT_NOISE_HOST,
        "access_token_saved_at": device.get("access_token_saved_at", int(time.time())),
    })
    return identity, source.data.get("sdk_token")


def sync_rotated_pair(node_id, old_refresh, pairing):
    source = Store(AUTH_DIR)
    current = source.data.get("device", {})
    if current.get("device_id") != node_id or current.get("refresh_token") != old_refresh:
        raise RuntimeError("Authentication state changed concurrently; stop the executor before using CLI refresh/mint.")
    source.data["device"] = {
        "device_id": node_id,
        "access_token": pairing["access_token"],
        "refresh_token": pairing["refresh_token"],
        "access_token_saved_at": pairing["access_token_saved_at"],
    }
    source.save()


class ObservedExecutor(Executor):
    def __init__(self, account, loop):
        super().__init__(account)
        self.loop = loop
        self.test_future = None

    def run(self, command, params, timeout_ms=None):
        result = super().run(command, params, timeout_ms)
        if command == "system.run" and params.get("command", "").strip() == TEST_COMMAND:
            future = self.test_future
            if future is not None:
                def complete():
                    if not future.done():
                        future.set_result(result)
                self.loop.call_soon_threadsafe(complete)
        return result


class MacService(sdk_service.Service):
    async def _maybe_refresh(self, pairing, force=False):
        result = await super()._maybe_refresh(pairing, force)
        if result and result["refresh_token"] != pairing["refresh_token"]:
            sync_rotated_pair(self.identity.node_id, pairing["refresh_token"], result)
            log.info("rotated device tokens synchronized to the authentication CLI state")
        return result

    async def _local_request(self, line):
        request = json.loads(line)
        action = request.get("action") if isinstance(request, dict) else None
        if action == "status":
            return {"ok": True, "connected": bool(self._current and self._current.registered_at),
                    "pid": os.getpid(), "node_id": self.identity.node_id,
                    "display_name": self.display_name, "run_as": self.executor.account.name}
        if action == "stop":
            asyncio.get_running_loop().call_later(0.1, self.stop)
            return {"ok": True, "message": "Executor stop requested."}
        if action == "self_test":
            return await self.self_test()
        return await super()._local_request(line)

    async def self_test(self):
        session = self._current
        if session is None or session.registered_at is None:
            return {"ok": False, "error": "Executor is not registered yet."}
        if self.executor.test_future is not None:
            return {"ok": False, "error": "A command test is already running."}
        future = asyncio.get_running_loop().create_future()
        self.executor.test_future = future
        session_id = str(uuid.uuid4())
        message = (
            f"Please verify the newly connected computer '{self.display_name}' "
            f"(device ID {self.identity.node_id}). Use its system.run device command "
            f"to execute exactly `{TEST_COMMAND}` and report the output. This is a "
            "read-only connectivity test. Run it on that connected device, not in "
            "your cloud VM. Do not run any other commands or modify any files."
        )
        try:
            acknowledgement = await session.send_chat(message, session_id)
            if not acknowledgement.get("ok"):
                return {"ok": False, "error": "Muse did not accept the test message.",
                        "status": acknowledgement.get("status")}
            log.info("sent read-only executor test in side chat %s", session_id)
            try:
                result = await asyncio.wait_for(future, timeout=120)
            except asyncio.TimeoutError:
                return {"ok": False, "error": "Muse accepted the message but did not invoke the exact test command within 120 seconds.",
                        "session_id": session_id}
            payload = result.get("payload", {})
            proof = {
                "ok": bool(result.get("ok") and payload.get("exit_code") == 0
                           and payload.get("stdout", "").strip() == "Darwin"),
                "node_id": self.identity.node_id,
                "session_id": session_id,
                "command": TEST_COMMAND,
                "result": result,
                "timestamp": int(time.time()),
            }
            config.save_json("self-test.json", proof)
            return proof
        finally:
            self.executor.test_future = None


class ReadyHandler(logging.Handler):
    def __init__(self, fd):
        super().__init__()
        self.fd = fd

    def emit(self, record):
        if self.fd is not None and record.getMessage() == "registered with the Muse":
            try:
                os.write(self.fd, b"registered\n")
            finally:
                os.close(self.fd)
                self.fd = None


async def serve():
    identity, sdk_token = import_credentials()
    account = Account.current()
    # Keep the SDK's platform/device-family protocol values, but describe the
    # command environment accurately to Muse and include Homebrew in PATH.
    sdk_service.COMMAND_SPECS = copy.deepcopy(sdk_executor.COMMAND_SPECS)
    sdk_service.COMMAND_SPECS["system.run"]["description"] = (
        "Run a shell command on this macOS computer with /bin/bash; return stdout, "
        "stderr and exit code. macOS tools and Homebrew are available."
    )
    sdk_executor.SAFE_PATH = "/opt/homebrew/bin:/opt/homebrew/sbin:" + sdk_executor.SAFE_PATH
    loop = asyncio.get_running_loop()
    executor = ObservedExecutor(account, loop)
    service = MacService(identity=identity, executor=executor, sdk_token=sdk_token,
                         display_name=f"{account.name}'s Mac (Muse executor)")
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, service.stop)
    log.info("commands run as %s; device %s", account.name, identity.node_id)
    server = await service.serve_local(SOCKET)
    try:
        await service.run()
    finally:
        server.close()
        await server.wait_closed()


def request_local(request, timeout=10):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(SOCKET))
        client.sendall(json.dumps(request).encode() + b"\n")
        with client.makefile("rb") as reader:
            response = reader.readline(1024 * 1024)
        return json.loads(response)


def run(ready_fd=None):
    with (RUNTIME / "process.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("The executor is already running.") from None
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        if ready_fd is not None:
            logging.getLogger().addHandler(ReadyHandler(ready_fd))
        (RUNTIME / "pid").write_text(str(os.getpid()))
        try:
            asyncio.run(serve())
        finally:
            (RUNTIME / "pid").unlink(missing_ok=True)
            SOCKET.unlink(missing_ok=True)


def start():
    try:
        existing = request_local({"action": "status"})
    except (OSError, ValueError):
        pass
    else:
        return existing
    read_fd, write_fd = os.pipe()
    log_fd = os.open(RUNTIME / "executor.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "run", "--ready-fd", str(write_fd)],
            stdin=subprocess.DEVNULL, stdout=log_fd, stderr=log_fd,
            pass_fds=(write_fd,), start_new_session=True,
        )
    finally:
        os.close(write_fd)
        os.close(log_fd)
    try:
        readable, _, _ = select.select([read_fd], [], [], 60)
        ready = os.read(read_fd, 64) if readable else b""
    finally:
        os.close(read_fd)
    if ready.strip() == b"registered":
        return request_local({"action": "status"})
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    raise RuntimeError(f"Executor did not register. See {RUNTIME / 'executor.log'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("start", "status", "stop", "test"):
        sub.add_parser(command)
    p = sub.add_parser("run")
    p.add_argument("--ready-fd", type=int)
    p = sub.add_parser("send")
    p.add_argument("message")
    p.add_argument("--session-id")
    args = parser.parse_args()
    try:
        prepare_runtime()
        if args.command == "run":
            run(args.ready_fd)
            return 0
        if args.command == "start":
            response = start()
        elif args.command == "test":
            response = request_local({"action": "self_test"}, timeout=195)
        elif args.command == "send":
            request = {"message": args.message}
            if args.session_id:
                request["session_id"] = args.session_id
            response = request_local(request, timeout=90)
        else:
            response = request_local({"action": args.command})
        print(json.dumps(response, indent=2))
        return 0 if response.get("ok") else 1
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
