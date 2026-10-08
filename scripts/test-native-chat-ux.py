#!/usr/bin/env python3
"""Native chat/session/streaming AX acceptance with a synthetic isolated daemon.

Never activates the app, sends global keyboard/mouse events, loads model weights,
restarts production, or contacts mail providers. Clipboard evidence is synthetic.
Offscreen TextKit visual/selection tests are a separate Swift gate.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
import plistlib
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "email_gui_helpers", ROOT / "scripts/test-email-mvp-gui.py"
)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
GUI, run, literal, rpc = helper.GUI, helper.run, helper.literal, helper.rpc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument(
        "--scroll-only",
        action="store_true",
        help="Run only streaming, reading position, cancellation and restart checks",
    )
    parser.add_argument(
        "--inspection-marker",
        type=Path,
        help="Pause on the synthetic fixture until MARKER.continue exists (maximum five minutes)",
    )
    args = parser.parse_args()
    driver_lock = Path(tempfile.gettempdir()) / f"capdep-email-gui-driver-{os.getuid()}.lock"
    with driver_lock.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        execute(args, parser)


def execute(args, parser):
    if args.artifacts.exists() and any(args.artifacts.iterdir()):
        parser.error("Use an empty evidence directory")
    artifacts = args.artifacts.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    package = ROOT / "apps/macos/CapDep"
    swift = [
        "swift",
        "build",
        "--disable-sandbox",
        "--package-path",
        str(package),
        "--cache-path",
        "/tmp/capdep-native-spm-cache",
    ]
    (artifacts / "build.log").write_text(run(swift, timeout=240))
    binary = Path(run([*swift, "--show-bin-path"])) / "CapDepMac"
    sources = sorted(package.rglob("*.swift"))
    sources = [path for path in sources if ".build" not in path.parts]
    build_identity = {
        "git_head": run(["git", "rev-parse", "HEAD"]),
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "source_sha256": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sources
        },
        "working_tree_dirty": bool(run(["git", "status", "--porcelain"])),
    }
    (artifacts / "build-manifest.json").write_text(json.dumps(build_identity, indent=2))
    run_id = uuid4().hex[:10]
    name = "CapDepNativeUXTest-" + run_id
    bundle_id = "local.capabledeputy.NativeUXTest." + run_id
    bundle = artifacts / (name + ".app")
    executable = bundle / "Contents/MacOS" / name
    executable.parent.mkdir(parents=True)
    shutil.copy2(binary, executable)
    with (bundle / "Contents/Info.plist").open("wb") as file:
        plistlib.dump(
            {
                "CFBundleExecutable": name,
                "CFBundleIdentifier": bundle_id,
                "CFBundleName": name,
                "CFBundlePackageType": "APPL",
                "LSMinimumSystemVersion": "14.0",
                "NSHighResolutionCapable": True,
            },
            file,
        )
    run(["codesign", "--force", "--sign", "-", str(bundle)])
    socket_path = Path(tempfile.mkdtemp(prefix="cdnative-", dir="/tmp")) / "daemon.sock"
    log = (artifacts / "daemon.log").open("w")
    daemon = subprocess.Popen(
        [
            str(ROOT / ".venv/bin/python"),
            "scripts/native-chat-test-daemon.py",
            "--socket",
            str(socket_path),
            "--state-dir",
            str(artifacts / "state"),
        ],
        cwd=ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    app = None
    app_pid = None
    steps = []

    def launch():
        command = ["open", "-n", "-g", "-W"]
        for key, value in {
            "CAPDEP_SOCKET": str(socket_path),
            "CAPDEP_GUI_OWNS_DAEMON": "0",
            "CAPDEP_IDLE_SHUTDOWN_SECONDS": "off",
            "CAPDEP_GUI_BACKGROUND_TEST": "1",
        }.items():
            command += ["--env", key + "=" + value]
        command += [str(bundle), "--args", "-capdep.onboarding.completed.v1", "YES"]
        launched = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                pid = int(
                    run(
                        [
                            "osascript",
                            "-e",
                            'tell application "System Events" to get unix id of first '
                            "application process whose bundle identifier is " + literal(bundle_id),
                        ]
                    )
                )
                return launched, pid, GUI(pid, artifacts)
            except (RuntimeError, ValueError):
                time.sleep(0.2)
        raise AssertionError("Isolated app launch failed")

    def sessions():
        rows = rpc(socket_path, "session.list")
        return rows["sessions"] if isinstance(rows, dict) else rows

    def choose(gui, session_id):
        gui.click("capdep.chat.browse-conversations")
        gui.click("capdep.chat.conversation." + session_id)
        gui.wait("capdep.chat.input")

    def scroll_value(gui, value=None):
        return gui.script(
            """set scrollbar to missing value
set scrollbarCount to 0
repeat with currentWindow in windows
 set allElements to entire contents of currentWindow
 repeat with candidate in allElements
  try
   if (role of candidate as text) is "AXScrollBar" then
    set scrollbar to candidate
    set scrollbarCount to scrollbarCount + 1
   end if
  end try
 end repeat
