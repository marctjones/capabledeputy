#!/usr/bin/env python3
"""Automated native Mac email workflow test, using an isolated synthetic daemon.

Builds a separate .app with its own defaults and socket; never launches/restarts
production CapDep or contacts a mailbox. Requires macOS Accessibility permission.
Uses background AX value changes and button actions, without foreground activation,
keyboard events, mouse events, or raising windows.
Evidence contains only synthetic *.email-mvp.invalid messages.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import plistlib
import shutil
import socket
import subprocess
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "apps/macos/CapDep"
PROMPT = "Summarize my recent emails."
SUBJECT = "Re: Meeting agenda — GUI reviewed"
BODY = (
    "Thursday works for me.\n\n1. Review the agenda.\n2. Confirm the meeting time."
    "\n\nThanks,\nTest User"
)


def run(args, *, env=None, timeout=60):
    result = subprocess.run(
        args, cwd=ROOT, env=env, text=True, capture_output=True, timeout=timeout
    )
    if result.returncode:
        raise RuntimeError(f"Command failed: {args[0]}\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def literal(value):
    return (
        '"'
        + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
        + '"'
    )


class GUI:
    def __init__(self, pid, artifacts):
        self.pid = pid
        self.artifacts = artifacts
        self.background_checks = 0

    def script(self, code):
        result = run(
            [
                "osascript",
                "-e",
                f"""tell application "System Events"
set targetProcess to first application process whose unix id is {self.pid}
tell targetProcess
{code}
end tell
end tell""",
            ],
            timeout=45,
        )

        is_frontmost = run(
            [
                "osascript",
                "-e",
                'tell application "System Events" to get frontmost of '
                f"(first application process whose unix id is {self.pid})",
            ]
        )
        if is_frontmost == "true":
            raise AssertionError("Background test app must never become foreground")
        self.background_checks += 1
        return result

    def element(
        self, identifier, action="return value of targetElement", *, name=False, prefix=False
    ):
        selector = (
            f"(name of candidate as text is {literal(identifier)} or "
            f"description of candidate as text is {literal(identifier)})"
            if name
            else f'value of attribute "AXIdentifier" of candidate as text is {literal(identifier)}'
        )
        if prefix:
            selector = (
                'value of attribute "AXIdentifier" of candidate as text begins with '
                + literal(identifier)
            )
        return self.script(f"""set targetElement to missing value
repeat with currentWindow in windows
  set allElements to entire contents of currentWindow
  repeat with candidate in allElements
    try
      if {selector} then
        set targetElement to candidate
        exit repeat
      end if
    end try
  end repeat
  if targetElement is not missing value then exit repeat
end repeat
if targetElement is missing value then error "Missing control: {identifier}"
{action}""")

    def wait(self, identifier, *, name=False, prefix=False, timeout=40):
        deadline = time.monotonic() + timeout
        last = ""
        while time.monotonic() < deadline:
            try:
                self.element(identifier, "return role of targetElement", name=name, prefix=prefix)
                return
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                last = str(exc)
                time.sleep(0.3)
        raise AssertionError(last)

    def click(self, identifier, *, name=False, prefix=False):
        self.wait(identifier, name=name, prefix=prefix)
        deadline = time.monotonic() + 30
        while (
            self.element(identifier, "return enabled of targetElement", name=name, prefix=prefix)
            != "true"
        ):
            if time.monotonic() > deadline:
                raise AssertionError("Control stayed disabled: " + identifier)
            time.sleep(0.2)
        self.element(
            identifier, 'perform action "AXPress" of targetElement', name=name, prefix=prefix
        )

    def wait_text(self, text, *, name="waiting", timeout=40):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            tree = self.snapshot(name)
            if "AX read error:" not in tree and text in tree:
                return tree
            time.sleep(0.3)
        raise AssertionError("Visible text not found: " + text)

    def fill(self, identifier, value):
        self.wait(identifier)
        deadline = time.monotonic() + 30
        while True:
            try:
                self.element(identifier, f"set value of targetElement to {literal(value)}")
                observed = self.element(identifier)
                if observed == value:
                    return
            except RuntimeError:
                # SwiftUI can replace the AX element while committing another
                # field's binding. Only idempotent value changes are retried;
                # review/approval button actions are never replayed by the driver.
                if time.monotonic() >= deadline:
                    raise
            if time.monotonic() >= deadline:
                raise AssertionError("Accessible edit did not settle: " + identifier)
            time.sleep(0.2)

    def wait_value(self, identifier, expected, timeout=40):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.element(identifier) == expected:
                    return
            except (RuntimeError, subprocess.TimeoutExpired):
                pass  # The saved-version update replaces the editor's AX subtree.
            time.sleep(0.2)
        raise AssertionError("Expected accessible value did not appear: " + identifier)

    def snapshot(self, name):
        tree = self.script("""set output to "Window count: " & (count of windows as text) & linefeed
