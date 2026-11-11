#!/usr/bin/env python3
"""Fail if detect-secrets finds anything in tracked or staged files."""

import json
import subprocess
import sys

files = subprocess.run(
    ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
    capture_output=True,
    text=True,
    check=True,
).stdout.split()
out = subprocess.run(["detect-secrets", "scan", *files], capture_output=True, text=True, check=True)
results = json.loads(out.stdout)["results"]
for path, findings in results.items():
    for f in findings:
        print(f"secret? {path}:{f['line_number']} ({f['type']})", file=sys.stderr)
sys.exit(1 if results else 0)
