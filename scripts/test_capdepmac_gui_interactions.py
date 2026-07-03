#!/usr/bin/env python3
"""macOS-sensitive CapDepMac GUI interaction smoke.

This is intentionally outside the default deterministic tier. It uses
System Events accessibility automation to prove that the packaged app opens,
the primary chat controls are discoverable, and a prompt can be typed and
submitted through the GUI.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "apps/macos/CapDep"
LAUNCHER = APP_ROOT / "scripts/run-local-app.sh"
CHAT_TRACE = Path.home() / "Library/Logs/CapDep/chat-trace.log"


class SmokeError(RuntimeError):
    pass


def run(
    cmd: list[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> str:
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        raise SmokeError(
            f"command timed out after {timeout:.0f}s: {' '.join(cmd)}\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}",
        ) from exc
    if proc.returncode != 0:
        raise SmokeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}",
        )
    return proc.stdout


def osascript(script: str) -> str:
    return run(["osascript", "-e", script], timeout=10.0)


def require_accessibility() -> None:
    script = """
tell application "System Events"
  if UI elements enabled is false then
    error "System Events UI scripting is disabled. Enable Accessibility " & ¬
      "for the terminal/Codex host in System Settings > Privacy & Security > Accessibility."
  end if
end tell
"""
    osascript(script)


def launch_app(*, clean_build: bool, skip_launch: bool, timeout: float) -> None:
    if skip_launch:
        return
    env = os.environ.copy()
    env.setdefault("CLEAN_BUILD", "0" if not clean_build else "1")
    env.setdefault("VERIFY_APP_CONNECTION", "1")
    env.setdefault("CAPDEP_DAEMON_MODE", "tmux")
    run([str(LAUNCHER)], cwd=APP_ROOT, env=env, timeout=timeout)


def wait_for_chat_window(timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error = ""
    script = """
tell application "CapDepMac" to activate
tell application "System Events"
  if not (exists process "CapDepMac") then error "CapDepMac process not found"
  tell process "CapDepMac"
    set frontmost to true
    if (count of windows) is 0 then
      keystroke space using option down
      delay 0.5
    end if
    if (count of windows) is 0 then error "CapDep window not found"
  end tell
end tell
"""
    while time.monotonic() < deadline:
        try:
            osascript(script)
            return
        except SmokeError as exc:
            last_error = str(exc)
            time.sleep(0.5)
    raise SmokeError(f"CapDepMac chat window did not become ready: {last_error}")


def assert_identifier(identifier: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = ""
    label = {
        "capdep.chat.input": "CapDep chat input",
        "capdep.chat.send": "Send CapDep message",
        "capdep.chat.connection-status": "CapDep daemon connection",
    }.get(identifier, identifier)
    script = f"""
tell application "System Events"
  tell process "CapDepMac"
    set foundIt to false
    set seenElements to ""
    set frontmost to true
    if (count of windows) is 0 then
      keystroke space using option down
      delay 0.5
    end if
    set chatWindow to window 1
    repeat with candidate in entire contents of chatWindow
      try
        set seenElements to seenElements & (role of candidate as text) & " | "
        try
          set seenElements to seenElements & (name of candidate as text)
        end try
        set seenElements to seenElements & linefeed
      end try
      try
        if (value of attribute "AXIdentifier" of candidate as text) is "{identifier}" then
          set foundIt to true
          exit repeat
        end if
      end try
      try
        if (name of candidate as text) starts with "{label}" then
          set foundIt to true
          exit repeat
        end if
      end try
      if length of seenElements > 3000 then exit repeat
    end repeat
    if foundIt is false then
      error "missing accessibility hook {identifier}; seen:" & linefeed & seenElements
    end if
  end tell
end tell
"""
    while time.monotonic() < deadline:
        try:
            osascript(script)
            return
        except SmokeError as exc:
            last_error = str(exc)
            time.sleep(0.5)
    raise SmokeError(last_error)


def submit_prompt(message: str) -> None:
    escaped = message.replace("\\", "\\\\").replace('"', '\\"')
    script = f"""
