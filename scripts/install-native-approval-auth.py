#!/usr/bin/env python3
"""Operator-run installation of the built native authentication helper.

Does not enable authentication policy or approve any action. Run with the
repository venv after `swift build --product CapDepApprovalAuth`.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

from capabledeputy.cli._managed_config import user_config_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path, help="Trusted built CapDepApprovalAuth binary")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("Native approval authentication requires macOS")
    source = args.binary.resolve(strict=True)
    if not source.is_file() or not os.access(source, os.X_OK):
        parser.error("Supply the trusted executable produced by the macOS build")
    directory = user_config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "CapDepApprovalAuth"
    temporary = directory / f".CapDepApprovalAuth-{os.getpid()}"
    shutil.copyfile(source, temporary)
    temporary.chmod(0o700)
    temporary.replace(destination)
    config = directory / "approval-auth.json"
    pending = directory / f".approval-auth-{os.getpid()}.json"
    with pending.open("x") as stream:
        pending.chmod(0o600)
        json.dump(
            {
                "helper_path": str(destination),
                "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            },
            stream,
        )
    pending.replace(config)
    print("Native approval helper installed. Authentication policy is unchanged.")


if __name__ == "__main__":
    main()