end repeat
if scrollbarCount is not 1 then
 error ("Expected exactly one transcript scrollbar; got " & scrollbarCount)
end if
"""
            + (f"set value of scrollbar to {value}\n" if value is not None else "")
            + "return value of scrollbar"
        )

    try:
        helper.wait_daemon(socket_path, daemon)
        app, app_pid, gui = launch()
        if not args.scroll_only:
            gui.fill("capdep.chat.input", "[fixture]")
            gui.click("capdep.chat.send")
            fixture_ax = gui.wait_text("Final paragraph.", name="01-fixture")
            assert (
                "4. First authored item" in fixture_ax and "7. Second authored item" in fixture_ax
            )
            assert "Alice" in fixture_ax and "☑" in fixture_ax
            first = sessions()[0]["id"]
            gui.click("capdep.chat.copy-response-markdown")
            copied = subprocess.run(["pbpaste"], check=True, capture_output=True, text=True).stdout
            expected = (
                "Native fixture follows.\n\n"
                + (ROOT / "tests/fixtures/commonmark/native-semantic.md").read_text()
            )
            assert copied == expected.rstrip("\n"), "Copy Markdown changed the original response"
            (artifacts / "copied-markdown.txt").write_text(copied)
            steps.append("native semantic fixture AX and response Markdown copy")
            if args.inspection_marker:
                marker = args.inspection_marker.resolve()
                marker.write_text(json.dumps({"bundle": str(bundle), "pid": app_pid}, indent=2))
                deadline = time.monotonic() + 300
                while not Path(str(marker) + ".continue").exists():
                    if time.monotonic() > deadline:
                        raise AssertionError("Native fixture inspection pause timed out")
                    time.sleep(0.2)

            gui.fill("capdep.chat.input", "draft for first conversation")
            gui.click("capdep.chat.new-session")
            deadline = time.monotonic() + 20
            while len(sessions()) < 2:
                if time.monotonic() > deadline:
                    raise AssertionError("New conversation was not created")
                time.sleep(0.2)
            second = next(row["id"] for row in sessions() if row["id"] != first)
            gui.fill("capdep.chat.input", "draft for second conversation")
            choose(gui, first)
            gui.wait_value("capdep.chat.input", "draft for first conversation")
            choose(gui, second)
            gui.wait_value("capdep.chat.input", "draft for second conversation")
            choose(gui, first)
            gui.wait_text("Final paragraph.", name="02-reopened-history")
            steps.append("native create/switch/reopen and independent conversation drafts")
        gui.fill("capdep.chat.input", "[long]")
        gui.click("capdep.chat.send")
        gui.wait_text("Long transcript finished.", name="03-long-history")
        first = sessions()[0]["id"]
        scroll_value(gui, 0)
        gui.wait("capdep.chat.jump-latest")
        gui.fill("capdep.chat.input", "[stream]")
        gui.click("capdep.chat.send")
        gui.wait_text("Streaming started.", name="04-streaming")
        assert (
            gui.element("capdep.chat.browse-conversations", "return enabled of targetElement")
            == "false"
        )
        before = float(scroll_value(gui))
        time.sleep(2)
        after = float(scroll_value(gui))
        assert abs(after - before) < 0.05, (before, after)
        gui.wait("capdep.chat.jump-latest")
        gui.click("capdep.chat.cancel-turn")
        gui.wait_text("Interrupted", name="05-cancelled")
        gui.click("capdep.chat.jump-latest")
        time.sleep(0.3)
        assert float(scroll_value(gui)) > 0.9
        steps.append(
            "reading earlier while streaming retained position; cancel and explicit follow"
        )
        os.kill(app_pid, signal.SIGTERM)
        app.wait(timeout=15)
        app, app_pid, gui = launch()
        choose(gui, first)
        gui.wait_text("Long transcript finished.", name="06-app-restored-history")
        steps.append("app restart and daemon-persisted conversation restored via native browser")
        (artifacts / "manifest.json").write_text(
            json.dumps(
                {
                    "ok": True,
                    "synthetic": True,
                    "git_head": run(["git", "rev-parse", "HEAD"]),
                    "steps": steps,
                    "background_ax_checks": gui.background_checks,
                    "keyboard_events": False,
                    "model_weights": False,
                },
                indent=2,
            )
        )
        print(json.dumps({"ok": True, "artifacts": str(artifacts), "steps": steps}, indent=2))
    finally:
        if app_pid:
            with contextlib.suppress(ProcessLookupError):
                os.kill(app_pid, signal.SIGTERM)
        if app:
            try:
                app.wait(timeout=15)
            except subprocess.TimeoutExpired:
                app.terminate()
        daemon.terminate()
        daemon.wait(timeout=15)
        log.close()
        shutil.rmtree(socket_path.parent, ignore_errors=True)
        run(["defaults", "delete", bundle_id])


if __name__ == "__main__":
    main()