tell application "System Events"
  tell process "CapDepMac"
    set frontmost to true
    if (count of windows) is 0 then
      keystroke space using option down
      delay 0.5
    end if
    set inputField to missing value
    set sendButton to missing value
    set chatWindow to window 1
    repeat with candidate in entire contents of chatWindow
      try
        set candidateIdentifier to value of attribute "AXIdentifier" of candidate as text
        if candidateIdentifier is "capdep.chat.input" then set inputField to candidate
        if candidateIdentifier is "capdep.chat.send" then set sendButton to candidate
      end try
      try
        set candidateName to name of candidate as text
        if candidateName is "CapDep chat input" then set inputField to candidate
        if candidateName is "Send CapDep message" then set sendButton to candidate
      end try
    end repeat
    if inputField is missing value then error "chat input not found"
    set value of inputField to "{escaped}"
    if sendButton is missing value then error "send button not found"
    click sendButton
  end tell
end tell
"""
    try:
        osascript(script)
        return
    except SmokeError:
        pass

    keyboard_script = f"""
tell application "System Events"
  tell process "CapDepMac"
    set frontmost to true
    if (count of windows) is 0 then
      keystroke space using option down
      delay 0.5
    end if
    keystroke "{escaped}"
    key code 36
  end tell
end tell
"""
    osascript(keyboard_script)


def wait_for_prompt_echo(message: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    escaped = message.replace("\\", "\\\\").replace('"', '\\"')
    last_error = ""
    script = f"""
tell application "System Events"
  tell process "CapDepMac"
    set foundIt to false
    set frontmost to true
    if (count of windows) is 0 then
      keystroke space using option down
      delay 0.5
    end if
    set chatWindow to window 1
    repeat with candidate in entire contents of chatWindow
      try
        if (value of candidate as text) contains "{escaped}" then
          set foundIt to true
          exit repeat
        end if
      end try
    end repeat
    if foundIt is false then
      error "submitted prompt is not visible yet"
    end if
  end tell
end tell
"""
    while time.monotonic() < deadline:
        try:
            osascript(script)
            return
        except SmokeError as exc:
            last_error = str(exc)
            time.sleep(0.5)
    raise SmokeError(last_error)


def wait_for_chat_trace(message: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if CHAT_TRACE.exists():
            text = CHAT_TRACE.read_text(encoding="utf-8", errors="replace")
            if message in text and "submit_queued" in text:
                return
        time.sleep(0.5)
    raise SmokeError(f"submitted prompt did not appear in {CHAT_TRACE}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-launch", action="store_true")
    parser.add_argument("--clean-build", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--launch-timeout", type=float, default=120.0)
    parser.add_argument("--require-ax-hooks", action="store_true")
    parser.add_argument(
        "--message",
        default="GUI automation smoke: confirm typed prompt reaches chat history",
    )
    args = parser.parse_args(argv)

    try:
        require_accessibility()
        launch_app(
            clean_build=args.clean_build,
            skip_launch=args.skip_launch,
            timeout=args.launch_timeout,
        )
        wait_for_chat_window(args.timeout)
        for identifier in (
            "capdep.chat.input",
            "capdep.chat.send",
            "capdep.chat.connection-status",
        ):
            try:
                assert_identifier(identifier)
            except SmokeError as exc:
                if args.require_ax_hooks:
                    raise
                print(f"WARN {exc}", file=sys.stderr)
        submit_prompt(args.message)
        try:
            wait_for_prompt_echo(args.message, args.timeout)
        except SmokeError as exc:
            if args.require_ax_hooks:
                raise
            print(f"WARN {exc}", file=sys.stderr)
        wait_for_chat_trace(args.message, args.timeout)
    except SmokeError as exc:
        print(f"FAIL capdepmac gui interaction smoke\n{exc}", file=sys.stderr)
        return 1

    print("PASS capdepmac gui interaction smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
