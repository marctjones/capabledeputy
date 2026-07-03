#!/usr/bin/env python3
"""macOS-sensitive CapDepMac GUI interaction smoke.

This is intentionally outside the default deterministic tier. It uses
System Events accessibility automation to prove that the packaged app opens,
the primary chat controls are discoverable, and a prompt can be typed and
submitted through the GUI.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "apps/macos/CapDep"
LAUNCHER = APP_ROOT / "scripts/run-local-app.sh"
CHAT_TRACE = Path.home() / "Library/Logs/CapDep/chat-trace.log"
DEFAULT_ARTIFACT_DIR = Path(tempfile.gettempdir()) / "capdepmac-gui-smoke-artifacts"


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


def launch_app(
    *,
    clean_build: bool,
    skip_launch: bool,
    timeout: float,
    command_file: Path | None = None,
    background: bool = False,
) -> None:
    if skip_launch:
        return
    env = os.environ.copy()
    env.setdefault("CLEAN_BUILD", "0" if not clean_build else "1")
    env.setdefault("VERIFY_APP_CONNECTION", "1")
    env.setdefault("CAPDEP_DAEMON_MODE", "tmux")
    if command_file is not None:
        env["CAPDEP_GUI_TEST_COMMAND_FILE"] = str(command_file)
    if background:
        env["CAPDEP_GUI_BACKGROUND_OPEN"] = "1"
    run([str(LAUNCHER)], cwd=APP_ROOT, env=env, timeout=timeout)


def write_test_hook_prompt(
    command_file: Path,
    message: str,
    *,
    command: str = "submit_prompt",
) -> None:
    command_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {"command": command, "message": message}
    with command_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _read_trace_lines() -> list[str]:
    if not CHAT_TRACE.exists():
        return []
    return CHAT_TRACE.read_text(encoding="utf-8", errors="replace").splitlines()


def _trace_line_count() -> int:
    return len(_read_trace_lines())


def _trace_lines_after(line_count: int) -> list[str]:
    return _read_trace_lines()[line_count:]


def _line_contains_message(line: str, message: str) -> bool:
    return message in line or message[:200] in line


def wait_for_chat_trace_messages(
    messages: list[str],
    *,
    line_count: int,
    timeout: float,
    marker: str = "submit_queued",
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        lines = _trace_lines_after(line_count)
        if all(
            any(marker in line and _line_contains_message(line, message) for line in lines)
            for message in messages
        ):
            return
        time.sleep(0.5)
    raise SmokeError(f"{marker} did not appear for every prompt in {CHAT_TRACE}")


def wait_for_turn_completions(*, line_count: int, expected: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        lines = _trace_lines_after(line_count)
        if sum("turn_send_end" in line for line in lines) >= expected:
            return
        if any("turn_error" in line for line in lines):
            raise SmokeError("turn_error appeared in chat trace")
        time.sleep(0.5)
    raise SmokeError(
        f"only saw {sum('turn_send_end' in line for line in _trace_lines_after(line_count))} "
        f"turn_send_end trace entries; expected {expected}",
    )


def wait_for_trace_image_markdown(*, line_count: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        lines = _trace_lines_after(line_count)
        if any(
            "turn_send_end" in line and 'output_has_image_markdown="true"' in line
            for line in lines
        ):
            return
        time.sleep(0.5)
    raise SmokeError("completed GUI turn did not expose image markdown in chat trace")


def write_failure_artifacts(
    *,
    artifact_dir: Path,
    command_file: Path | None,
    args: argparse.Namespace,
    error: SmokeError,
) -> Path:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    out = artifact_dir / stamp
    out.mkdir(parents=True, exist_ok=True)
    (out / "error.txt").write_text(str(error), encoding="utf-8")
    (out / "args.json").write_text(
        json.dumps(
            {
                "driver": args.driver,
                "messages": args.messages,
                "wait_response": args.wait_response,
                "generated_image": args.generated_image,
                "require_ax_hooks": args.require_ax_hooks,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    if command_file is not None and command_file.exists():
        shutil.copyfile(command_file, out / "command-file.jsonl")
    if CHAT_TRACE.exists():
        tail = "\n".join(_read_trace_lines()[-250:])
        (out / "chat-trace-tail.log").write_text(tail + "\n", encoding="utf-8")
    try:
        ps = run(["ps", "-axo", "pid,ppid,command"], timeout=10.0)
        lines = [line for line in ps.splitlines() if "CapDep" in line or "capdep" in line]
        (out / "processes.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    except SmokeError as exc:
        (out / "processes-error.txt").write_text(str(exc), encoding="utf-8")
    return out


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
    wait_for_chat_trace_messages(
        [message],
        line_count=0,
        timeout=timeout,
        marker="submit_queued",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-launch", action="store_true")
    parser.add_argument("--clean-build", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--response-timeout", type=float, default=120.0)
    parser.add_argument("--launch-timeout", type=float, default=120.0)
    parser.add_argument("--require-ax-hooks", action="store_true")
    parser.add_argument(
        "--no-wait-response",
        dest="wait_response",
        action="store_false",
        help="only verify prompt submission; by default test-hook waits for turn completion",
    )
    parser.set_defaults(wait_response=True)
    parser.add_argument(
        "--multi-prompt",
        action="store_true",
        help="queue two prompts through the no-focus test hook before waiting for completion",
    )
    parser.add_argument(
        "--extra-message",
        dest="extra_messages",
        action="append",
        default=[],
        help="additional prompt to queue after --message; can be repeated",
    )
    parser.add_argument(
        "--generated-image",
        action="store_true",
        help="run an additional prompt that must complete with image markdown in the GUI trace",
    )
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=DEFAULT_ARTIFACT_DIR,
        help="directory where failure artifacts are written",
    )
    parser.add_argument(
        "--driver",
        choices=["test-hook", "keyboard"],
        default="test-hook",
        help="test-hook drives the app without focus; keyboard uses System Events typing.",
    )
    parser.add_argument(
        "--command-file",
        type=Path,
        default=Path(tempfile.gettempdir()) / f"capdepmac-gui-test-commands-{os.getpid()}.jsonl",
    )
    parser.add_argument(
        "--message",
        default="GUI automation smoke: reply with exactly capdepmac smoke ok",
    )
    args = parser.parse_args(argv)
    args.messages = [args.message, *args.extra_messages]
    if args.multi_prompt:
        args.messages.append(
            "GUI automation smoke second queued prompt: reply with exactly queued ok",
        )

    try:
        if args.driver == "keyboard":
            require_accessibility()
        if args.driver == "test-hook" and not args.skip_launch:
            args.command_file.unlink(missing_ok=True)
        launch_app(
            clean_build=args.clean_build,
            skip_launch=args.skip_launch,
            timeout=args.launch_timeout,
            command_file=args.command_file if args.driver == "test-hook" else None,
            background=args.driver == "test-hook",
        )
        trace_start = _trace_line_count()
        if args.driver == "test-hook":
            command = "queue_prompt" if len(args.messages) > 1 else "submit_prompt"
            for message in args.messages:
                write_test_hook_prompt(args.command_file, message, command=command)
            wait_for_chat_trace_messages(
                args.messages,
                line_count=trace_start,
                timeout=args.timeout,
                marker="submit_queued",
            )
            if args.wait_response:
                wait_for_turn_completions(
                    line_count=trace_start,
                    expected=len(args.messages),
                    timeout=args.response_timeout,
                )
            if args.generated_image:
                image_message = "generate and show me a picture of a simple red square icon"
                image_start = _trace_line_count()
                write_test_hook_prompt(args.command_file, image_message)
                wait_for_chat_trace_messages(
                    [image_message],
                    line_count=image_start,
                    timeout=args.timeout,
                    marker="submit_queued",
                )
                wait_for_turn_completions(
                    line_count=image_start,
                    expected=1,
                    timeout=max(args.response_timeout, 300.0),
                )
                wait_for_trace_image_markdown(
                    line_count=image_start,
                    timeout=args.timeout,
                )
            print("PASS capdepmac gui interaction smoke")
            return 0

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
        wait_for_chat_trace_messages(
            [args.message],
            line_count=trace_start,
            timeout=args.timeout,
            marker="submit_queued",
        )
    except SmokeError as exc:
        artifacts = write_failure_artifacts(
            artifact_dir=args.artifact_dir,
            command_file=args.command_file if args.driver == "test-hook" else None,
            args=args,
            error=exc,
        )
        print(f"FAIL capdepmac gui interaction smoke\n{exc}", file=sys.stderr)
        print(f"Artifacts: {artifacts}", file=sys.stderr)
        return 1

    print("PASS capdepmac gui interaction smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
