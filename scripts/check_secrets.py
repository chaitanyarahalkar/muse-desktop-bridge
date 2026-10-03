#!/usr/bin/env python3
"""Check staged Git blobs for private artifacts and common credential formats.

Reports file names and rule names only; never prints a matched credential.
Run from any directory in the repository. This is a narrow guard, not a
replacement for reviewing a diff or running a general-purpose secret scanner.
"""

import math
from pathlib import PurePosixPath
import re
import subprocess
import sys


BLOCKED_DIRS = {".state", ".runtime", ".venv", "venv", "__pycache__", "decompiled", "evidence"}
BLOCKED_NAMES = {"state.json", "pairing.json", "pairing.json.tmp", "identity.json",
                 "sdk_token", "self-test.json", "credentials.json", ".DS_Store"}
BLOCKED_SUFFIXES = {".apk", ".aab", ".dex", ".har", ".pem", ".key", ".p12", ".pfx",
                    ".log", ".sock", ".pid", ".pyc"}
PATTERNS = {
    "JWT": rb"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    "GitHub token": rb"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})\b",
    "AWS access key": rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    "private key": rb"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----",
    "credential in URL": rb"https?://[^\s/:@]+:[^\s/@]+@",
    "local home path": rb"/(?:Users|home)/[A-Za-z0-9_.-]+/",
}
SDK_PATTERN = re.compile(rb"\bmgst_([A-Za-z0-9_-]{43})\b")


def git(*args):
    return subprocess.check_output(["git", *args])


def entropy(value):
    return -sum((value.count(char) / len(value)) * math.log2(value.count(char) / len(value))
                for char in set(value))


def main():
    root = git("rev-parse", "--show-toplevel").decode().strip()
    paths = git("-C", root, "ls-files", "--cached", "-z").split(b"\0")
    issues = []
    count = 0
    for raw in paths:
        if not raw:
            continue
        path = raw.decode("utf-8")
        parts = PurePosixPath(path)
        name = parts.name
        count += 1
        if (set(parts.parts) & BLOCKED_DIRS or name in BLOCKED_NAMES
                or parts.suffix in BLOCKED_SUFFIXES or name == ".env" or name.startswith(".env.")
                or name.startswith("state-")
                or (name.startswith("credentials") and name.endswith(".json"))
                or ("token" in name.lower() and name.endswith(".json"))):
            issues.append((path, "private/generated artifact"))
            continue
        data = git("-C", root, "show", ":" + path)
        for label, pattern in PATTERNS.items():
            if re.search(pattern, data):
                issues.append((path, label))
        # Low-entropy all-A / repeated-character public test fixtures are synthetic.
        if any(entropy(match[1]) >= 3.5 for match in SDK_PATTERN.finditer(data)):
            issues.append((path, "Muse SDK token"))
    if issues:
        for path, label in issues:
            print(f"BLOCKED: {path}: {label}", file=sys.stderr)
        return 1
    print(f"Credential guard passed for {count} staged/tracked files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