repeat with currentWindow in windows
 set output to output & "Window: " & (name of currentWindow as text) & linefeed
 set allElements to entire contents of currentWindow
 repeat with candidate in allElements
  try
   set output to output & (role of candidate as text) & " | "
   try
    set output to output & (value of attribute "AXIdentifier" of candidate as text) & " | "
   end try
   try
    set output to output & (name of candidate as text) & " | "
   end try
   try
    set output to output & (value of candidate as text)
   end try
   set output to output & linefeed
  on error detail
   set output to output & "AX read error: " & detail & linefeed
  end try
 end repeat
end repeat
return output""")
        (self.artifacts / (name + ".ax.txt")).write_text(tree)
        return tree


def wait_json(path, predicate, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            try:
                data = json.loads(path.read_text())
                if predicate(data):
                    return data
            except json.JSONDecodeError:
                pass
        time.sleep(0.2)
    raise AssertionError(f"Expected evidence was not recorded in {path}")


def rpc(socket_path, method, params=None):
    request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    token = Path(str(socket_path) + ".operator-token")
    if token.exists():
        request["auth"] = token.read_text().strip()
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(3)
        client.connect(str(socket_path))
        client.sendall(json.dumps(request).encode() + b"\n")
        response = b""
        while not response.endswith(b"\n"):
            chunk = client.recv(65536)
            if not chunk:
                raise RuntimeError("Daemon closed connection")
            response += chunk
    data = json.loads(response)
    if data.get("error"):
        raise RuntimeError(str(data["error"]))
    return data.get("result")


def wait_daemon(socket_path, process):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError("Synthetic daemon exited")
        try:
            rpc(socket_path, "ping")
            return
        except (OSError, RuntimeError):
            time.sleep(0.2)
    raise AssertionError("Synthetic daemon did not respond to RPC")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument(
        "--smtp-outcome",
        choices=["accepted", "failed_before_submission", "outcome_unknown"],
        default="accepted",
    )
    args = parser.parse_args()
    # System Events object references may resolve by process name. Serialize
    # native test drivers on a host, and give every test executable a unique
    # identity so AX actions cannot cross into another run or the user's app.
    driver_lock = Path(tempfile.gettempdir()) / f"capdep-email-gui-driver-{os.getuid()}.lock"
    with driver_lock.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        execute(args, parser)


def execute(args, parser):
    artifacts = args.artifacts or Path(tempfile.mkdtemp(prefix="capdep-email-gui-"))
    if artifacts.exists() and any(artifacts.iterdir()):
        parser.error("--artifacts must be an empty directory so evidence cannot mix between runs")
    artifacts.mkdir(parents=True, exist_ok=True)
    artifacts = artifacts.resolve()
    env = os.environ.copy()
    env.update(
        CLANG_MODULE_CACHE_PATH="/tmp/capdep-mvp-clang-cache",
        SWIFTPM_MODULECACHE_OVERRIDE="/tmp/capdep-mvp-swift-cache",
    )
    swift = [
        "swift",
        "build",
        "--disable-sandbox",
        "--package-path",
        str(PACKAGE),
        "--cache-path",
        "/tmp/capdep-mvp-spm-cache",
    ]
    if not args.skip_build:
        (artifacts / "build.log").write_text(run(swift, env=env, timeout=240))
    binary = Path(run([*swift, "--show-bin-path"], env=env)) / "CapDepMac"
    (artifacts / "build-manifest.json").write_text(
        json.dumps(
            {
                "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                "git_head": run(["git", "rev-parse", "HEAD"]),
                "working_tree_dirty": bool(run(["git", "status", "--porcelain"])),
                "background_test_mode": True,
            },
            indent=2,
        )
    )
    run_id = uuid4().hex[:10]
    app_name = "CapDepEmailMVPTest-" + run_id
    bundle_id = "local.capabledeputy.EmailMVPTest." + run_id
    bundle = artifacts / (app_name + ".app")
    macos = bundle / "Contents/MacOS"
    macos.mkdir(parents=True, exist_ok=True)
    executable = macos / app_name
    shutil.copy2(binary, executable)
    with (bundle / "Contents/Info.plist").open("wb") as handle:
        plistlib.dump(
            {
                "CFBundleExecutable": executable.name,
                "CFBundleIdentifier": bundle_id,
                "CFBundleName": app_name,
                "CFBundlePackageType": "APPL",
                "LSMinimumSystemVersion": "14.0",
                "NSHighResolutionCapable": True,
            },
            handle,
        )
    run(["codesign", "--force", "--sign", "-", str(bundle)])
    # Unix sockets have a short path limit, independent of artifact directory.
    socket_dir = Path(tempfile.mkdtemp(prefix="cdmvp-", dir="/tmp"))
    socket_path = socket_dir / "daemon.sock"
    state = artifacts / "daemon-state"
    state.mkdir(exist_ok=True)
    daemon_log = (artifacts / "daemon.log").open("w")
    app_log = (artifacts / "app.log").open("w")
    daemon = None
    app = None
    app_pid = None
    steps = []

    def start_daemon():
        return subprocess.Popen(
            [
                str(ROOT / ".venv/bin/python"),
                "scripts/email-mvp-test-daemon.py",
                "--socket",
                str(socket_path),
                "--state-dir",
                str(state),
                "--smtp-outcome",
                args.smtp_outcome,
            ],
            cwd=ROOT,
            stdout=daemon_log,
            stderr=subprocess.STDOUT,
        )

    try:
        daemon = start_daemon()
        wait_daemon(socket_path, daemon)
        gui_env = env | {
            "CAPDEP_SOCKET": str(socket_path),
            "CAPDEP_GUI_OWNS_DAEMON": "0",
            "CAPDEP_IDLE_SHUTDOWN_SECONDS": "off",
            "CAPDEP_GUI_BACKGROUND_TEST": "1",
        }
        gui_env.pop("CAPDEP_GUI_TEST_COMMAND_FILE", None)
        launch = [
            "open",
            "-n",
            "-g",
            "-W",
            "--stdout",
            str(artifacts / "app.log"),
            "--stderr",
            str(artifacts / "app.log"),
        ]
        for key in (
            "CAPDEP_SOCKET",
            "CAPDEP_GUI_OWNS_DAEMON",
            "CAPDEP_IDLE_SHUTDOWN_SECONDS",
            "CAPDEP_GUI_BACKGROUND_TEST",
        ):
            launch.extend(["--env", key + "=" + gui_env[key]])
        launch.extend([str(bundle), "--args", "-capdep.onboarding.completed.v1", "YES"])
        app = subprocess.Popen(launch, stdout=app_log, stderr=subprocess.STDOUT)
        app_pid = None
        deadline = time.monotonic() + 30
        while app_pid is None:
            try:
                app_pid = int(
                    run(
                        [
                            "osascript",
                            "-e",
                            'tell application "System Events" to get unix id '
                            "of first application process whose bundle identifier is "
                            + literal(bundle_id),
                        ]
                    )
                )
            except (RuntimeError, ValueError):
                if time.monotonic() > deadline:
                    raise AssertionError("Test app did not register with Launch Services") from None
                time.sleep(0.3)
        gui = GUI(app_pid, artifacts)
        gui.snapshot("launch")
        print("Launched isolated native test app", flush=True)
        gui.wait("capdep.chat.input", timeout=60)
        gui.fill("capdep.chat.input", PROMPT)
        gui.click("capdep.chat.send")
        steps.append("natural summary submitted using native controls")
        # The source buttons are the daemon's actual source association, not a test hook.
        summary = gui.wait_text("2. Bob", name="00-summary")
        assert "1. Alice" in summary and "complete coverage" in summary
        gui.click("capdep.email.open")
        gui.wait("capdep.email.reply.", prefix=True, timeout=60)
        gui.snapshot("01-sources")
        gui.click("capdep.email.reply.", prefix=True)
        gui.fill("capdep.email.subject", SUBJECT)
        gui.fill("capdep.email.body", BODY)
        gui.click("capdep.email.save")
        gui.wait_value("capdep.email.version", "Version 2 · Saved")
        gui.snapshot("02-edited-draft")
        steps.append("selected source and saved edited subject/body through native controls")
        gui.click("capdep.email.review")
        gui.wait("capdep.email.review-envelope")
        reviewed = gui.snapshot("03-exact-review")
        assert SUBJECT in reviewed and BODY in reviewed, reviewed
        assert "owner@email-mvp.invalid" in reviewed and "alice@email-mvp.invalid" in reviewed
        assert "Window: Approval" not in reviewed, reviewed
        assert gui.element("capdep.task-review.body") == BODY
        steps.append("exact account/recipient/content reviewed inside original task window")
        evidence_path = state / "evidence.json"
        gui.click("capdep.task-review.back")
        gui.wait("capdep.task-review.open.", prefix=True)
        assert json.loads(evidence_path.read_text())["smtp_attempt_count"] == 0
        gui.click("capdep.task-review.open.", prefix=True)
        gui.wait("capdep.task-review.edit")
        gui.click("capdep.task-review.edit")
        gui.fill("capdep.task-review.body-editor", BODY + "\nReviewed again.")
        gui.click("capdep.task-review.save")
        gui.wait_value("capdep.task-review.body", BODY + "\nReviewed again.")
        gui.wait_text("Changed since the previous review: body.", name="03-changed-fields")
        steps.append(
            "back kept consent pending; editing superseded it and produced a fresh exact review"
        )
        gui.click("capdep.email.deny")
        gui.wait_text("You chose not to send this reply", name="03-declined")
        assert json.loads(evidence_path.read_text())["smtp_attempt_count"] == 0
        gui.click("capdep.task-review.fresh")
        gui.wait_value("capdep.task-review.body", BODY + "\nReviewed again.")
        gui.click("capdep.email.approve")
        evidence = wait_json(evidence_path, lambda data: data.get("smtp_attempt_count", 0) == 1)
        expected_status = {
            "accepted": "Accepted by your mail service",
            "failed_before_submission": "Not sent",
            "outcome_unknown": "Send outcome unknown",
        }[args.smtp_outcome]
        gui.wait_text(expected_status, name="04-task-receipt")
        steps.append(
            "one deliberate task review submitted exactly once and kept its result in place"
        )
        attempt = evidence["smtp_attempts"][0]
        assert attempt["to"] == "alice@email-mvp.invalid", attempt
        assert attempt["from_addr"] == "owner@email-mvp.invalid", attempt
        assert attempt["subject"] == SUBJECT and attempt["body"] == BODY + "\nReviewed again.", (
            attempt
        )
        assert attempt["in_reply_to"] == "<agenda@email-mvp.invalid>", attempt
        gui.click("capdep.task-review.back")
        gui.wait_text(expected_status, name="04-conversation-receipt")
        daemon.terminate()
        daemon.wait(timeout=15)
        daemon = start_daemon()
        wait_daemon(socket_path, daemon)
        gui.wait_text(expected_status, name="05-recovered-task-receipt")
        evidence = json.loads(evidence_path.read_text())
        assert evidence.get("smtp_attempt_count") == 1, evidence
        steps.append("daemon restart retained task receipt without another send")
        if args.smtp_outcome == "outcome_unknown":
            # Existing explicit provider-evidence surface remains covered while
            # the task review renders the same authoritative reconciled result.
            gui.click("capdep.email.open")
            gui.click("capdep.email.reconcile.", prefix=True)
            gui.wait("capdep.email.reconciliation.identity")
            identity = gui.element("capdep.email.reconciliation.identity")
            assert SUBJECT in identity and attempt["message_id"] in identity, identity
            assert (
                gui.element("capdep.email.reconciliation.record", "return enabled of targetElement")
                == "false"
            )
            gui.click("capdep.email.reconciliation.accepted")
            gui.fill(
                "capdep.email.reconciliation.reason",
                "Synthetic provider ledger confirms this exact Message-ID was accepted.",
            )
            gui.click("capdep.email.reconciliation.confirm")
            gui.click("capdep.email.reconciliation.record")
            gui.wait_text("Operator verification: smtp accepted", name="06-provider-verification")
            assert json.loads(evidence_path.read_text())["smtp_attempt_count"] == 1
            gui.click("capdep.email.done")
            expected_status = "Accepted by your mail service"
            gui.wait_text(expected_status, name="06-reconciled-task-receipt")
            steps.append("provider reconciliation updated original task without retry")
        gui.fill("capdep.chat.input", "Show the Markdown rendering fixture.")
        gui.click("capdep.chat.send")
        rendered = gui.wait_text("Second ordered item", name="07-readable-transcript")
        for expected in (
            "Rendering Fixture",
            "First paragraph with bold text",
            "Second paragraph stays separate",
            "1. First ordered item.",
            "2. Second ordered item",
            "A quoted paragraph",
            "first code line\nsecond code line",
        ):
            assert expected in rendered, expected
        assert "First paragraph with bold text and emphasis.\n" in rendered
        assert "\nSecond paragraph stays separate" in rendered
        assert json.loads(evidence_path.read_text())["smtp_attempt_count"] == 1
        steps.append(
            "native transcript preserved paragraphs, heading/list boundaries, quote and code lines"
        )
        sessions = rpc(socket_path, "session.list")
        rows = sessions.get("sessions", []) if isinstance(sessions, dict) else sessions
        source_sessions = [row for row in rows if row.get("intent") == PROMPT]
        assert len(source_sessions) == 1, source_sessions
        source_session_id = source_sessions[0]["id"]
        previous_checks = gui.background_checks
        os.kill(app_pid, 15)
        app.wait(timeout=15)
        app_pid = None
        reopen_launch = list(launch)
        bundle_index = reopen_launch.index(str(bundle))
        reopen_launch[bundle_index:bundle_index] = [
            "--env",
            "CAPDEP_GUI_TEST_REOPEN_SESSION=" + source_session_id,
        ]
        app = subprocess.Popen(reopen_launch, stdout=app_log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 30
        while app_pid is None:
            try:
                app_pid = int(
                    run(
                        [
                            "osascript",
                            "-e",
                            'tell application "System Events" to get unix id '
                            "of first application process whose bundle identifier is "
                            + literal(bundle_id),
                        ]
                    )
                )
            except (RuntimeError, ValueError):
                if time.monotonic() >= deadline:
                    raise AssertionError("Restarted test app did not register") from None
                time.sleep(0.3)
        gui = GUI(app_pid, artifacts)
        gui.background_checks = previous_checks
        gui.wait_text("Second ordered item", name="08-app-reopened-transcript")
        gui.wait_text(expected_status, name="09-app-reopened-receipt")
        assert json.loads(evidence_path.read_text())["smtp_attempt_count"] == 1
        steps.append(
            "app restart reloaded persisted transcript and source-session receipt without resending"
        )
        result = {
            "result": "PASS",
            "driver": "background native macOS Accessibility",
            "foreground_activation": False,
            "background_checks": gui.background_checks,
            "keyboard_or_mouse_events": False,
            "smtp_outcome": args.smtp_outcome,
            "steps": steps,
            "evidence": evidence,
            "socket": str(socket_path),
            "real_mail_contacted": False,
            "reopen_session_selection": (
                "DEBUG setup calls production focusSession on an authorized daemon-listed session"
            ),
        }
        (artifacts / "result.json").write_text(json.dumps(result, indent=2))
        print(json.dumps({"result": "PASS", "artifacts": str(artifacts), "steps": steps}, indent=2))
    except Exception as exc:
        if app and app.poll() is None:
            with suppress(Exception):
                GUI(app_pid, artifacts).snapshot("failure")
        (artifacts / "result.json").write_text(
            json.dumps({"result": "FAIL", "error": str(exc), "steps": steps}, indent=2)
        )
        raise
    finally:
        if app and app_pid:
            with suppress(ProcessLookupError):
                os.kill(app_pid, 15)
        for process in (app, daemon):
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        daemon_log.close()
        app_log.close()
        shutil.rmtree(socket_dir)


if __name__ == "__main__":
    main()
