"""Built-in demo scenarios for the interactive REPL.

Each Scenario is a self-contained walkthrough: a one-paragraph intro
explaining the situation, the seed data to load into the in-memory
stores, the capabilities to grant the session, suggested prompts the
user might try, and a short note about the security property the
scenario exercises.

These deliberately live in Python (not JSON on disk) so the seed
shapes type-check and reference real Labels / CapabilityKinds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.labels import (
    CategoryTag,
    LabelState,
)
from capabledeputy.policy.tiers import Tier


@dataclass(frozen=True)
class InboxSeed:
    id: str
    sender: str
    subject: str
    body: str
    minutes_ago: int = 60


@dataclass(frozen=True)
class CalendarSeed:
    title: str
    starts_in_minutes: int
    duration_minutes: int = 30
    notes: str = ""
    label_state: LabelState = field(default_factory=LabelState)


@dataclass(frozen=True)
class MemorySeed:
    key: str
    value: str
    label_state: LabelState = field(default_factory=LabelState)


@dataclass(frozen=True)
class ScenarioCapability:
    """A capability the scenario grants the session at start.

    Mirrors `policy.Capability` but flat for transport over JSON-RPC.
    """

    kind: CapabilityKind
    pattern: str = "*"
    max_amount: int | None = None
    allows_destructive: bool = False


@dataclass(frozen=True)
class Scenario:
    name: str
    one_line: str
    intro: str
    intent: str
    capabilities: tuple[ScenarioCapability, ...]
    inbox: tuple[InboxSeed, ...] = ()
    calendar: tuple[CalendarSeed, ...] = ()
    memory: tuple[MemorySeed, ...] = ()
    suggested_prompts: tuple[str, ...] = ()
    security_note: str = ""


class ScenarioNotFoundError(KeyError):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


# --- scenario definitions -------------------------------------------------


_DAILY_BRIEFING = Scenario(
    name="daily-briefing",
    one_line="Read inbox + calendar, summarise the day.",
    intro=(
        "You have 3 unread emails and 2 calendar events today. Ask the "
        "agent to give you a summary of what's on your plate. The agent "
        "can read the inbox (which is labeled untrusted.external) and "
        "the calendar — but if it tries to act on anything that mixes "
        "untrusted content with an outbound channel, policy will block."
    ),
    intent="daily briefing — summarise inbox + calendar",
    capabilities=(
        ScenarioCapability(kind=CapabilityKind.READ_FS),
        ScenarioCapability(kind=CapabilityKind.CALENDAR_READ),
        ScenarioCapability(kind=CapabilityKind.CREATE_CAL),
        ScenarioCapability(kind=CapabilityKind.SEND_EMAIL),
    ),
    memory=(
        MemorySeed(
            key="contacts.wife",
            value="julie@example.com",
        ),
        MemorySeed(
            key="contacts.manager",
            value="manager@work.example",
        ),
    ),
    inbox=(
        InboxSeed(
            id="m1",
            sender="manager@work.example",
            subject="Q3 planning sync moved to 3pm",
            body="Heads up — moved Q3 planning to 3pm today, conf room B.",
            minutes_ago=120,
        ),
        InboxSeed(
            id="m2",
            sender="newsletter@news.example",
            subject="Weekly digest",
            body="This week in tech: ...",
            minutes_ago=240,
        ),
        InboxSeed(
            id="m3",
            sender="friend@personal.example",
            subject="Dinner Friday?",
            body="Want to grab dinner Friday around 7?",
            minutes_ago=30,
        ),
    ),
    calendar=(
        CalendarSeed(title="Standup", starts_in_minutes=60, duration_minutes=15),
        CalendarSeed(
            title="Q3 planning sync",
            starts_in_minutes=240,
            duration_minutes=60,
            notes="Moved from 1pm — conf room B",
        ),
    ),
    suggested_prompts=(
        "What's on my plate today?",
        "Summarise my unread email.",
        "Reply to my friend saying yes to dinner.",
    ),
    security_note=(
        "Try asking the agent to forward an email externally — "
        "untrusted-meets-egress will block it without an approval."
    ),
)


_UNTRUSTED_RESEARCH = Scenario(
    name="untrusted-research",
    one_line="Fetch a web page, decide whether to act on it.",
    intro=(
        "Web content is labeled untrusted.external. The agent can fetch "
        "and summarise, but if it tries to send the content (or anything "
        "derived from it) via email, the policy engine will block — "
        "this is the lethal-trifecta defense in miniature."
    ),
    intent="web research — fetch and summarise",
    capabilities=(
        ScenarioCapability(kind=CapabilityKind.WEB_FETCH),
        ScenarioCapability(kind=CapabilityKind.READ_FS),
        ScenarioCapability(kind=CapabilityKind.SEND_EMAIL),
    ),
    suggested_prompts=(
        "Fetch https://example.com and summarise it for me.",
        "Now email that summary to alice@example.com.",
    ),
    security_note=(
        "The email send will be blocked by the untrusted-meets-egress "
        "rule — once the session has read web content, it cannot egress."
    ),
)


_ACCOUNTANT = Scenario(
    name="accountant",
    one_line="Summarise financial notes, optionally send to accountant.",
    intro=(
        "Your memory holds three financial notes labeled "
        "confidential.financial. Ask the agent to summarise them. Then "
        "try asking it to email the summary to your accountant — "
        "financial-meets-email will block; you'd need the gated "
        "declassification path (approve via /approve) to send."
    ),
    intent="financial summary",
    capabilities=(
        ScenarioCapability(kind=CapabilityKind.READ_FS),
        ScenarioCapability(kind=CapabilityKind.SEND_EMAIL),
    ),
    memory=(
        MemorySeed(
            key="rent-2026-05",
            value="Rent paid 2026-05-01: $2400",
            label_state=LabelState(
                a=frozenset(
                    {
                        CategoryTag(
                            "financial",
                            Tier.REGULATED,
                            assignment_provenance="source-declared",
                        )
                    }
                )
            ),
        ),
        MemorySeed(
            key="grocery-2026-05",
            value="Groceries weekly avg: $180",
            label_state=LabelState(
                a=frozenset(
                    {
                        CategoryTag(
                            "financial",
                            Tier.REGULATED,
                            assignment_provenance="source-declared",
                        )
                    }
                )
            ),
        ),
        MemorySeed(
            key="utilities-2026-05",
            value="Utilities May: $145",
            label_state=LabelState(
                a=frozenset(
                    {
                        CategoryTag(
                            "financial",
                            Tier.REGULATED,
                            assignment_provenance="source-declared",
                        )
                    }
                )
            ),
        ),
    ),
    suggested_prompts=(
        "Read my financial notes and summarise May spending.",
        "Email that summary to accountant@cpa.example.",
    ),
    security_note=(
        "The email send will surface as require_approval. Approve it "
        "with /approve <id> to see the purpose-limited session spawn."
    ),
)


_SCRIPT_BATCH_FILES = Scenario(
    name="script-batch-files",
    one_line="Create a small script to clean up a folder of downloaded files.",
    intro=(
        "You have a messy downloads folder and want CapDep to propose a "
        "small Python script that groups files by extension. The safe "
        "scripting workflow should review the exact script, run it in an "
        "isolated sandbox, then ask before exporting any file changes."
    ),
    intent="safe scripting — batch file cleanup",
    capabilities=(
        ScenarioCapability(kind=CapabilityKind.READ_FS, pattern="workspace/downloads/*"),
        ScenarioCapability(kind=CapabilityKind.CREATE_FS, pattern="workspace/organized/*"),
        ScenarioCapability(kind=CapabilityKind.EXECUTE_SANDBOX, pattern="safe-scripting/*"),
    ),
    memory=(
        MemorySeed(
            key="workspace.downloads.manifest",
            value=(
                "Downloads contains invoice-may.pdf, headshot.png, notes.txt, "
                "invoice-june.pdf, and archive.zip."
            ),
        ),
    ),
    suggested_prompts=(
        "Plan a safe script that organizes my downloads by file extension.",
        "Show me the exact script before it runs.",
        "Review the sandbox output and propose the export.",
    ),
    security_note=(
        "The workflow should bind the reviewed script and proposed output to "
        "typed artifacts before any file export is approved."
    ),
)


_SCRIPT_BATCH_PHOTOS = Scenario(
    name="script-batch-photos",
    one_line="Prepare a photo batch script with reviewed outputs.",
    intro=(
        "You have a folder of photos and want a simple automation that "
        "renames them consistently and prepares resized copies. CapDep "
        "should keep the script/run/export sequence daemon-owned so normal "
        "users can inspect the result without learning a developer workflow."
    ),
    intent="safe scripting — batch photo processing",
    capabilities=(
        ScenarioCapability(kind=CapabilityKind.READ_FS, pattern="photos/input/*"),
        ScenarioCapability(kind=CapabilityKind.CREATE_FS, pattern="photos/output/*"),
        ScenarioCapability(kind=CapabilityKind.EXECUTE_SANDBOX, pattern="safe-scripting/*"),
    ),
    memory=(
        MemorySeed(
            key="photos.input.manifest",
            value="IMG_1040.JPG, IMG_1041.JPG, IMG_1042.JPG need event-based names.",
        ),
    ),
    suggested_prompts=(
        "Make a safe plan to rename these photos and create smaller copies.",
        "Prepare the script artifact for review.",
        "Summarize the run evidence before asking me to export anything.",
    ),
    security_note=(
        "Photo writes stay as proposed file-export artifacts until the user "
        "approves the exact destination and content hash."
    ),
)


_SCRIPT_DOCUMENT_TRANSFORM = Scenario(
    name="script-document-transform",
    one_line="Transform a document or spreadsheet into a reviewed report.",
    intro=(
        "You have a small CSV-style project tracker and want a script to "
        "produce a clean status report. The workflow should review generated "
        "code, run it in isolation, capture evidence, and propose the report "
        "as an exact file export."
    ),
    intent="safe scripting — document transformation",
    capabilities=(
        ScenarioCapability(kind=CapabilityKind.READ_FS, pattern="documents/source/*"),
        ScenarioCapability(kind=CapabilityKind.CREATE_FS, pattern="documents/reports/*"),
        ScenarioCapability(kind=CapabilityKind.EXECUTE_SANDBOX, pattern="safe-scripting/*"),
    ),
    memory=(
        MemorySeed(
            key="documents.project_tracker",
            value="task,status\nIntake,done\nDraft,in progress\nReview,blocked\n",
        ),
    ),
    suggested_prompts=(
        "Plan a safe script that turns this tracker into a markdown status report.",
        "Prepare the reviewed script artifact.",
        "Prepare the reviewed report export after the sandbox run.",
    ),
    security_note=(
        "The report export should be reviewable as a typed artifact with an "
        "exact destination, content type, and digest."
    ),
)


SCENARIOS: dict[str, Scenario] = {
    s.name: s
    for s in (
        _DAILY_BRIEFING,
        _UNTRUSTED_RESEARCH,
        _ACCOUNTANT,
        _SCRIPT_BATCH_FILES,
        _SCRIPT_BATCH_PHOTOS,
        _SCRIPT_DOCUMENT_TRANSFORM,
    )
}


def get_scenario(name: str) -> Scenario:
    try:
        return SCENARIOS[name]
    except KeyError as e:
        raise ScenarioNotFoundError(name) from e


def absolute_time(reference: datetime, minutes_offset: int) -> datetime:
    """Convenience for converting relative seed offsets to absolute datetimes
    against a fixed reference (so demo seeds are deterministic per run)."""
    return reference + timedelta(minutes=minutes_offset)


def utcnow_floor_minute() -> datetime:
    now = datetime.now(UTC)
    return now.replace(second=0, microsecond=0)
