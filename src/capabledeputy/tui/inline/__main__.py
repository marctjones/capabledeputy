"""Run the inline console demo:  python -m capabledeputy.tui.inline

Launches the greenfield inline console with the scripted demo driver (no daemon
needed) so the full redesigned experience can be seen end to end.
"""

from __future__ import annotations

from capabledeputy.tui.inline.app import InlineConsole
from capabledeputy.tui.inline.demo import DemoDriver
from capabledeputy.tui.inline.status import TrustState


def main() -> None:
    app = InlineConsole(
        DemoDriver(),
        trust=TrustState(
            session_name="morning-triage",
            purpose="daily-life",
            clearance="restricted",
        ),
    )
    app.run(inline=True)


if __name__ == "__main__":
    main()
