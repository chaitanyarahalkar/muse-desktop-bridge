#!/usr/bin/env python3
"""Local Muse OTP login and gadget-token client, reconstructed from APK/SDK."""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import urllib.error
import urllib.request
import uuid

AUTH_BASE = "https://hatch-api.meta.ai"
DEVICE_BASE = "https://api.muse.ai"
DEFAULT_STATE = Path(__file__).resolve().parent / ".state"


class FlowError(Exception):
    pass


class ApiError(FlowError):
    def __init__(self, status, body):
        self.status = status
        self.body = body
        self.path = None
        # Server strings may echo credentials; keep raw bodies out of stdout.
        super().__init__(f"HTTP {status}; the response is in the private state file.")

    def __str__(self):
        message = f"HTTP {self.status}"
        if self.path:
            message += f" at {self.path}"
        if isinstance(self.body, dict) and self.body.get("title") == "JSON Schema Validation Error":
            detail = self.body.get("detail")
            if isinstance(detail, str):
                field = re.search(r"JSON field '([A-Za-z_][A-Za-z0-9_]*)'", detail)
                expected = re.search(r"Expected type '(string|number|integer|boolean|object|array|null)'", detail)
                if field and expected:
                    message += f": JSON field '{field[1]}' must have type '{expected[1]}'"
        return message + "; the response is in the private state file."


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Http:
    def request(self, base, path, body=None, bearer=None):
        if base not in (AUTH_BASE, DEVICE_BASE) or not path.startswith("/"):
            raise FlowError("Unexpected API target.")
        headers = {
            "Accept": "application/json",
            "X-API-Version": "1.0.0",
            "User-Agent": "MuseAuthLocal/0.1",
        }
        if bearer:
            headers["Authorization"] = "Bearer " + bearer
        encoded = None
        if body is not None:
            encoded = json.dumps(body).encode()
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(
            base + path, data=encoded, headers=headers,
            method="POST" if body is not None else "GET",
        )
        try:
            try:
                response = urllib.request.build_opener(NoRedirect()).open(request, timeout=30)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                status = response.code
                raw = response.read(1024 * 1024 + 1)
        except (urllib.error.URLError, OSError):
            raise FlowError("Network request failed; no automatic retry was made.") from None
        if len(raw) > 1024 * 1024:
            raise FlowError("API response exceeded 1 MiB.")
        try:
            data = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            data = {"non_json_response": True}
        if not 200 <= status < 300:
            raise ApiError(status, data)
        if not isinstance(data, dict):
            raise FlowError("API response was not a JSON object.")
        return data


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).expanduser()
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.directory.chmod(0o700)
        self.path = self.directory / "state.json"
        if self.path.is_symlink():
            raise FlowError("State file must not be a symlink.")
        if self.path.exists():
            self.path.chmod(0o600)
            self.data = json.loads(self.path.read_text())
            if not isinstance(self.data, dict) or self.data.get("schema") != 1:
                raise FlowError("Unrecognized state file.")
        else:
            self.data = {
                "schema": 1,
                "phone_device_id": str(uuid.uuid4()),
                "gadget_node_id": "homelink-" + secrets.token_hex(3),
            }
            self.save()

    def save(self):
        fd, temporary = tempfile.mkstemp(prefix="state-", dir=self.directory)
        try:
            with os.fdopen(fd, "w") as file:
                json.dump(self.data, file, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def present(value):
    return isinstance(value, str) and bool(value.strip())


def token_pair(response):
    value = response.get("payload", response)
    if not isinstance(value, dict) or not all(
        present(value.get(key)) for key in ("access_token", "refresh_token")
    ):
        raise FlowError("Response did not contain a complete token pair; existing pair retained.")
    return {key: value[key] for key in ("access_token", "refresh_token")}


class Client:
    def __init__(self, store, http=None):
        self.store = store
        self.state = store.data
        self.http = http or Http()

    def call(self, base, path, body=None, bearer=None):
        try:
            result = self.http.request(base, path, body, bearer)
        except ApiError as exc:
            exc.path = path
            self.state["last_error"] = {"path": path, "status": exc.status, "body": exc.body}
            self.store.save()
            raise
        self.state.pop("last_error", None)
        return result

    def flow_fields(self):
        flow_id = self.state.get("waterfall_id")
        if not flow_id:
            raise FlowError("Run start or login first.")
        return {"device_id": self.state["phone_device_id"], "waterfall_id": flow_id}

    def temp_token(self):
        value = self.state.get("login", {}).get("temp_token")
        if not present(value):
            raise FlowError("No temporary login token. Run start or login first.")
        return value

    def auth_call(self, action, fields):
        return self.call(AUTH_BASE, "/hatch/auth/" + action,
                         {**self.flow_fields(), **fields}, self.temp_token())

    def start(self):
        flow_id = str(uuid.uuid4())
        body = {"device_id": self.state["phone_device_id"], "waterfall_id": flow_id}
        if self.state.get("id_for_aymh"):
            body["id_for_aymh"] = self.state["id_for_aymh"]
        result = self.call(AUTH_BASE, "/hatch/auth/start", body)
        if not present(result.get("access_token")):
            raise FlowError("Bootstrap returned no temporary access token.")
        self.state["waterfall_id"] = flow_id
        self.state["login"] = {"temp_token": result["access_token"], "phase": "started"}
        if present(result.get("id_for_aymh")):
            self.state["id_for_aymh"] = result["id_for_aymh"]
        self.store.save()

    def options(self):
        result = self.auth_call("login_options", {})
        self.state["login"]["options"] = result
        self.store.save()
        return result

    def send_otp(self, contact, region=None):
        if not contact.strip():
            raise FlowError("An email address or international phone number is required.")
        login = self.state.get("login", {})
        fields = {"contact_point": contact.strip()}
        # The server rejects JSON null. The Android request model allows a
        # missing region hint; omit it unless supplied.
        region = region.strip().upper() if present(region) else None
        if region:
            fields["region"] = region
        if login.get("contact_point") == contact.strip() and login.get("auth_state"):
            fields["auth_state"] = login["auth_state"]
        result = self.auth_call("send_otp", fields)
        login["otp_response"] = result
        if not present(result.get("auth_state")):
            login["phase"] = "additional_login_step_required"
            self.state["login"] = login
            self.store.save()
            raise FlowError("OTP response did not contain auth_state; response saved privately.")
        login.update(contact_point=contact.strip(), region=region,
                     auth_state=result["auth_state"], phase="otp_sent", otp_response=result)
        login.pop("pending", None)
        self.state["login"] = login
        self.store.save()
        return result

    def confirm_otp(self, code):
        if not present(code):
            raise FlowError("Verification code must be nonempty.")
        login = self.state.get("login", {})
        if not present(login.get("auth_state")):
            raise FlowError("Send an OTP first.")
        result = self.auth_call("confirm_otp", {
            "auth_state": login["auth_state"], "otp_code": code.strip(),
            "supports_account_selection": True,
        })
        return self.accept_credentials(result)

    def accept_credentials(self, result):
        login = self.state.setdefault("login", {})
        login["pending"] = result
        if result.get("checkpoint_required"):
            phase = "checkpoint_required"
        elif result.get("account_selection_required"):
            phase = "account_selection_required"
        elif result.get("two_factor_required"):
            phase = "two_factor_required"
        elif any(result.get(key) for key in
                 ("requires_birthday", "requires_name", "requires_consent")):
            phase = "registration_completion_required"
        elif present(result.get("abra_access_token")) and present(result.get("abra_user_id")):
            self.state["session"] = {
                "access_token": result["abra_access_token"],
                "user_id": result["abra_user_id"],
                "frl_access_token": result.get("frl_access_token", ""),
                "frl_account_id": result.get("frl_account_id", ""),
            }
            self.state["login"] = {"phase": "authenticated"}
            self.store.save()
            return "authenticated"
        else:
            phase = "additional_login_step_required"
        login["phase"] = phase
        self.store.save()
        return phase

    def select_account(self, account_ref):
        pending = self.state.get("login", {}).get("pending", {})
        if not pending.get("account_selection_required") or not pending.get("selection_state"):
            raise FlowError("No account-selection challenge is pending.")
        if account_ref not in [item.get("account_ref") for item in pending.get("accounts", [])]:
            raise FlowError("Account reference was not offered by the server.")
        return self.accept_credentials(self.auth_call("select_account", {
            "selection_state": pending["selection_state"], "account_ref": account_ref,
        }))

    def two_factor(self, method_id, code=None):
        if code is not None and not present(code):
            raise FlowError("Two-factor code must be nonempty.")
        pending = self.state.get("login", {}).get("pending", {})
        if not pending.get("two_factor_required") or not pending.get("two_factor_state"):
            raise FlowError("No two-factor challenge is pending.")
        if method_id not in [item.get("method_id") for item in pending.get("two_factor_methods", [])]:
            raise FlowError("Two-factor method was not offered by the server.")
        fields = {"two_factor_state": pending["two_factor_state"], "method_id": method_id}
        if code is None:
            self.auth_call("two_factor/send_code", fields)
            self.store.save()
            return "two_factor_code_sent"
        fields["code"] = code.strip()
        return self.accept_credentials(self.auth_call("two_factor", fields))

    def exchange_frl(self, frl_token):
        if not present(frl_token):
            raise FlowError("FRL token must be nonempty.")
        result = self.call(AUTH_BASE, "/hatch/login",
                           {"frl_access_token": frl_token}, frl_token)
        user = result.get("abra_user_id") or result.get("user_id")
        if not present(result.get("access_token")) or not user:
            raise FlowError("FRL exchange returned incomplete session credentials.")
        self.state["session"] = {"access_token": result["access_token"],
                                 "user_id": str(user), "frl_access_token": frl_token}
        self.state["login"] = {"phase": "authenticated"}
        self.store.save()

    def mint(self, sdk_token, node_id=None):
        bearer = self.state.get("session", {}).get("access_token")
        if not present(bearer) or bearer.startswith("mgst_"):
            raise FlowError("Minting requires an authenticated Muse session token.")
        if not re.fullmatch(r"mgst_[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]", sdk_token):
            raise FlowError("SDK token does not match the published format.")
        node = (node_id or self.state["gadget_node_id"]).strip().lower()
        if not node:
            raise FlowError("Gadget node ID must be nonempty.")
        result = self.call(DEVICE_BASE, "/device_token/mint",
                           {"device_id": node, "sdk_token": sdk_token}, bearer)
        pair = token_pair(result)
        self.state.update(gadget_node_id=node, sdk_token=sdk_token,
                          device={**pair, "device_id": node})
        self.store.save()

    def refresh(self):
        device = self.state.get("device", {})
        refresh = device.get("refresh_token", "")
        if not present(refresh) or not device.get("device_id"):
            raise FlowError("No device token pair. Complete login and mint first.")
        raw = refresh.rsplit(":", 1)[-1]
        if not raw:
            raise FlowError("Device refresh token is empty.")
        body = {"device_id": device["device_id"]}
        if self.state.get("sdk_token"):
            body["sdk_token"] = self.state["sdk_token"]
        result = self.call(DEVICE_BASE, "/device_token/refresh", body,
                           "hatch_refresh:" + raw)
        pair = token_pair(result)
        self.state["device"] = {**pair, "device_id": device["device_id"]}
        self.store.save()

    def vms(self):
        bearer = self.state.get("device", {}).get("access_token")
        if not present(bearer):
            raise FlowError("No device access token. Complete login and mint first.")
        result = self.call(DEVICE_BASE, "/fetch_vms", bearer=bearer)
        if not isinstance(result.get("vm_list"), list):
            raise FlowError("VM lookup returned no vm_list.")
        self.state["vms"] = result["vm_list"]
        self.store.save()
        return len(result["vm_list"])

    def summary(self):
        return {
            "login_phase": self.state.get("login", {}).get("phase", "not_started"),
            "has_user_session": present(self.state.get("session", {}).get("access_token")),
            "has_device_pair": all(present(self.state.get("device", {}).get(key))
                                   for key in ("access_token", "refresh_token")),
            "gadget_node_id": self.state["gadget_node_id"],
            "stored_vm_count": len(self.state.get("vms", [])),
            "state_file": str(self.store.path),
        }


def choose(items, label):
    if not items:
        raise FlowError("Server supplied no choices for the pending challenge.")
    for index, item in enumerate(items, 1):
        print(f"{index}. {label(item)}")
    try:
        index = int(input("Choose a number: ")) - 1
    except ValueError:
        raise FlowError("Expected a number.") from None
    if not 0 <= index < len(items):
        raise FlowError("Choice is out of range.")
    return items[index]


def complete_login(client, phase):
    while phase in ("account_selection_required", "two_factor_required"):
        pending = client.state["login"]["pending"]
        if phase == "account_selection_required":
            account = choose(pending.get("accounts", []), lambda item: ", ".join(
                str(profile.get("display_name", "Account")) for profile in item.get("profiles", [])))
            phase = client.select_account(account["account_ref"])
        else:
            method = choose(pending.get("two_factor_methods", []),
                            lambda item: item.get("label") or item.get("method_type", "Method"))
            if method.get("method_type") == "SMS":
                client.two_factor(method["method_id"])
            phase = client.two_factor(method["method_id"], getpass.getpass("Two-factor code: "))
    if phase != "authenticated":
        raise FlowError(f"Login requires {phase}. Complete that step in the Muse app; "
                        "the server response is saved privately. No device mint was attempted.")
    print("Muse user authentication completed.")


def login(client, region):
    contact = input("Muse account email or phone (+country code): ").strip()
    client.start()
    client.options()
    client.send_otp(contact, region)
    print("Verification code requested.")
    complete_login(client, client.confirm_otp(getpass.getpass("Verification code: ")))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "options", "status", "refresh", "vms", "confirm-otp", "exchange-frl"):
        sub.add_parser(name)
    for name in ("login", "send-otp", "setup"):
        p = sub.add_parser(name)
        p.add_argument("--region", help="Optional country/region hint, e.g. US")
        if name == "setup":
            p.add_argument("--device-id", help="Stable gadget node ID; defaults to a generated one")
    p = sub.add_parser("mint")
    p.add_argument("--device-id")
    p = sub.add_parser("select-account")
    p.add_argument("account_ref")
    p = sub.add_parser("two-factor")
    p.add_argument("method_id")
    p.add_argument("--send-code", action="store_true")
    args = parser.parse_args(argv)
    try:
        client = Client(Store(args.state_dir))
        if args.command == "start":
            client.start()
            print("Bootstrap succeeded; temporary login bearer saved privately.")
        elif args.command == "options":
            result = client.options()
            print("Login options fetched; response field names:", ", ".join(sorted(result)))
        elif args.command in ("login", "setup"):
            login(client, args.region)
        elif args.command == "send-otp":
            client.send_otp(input("Email or phone (+country code): "), args.region)
            print("Verification code requested.")
        elif args.command == "confirm-otp":
            complete_login(client, client.confirm_otp(getpass.getpass("Verification code: ")))
        elif args.command == "select-account":
            complete_login(client, client.select_account(args.account_ref))
        elif args.command == "two-factor":
            code = None if args.send_code else getpass.getpass("Two-factor code: ")
            phase = client.two_factor(args.method_id, code)
            if not args.send_code:
                complete_login(client, phase)
        elif args.command == "exchange-frl":
            client.exchange_frl(getpass.getpass("Meta FRL access token: ").strip())
            print("Muse session token saved privately.")
        elif args.command == "refresh":
            client.refresh()
            print("Replacement device token pair saved.")
        elif args.command == "vms":
            print(f"Fetched {client.vms()} VM(s); credentials saved privately.")
        if args.command in ("mint", "setup"):
            sdk = os.environ.get("MUSE_SDK_TOKEN") or client.state.get("sdk_token")
            sdk = sdk or getpass.getpass("Gadget SDK token: ")
            client.mint(sdk.strip(), args.device_id)
            print("Device access and refresh tokens minted and saved privately.")
        print(json.dumps(client.summary(), indent=2))
        return 0
    except (FlowError, ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nStopped; completed steps remain in the private state file.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
