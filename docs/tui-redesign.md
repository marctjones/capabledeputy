# TUI redesign — inline console design record

A greenfield visual + UX redesign of the operator interface, on the **same
stack** (Textual / Rich / prompt_toolkit). This began as a proposal on
2026-06-08 and shipped as the inline console in v0.19.0 (`capdep ui`). Keep
this document as the design record and safety checklist for future console
work; implementation status belongs in `CHANGELOG.md` and
`docs/usability-hardening-plan.md`.

The anchor: the operator likes how **Claude Code** works — a conversational,
inline streaming REPL in normal scrollback that *pauses for permission*, not a
full-screen instrument panel. For CapableDeputy this is doubly apt, because **the
permission prompt is the central interaction** — every gated action is one. So
the design goal is: build the best-in-class version of Claude Code's permission
prompt, security-aware and fatigue-resistant.

---

## 1. The model shift: inline conversational REPL, not a dashboard

The current TUI is a full-screen instrument panel (panes for sessions /
approvals / trace / events). Claude Code, Crush, OpenCode, and Aider all do the
opposite, and it's why they feel good: a conversation that **flows in
scrollback**, with tool calls and decisions **live-rendering inline as they
happen**, and the terminal pausing only when the human is needed.

Greenfield, CapableDeputy adopts that model: a streaming conversational REPL where
each tool call and policy decision renders inline, approvals appear as inline
permission cards, and heavy views (lineage, audit, session overview) are
one-keystroke full-screen *screens*, not always-on panes.

---

## 2. References (what we borrow, and from where)

| Source | Pattern | Mapping to CapableDeputy |
|---|---|---|
| **Claude Code** | inline streaming; pause-for-permission; `/` palette (filter; unavailable hidden); a quiet status line; `/fewer-permission-prompts` (scan history → *propose an allowlist*) | approval = inline permission card; status line shows *trust state*; allowlist-proposal becomes "**turn this into a rule?**" wired to `rules.yaml`/inspectors |
| **Crush / Charm `tui-design`** | "every character is a design decision"; consistent **glyph vocabulary** (● ○ ◉ ✓ ✗); **grid discipline** "invisible but felt"; **borders sparingly** (one outer container; dividers via spacing/color) | the visual language (§5) |
| **Posting** (Textual) | keyboard-first; jump-to-anything command palette; SSH-friendly; readable YAML config | power-user accelerator; remote oversight |
| **Elia** (Textual ChatGPT client) | the conversational-LLM-TUI structure in *our* stack | reference architecture for the streaming surface |
| **Aider / OpenCode** | tool calls / edits / planning live-render inline; legible action log | the inline decision-chip timeline |
| **Dolphie** | "single pane of glass" real-time monitoring, done beautifully | the optional full-screen sessions overview |

---

## 3. The main experience

```
  morning-triage · daily-life · clearance:restricted · ●untrusted · ⚑1   ← persistent status line (dim)
  ────────────────────────────────────────────────────────────────────

› summarize my labs and email a recap to Dr Lee

  ◇ read    ~/Documents/Medical/labs.pdf                            ✓
  ◇ extract via quarantine → DoseSummary                   ✓ declassified
  ⚑ heads-up the recap contains personal health data          (proceeding)
  ⛔ email.send → dr.lee@clinic.com
     health-meets-egress · reason: "share lab recap"
     ╭──────────────────────────────────────────────────╮
     │  approve this send?   to dr.lee@clinic.com         │
     │  ⬤ health / restricted        irreversible         │
     │  [a] approve   [d] deny   [o] override   [w] why   │
     ╰──────────────────────────────────────────────────╯

›                                                       ⏎ send  ·  / for cmds
```

- **Persistent status line** carrying the trust state the engine knows but
  never shows today: session purpose, clearance ceiling, current taint,
  pending-advisory count. One line, dim until something changes.
- **Streaming conversation** as a Textual inline `Markdown` widget; the agent's
  reasoning and each tool call appear as they happen.
- **Inline decision chips** with semantic glyphs: `◇` tool call · `✓` allowed
  (quiet/dim) · `⚑` advisory/WARN (amber, **non-blocking**) · `⛔` needs-approval
  (expands into a card) · `✗` denied (red, with the engine's pasteable recovery
  inline).
- **Approval is an inline card — *trusted because the app drew it*, not because
  it's in a separate region.** The card renders inline in the conversation
  (the preferred feel), drawn by the app from a typed `PolicyDecision`, carrying
  the per-session anti-spoof marker. It is unforgeable not by segregation but
  because untrusted content is quarantine-rendered (can't draw a card) and a
  keypress only acts on the one *armed* engine decision (§8.1). Keyboard-driven,
  **grouped** (FR-035) into one card with per-item toggles when several share a
  rationale. Only the gravest actions (override / prohibited / irreversible
  egress) escalate to a brief focused confirmation; a `docked` decision surface
  is an *optional* mode for high-assurance deployments.
- **Override** rendered as a visibly heavier card with typed friction scaled to
  severity — unmistakable from an ordinary approval.

---

## 4. On-demand full-screen screens (calm by default)

Reached via the command palette / slash; not on-screen until asked for:
- **`/flow`** — a live data-lineage view (labs.pdf → quarantine → declassified
  summary → ✉). Shows the security model instead of documenting it. The
  standout, novel surface.
- **`/sessions`** — Dolphie-style single-pane overview, each row with its trust
  strip.
- **`/audit`** — the hash-chained log as a navigable, verifiable table.

---

## 5. Visual language (grounded in the `tui-design` principles)

- **A consistent glyph vocabulary, learned once:** `◇` action · `✓` allow ·
  `⚑` warn · `⛔` approve · `✗` deny · `⬤` tier dot · `●` untrusted · `⚖`
  override. Every character is a design decision.
- **Semantic color only — never decoration.** Dim/neutral = allowed; one amber
  = advisory; one confident blue = approval-needed; red reserved strictly for
  deny/danger. Color *is* signal — this keeps it calm and fights alert-
  blindness.
- **Grid discipline, "invisible but felt":** aligned columns, consistent
  gutters, generous whitespace.
- **Borders sparingly:** only *cards* (approval / override) get a box, because
  they are decision points; everything else is separated by spacing and color.
- **Motion to direct attention, not decorate:** cards slide in; the status line
  pulses amber once on a new advisory; a subtle shimmer while the agent works.
- **Themes as Textual CSS:** polished dark default + light + high-contrast/
  accessible; a focus mode that hides all but the conversation and active card.
- **A distinct identity:** a recognizable status line + decision-glyph set so
  CapableDeputy *looks like* a calm, trustworthy security console.

---

## 6. Anti-fatigue UX (the part that ties to the engine work)

- **Approve in one keystroke; grouped approvals approve as a set** — make the
  FR-035 grouping visible.
- **Learn from approvals** (the Claude Code `/fewer-permission-prompts` idea):
  after you approve similar sends a few times, CapableDeputy offers "**make this a
  rule?**" → drafts a `rules.yaml`/inspector entry you **ratify deliberately**
  (never one-click-silent). Pairs with U2 (inspectors) + the ratification flow.
- **The WARN lane** (usability U3) gets a dedicated, non-blocking visual
  treatment — informed-proceed instead of a stop.
- **Progressive disclosure:** `/why` expands rationale, `/flow` shows
  provenance; nothing heavy on-screen until asked.

---

## 7. Technology (same stack)

- **Textual in inline mode** for the REPL (streaming `Markdown` + `Input` + a
  `Worker`), with full-screen Textual **screens** for `/flow`, `/sessions`,
  `/audit`. One framework, inline *and* full-screen, plus a **browser
  companion for free** via `textual serve` / textual-web.
- **prompt_toolkit** for the input line + completion (richer editing than
  Textual's `Input`; already powers the existing completer; it's what makes
  IPython/pgcli feel good).
- **Rich** for the decision-chip / card rendering primitives.
- *Tradeoff:* Textual inline mode is unsupported on Windows. If Windows parity
  matters, the fallback is a **Rich `Live` + prompt_toolkit** REPL (closer to
  today's `chat.py`, fully cross-platform), reserving full Textual screens for
  the heavy views. Default to Textual-inline; keep the fallback in mind.

---

## 8. Safety alignment of *this design*

A UI does not enforce the security models — the engine does — so the right
question is: **does the presentation layer make the human a faithful overseer,
and does it avoid introducing new ways to be fooled or to erode oversight?**
Honest verdict: this design is **net strongly positive** for alignment (it
makes the otherwise-invisible security state legible, which is what lets a
human *actually* exercise oversight), but it introduces **two new risks the
engine doesn't cover** — presentation-layer trust-spoofing, and fatigue-feature
erosion — that the design MUST commit to mitigating. The non-negotiable
requirements are listed after the frame-by-frame read.

### Security models
- **Reference monitor (total mediation):** ✅ supports. Every mediated decision
  renders inline as it happens — nothing is silently auto-run. *Risk:* "quiet
  allows" + scrollback could let a WARN scroll past unseen → the WARN lane must
  be visually distinct and counted in the status line (it is).
- **Bell-LaPadula / clearance, Brewer-Nash conflict floors:** ✅ supports
  *legibility* — the status-line taint + the `/flow` view make read-up and
  conflict-floor denials understandable rather than mysterious. Neutral to
  enforcement (engine-side).
- **Clark-Wilson (gated txn + sep-of-duty):** ✅ supports. The override card
  renders the dual-control state and the typed friction. **Requirement:** the
  attester's card MUST show *engine-authored verbatim facts, never model prose*
  (FR-036 / Principle V) — see hard requirement #1.
- **Object-capability / IFC:** ✅ supports legibility (the `/flow` lineage view
  *is* the IFC graph made visible). Neutral to enforcement.

### Flow patterns
- **① turn-level:** ✅ the inline chip timeline is the per-turn gate made
  visible.
- **② dual-LLM / declassify:** ✅ the `✓ declassified` chip + `/flow` make the
  quarantine boundary visible. **Requirement:** `/flow` and the conversation
  must render the *declassified schema summary* and *opaque handles*, never the
  raw quarantined source bytes, so the UI does not become the leak the dual-LLM
  pattern prevents (the human authorizer may of course see verbatim *facts*
  needed to decide — that is engine-authored, not the raw model-handled blob).
- **③ reference-handle:** ✅ shows handle-bound destinations; the human sees the
  resolved, pinned destination on the card — exactly what redirection-
  resistance wants surfaced.
- **⑤ sealed containment:** ✅ a chip can show "ran in sandbox," reinforcing
  containment ≠ declassification (the output keeps its labels in `/flow`).

### AI-safety principles
- **P1 least authority:** neutral (engine). The UI should not offer a "grant
  broad capability" shortcut that out-paces the engine's scoping.
- **P2 trusted/untrusted separation:** ✅✅ the status-line taint indicator makes
  provenance *visible* — a major practical win. **But the UI is itself a new P2
  surface** (see hard requirement #2): rendered untrusted content (email bodies,
  tool results) must never be able to impersonate trusted UI chrome.
- **P3 confidentiality / controlled flow:** ✅✅ `/flow` is the single best thing
  this design does for P3 — it teaches and exposes controlled flow without a
  manual.
- **P4 purpose limitation:** ✅ the status line shows the active purpose;
  inadmissible reads can surface as a visible (non-blocking) contamination
  signal (usability U7).
- **P5 human oversight:** ✅✅ the approval/override cards are P5 made fast and
  informed. **The risk lives here:** the anti-fatigue "make this a rule?"
  feature can erode oversight into rubber-stamping if it is one-click or
  nudges toward auto-approve (see hard requirement #3).
- **P6 accountability / traceability:** ✅ `/audit` surfaces the hash-chained,
  verifiable log; the UI never edits it.
- **P7 fail-safe defaults:** ✅ *as a requirement* — see hard requirement #4: a
  rendering failure, disconnect, or timeout must **never** become an implicit
  approval; the UI defaults to "pending / blocked," never "proceed."
- **P8 containment:** neutral (engine).

### Hard requirements (non-negotiable for this design to stay aligned)

1. **Engine-authored facts only on decision cards (FR-036 / Principle V).** The
   approval/override card content (action, target, labels, reason, floor) is
   sourced from the engine `PolicyDecision` object, *never* from model-
   generated prose. A compromised or manipulated model must not be able to
   craft a persuasive-but-false approval prompt. The model's narration may
   appear in the conversation, visually *separated* from the decision card.
2. **Untrusted content cannot impersonate trusted chrome (presentation-layer
   P2).** All rendered tool results / model output / email bodies are
   sanitized — strip ANSI/escape sequences, neutralize markup that could forge
   a card, status line, or decision glyph — and are visually delineated as
   untrusted (e.g. a quoted, provenance-tagged block). The decision cards,
   status line, and glyphs are *trusted chrome* the UI draws itself and that
   untrusted content can never reproduce. This is the UI's analogue of the
   untrusted-egress floor, and the engine does not cover it — it is the single
   most important new safety obligation this design introduces.
3. **Rule-creation stays deliberate, never a fatigue shortcut to auto-approve.**
   "Make this a rule?" drafts a rule the human *ratifies* through the existing
   ratification path (it cannot self-apply; the AI never authors a firing rule —
   FR-031/FR-014). The UI must show what the rule would auto-allow and require
   an explicit, non-defaulted confirmation. Oversight reduction must come from
   *accuracy*, not from making "yes" the path of least resistance.
4. **Fail-safe rendering.** No UI state (lost connection, render error, input
   timeout, scrollback overflow) may resolve a pending decision toward ALLOW.
   The default on any uncertainty is to keep the action gated and visible.
5. **WARN is noticeable but bounded.** The advisory lane must be distinct
   enough not to be missed (status-line count + a one-time pulse) yet never so
   frequent it trains the eye to ignore it — which means WARN only ever applies
   to the discretionary band, never a structural floor (consistent with
   usability U3).

**Net:** the design *strengthens* P2/P3/P5/P6 in practice by making the
security model legible and oversight fast — provided the five hard requirements
hold. Requirements #1 and #2 are the load-bearing ones: they keep the
presentation layer from becoming the weak link (a believable-but-false approval
card, or untrusted content masquerading as trusted UI) that the otherwise-
strong engine can't see.

---

## 8.1 Security-strengthening changes (adopted from the review)

The §8 hard requirements are *mitigations the implementer must remember*. These
changes turn the two load-bearing ones (a false approval card; untrusted content
impersonating chrome) into **architecture that can't be gotten wrong**, plus a
few defense-in-depth additions. They cost a little of the pure-inline aesthetic;
the trade is worth it.

1. **Decision cards stay INLINE (the preferred feel) and are trusted because the
   app drew them, not because they're segregated.** "Trusted chrome" means *the
   app rendered it from a typed `PolicyDecision`, and untrusted content cannot
   reproduce it* — which is achieved by mechanism, not by taking over the screen:
   (a) untrusted content is quarantine-rendered (#3) so it literally cannot draw
   a card; (b) every real card carries the per-session anti-spoof marker (#4) a
   forger can't know; (c) a keypress only ever acts on the one *armed* engine
   decision (#7) — a painted fake card is inert text. Together these make an
   inline card as unforgeable as a docked one. The residual ("a fake card shown
   beside a real pending grave decision") is closed by **escalating only the
   gravest actions** (override / prohibited / irreversible egress) to a brief
   focused confirmation, and by type-the-target confirmation (#6). A fully
   **`docked` decision surface is an optional mode** (`ui.decision_surface:
   inline | docked`) for high-assurance / shared-terminal deployments — same
   engine, same guarantees, different placement. Default is inline.
2. **The decision component accepts a typed `PolicyDecision` only — never a
   string.** Engine facts and model narration travel different code paths to
   different surfaces; there is no function where model-supplied text can reach
   a decision card. Makes hard-requirement #1 (engine-authored facts) a
   *type-level* guarantee, not a discipline.
3. **Untrusted content renders in a forced quarantine style.** A permanent
   left-gutter marker, forced plaintext (no markup interpretation), and all
   escape/control sequences stripped — including terminal hyperlinks (OSC 8) and
   inline images (sixel/kitty), which untrusted content must never emit (a link
   can phish, an image can paint fake chrome). Untrusted content can only ever
   appear as visibly-quoted plaintext.
4. **Per-session anti-spoof marker.** The trusted chrome carries a per-session
   random accent/glyph shown in the status line, learned by the operator. A
   "card" lacking the session's marker is, by construction, not real chrome —
   a cheap secure-attention signal against any residual impersonation.
5. **"Make this a rule?" goes through shadow-mode → blast-radius preview →
   ratification.** A drafted rule first runs in **shadow** (the existing P2.8
   rule-shadow machinery) for K turns, showing exactly what it *would have*
   auto-allowed, before it can be promoted — and promotion is the existing
   ratification path (single-/dual-control), never one click. Fatigue reduction
   thus comes from *reviewed accuracy*, never from making "yes" the easy path.
6. **Grave irreversible egress: type the engine-provided target to confirm.**
   For the highest-severity sends/overrides, the human types the actual
   recipient/destination (supplied by the engine, not echoed from model text)
   into the trusted chrome — proving they read it and defeating blind approve.
   Friction text is always entered into chrome, never reflected from content.
7. **The UI is a re-authorized client + an always-available kill switch.** Every
   approve/deny/override is submitted to the engine, which re-checks the
   operator's authorization and audits it with the principal — the UI can never
   *forge* an authorization, only request one. A global, engine-enforced
   "halt/revoke this session" hotkey is always reachable (P8 containment from
   the keyboard), and the status line is fixed, engine-sourced chrome that
   cannot be scrolled away, covered, or served stale.

Net effect: changes 1–3 move the presentation layer's two worst failure modes
from "mitigated" to "structurally prevented"; 4–7 are defense-in-depth that also
keep the anti-fatigue features (5) and human-in-control (7) from quietly eroding
oversight. Engine-side enforcement is unchanged; the UI simply stops being a
place the otherwise-strong engine could be undermined.

---

## 9. Phased build (greenfield, alongside the old UI until parity)

1. **REPL spine with the trust mechanisms built in** — inline streaming
   conversation + fixed engine-sourced status line + input, and from day one the
   *content/decision distinction by mechanism*: untrusted content is quarantine-
   rendered, real chrome carries the per-session marker, keypresses bind to the
   armed engine decision (§8.1 #1/#3/#4/#7). Foundational, not a later pass.
2. **Inline decision cards, app-drawn from a typed `PolicyDecision`** — never
   from a model string (§8.1 #2); only one armed at a time; grave actions
   escalate to a brief focused confirm; `docked` is an optional mode. Encodes
   hard requirements #1 + #4 from the start.
3. **Visual language pass** — glyphs, semantic color, themes, motion, plus the
   per-session anti-spoof marker (§8.1 #4).
4. **Untrusted-content quarantine rendering** — forced plaintext, gutter,
   ANSI/OSC/hyperlink/image stripping (§8.1 #3 / hard requirement #2). A safety
   property, not polish — do not defer.
5. **Anti-fatigue layer** — grouped approvals + deliberate "make this a rule?"
   (hard requirement #3); pairs with usability U2/U3.
6. **On-demand screens** — `/flow` (the differentiator, hard requirement #2b:
   handles/labels not raw bytes), `/sessions`, `/audit`.
7. **`textual serve`** browser companion.

---

## Sources

- Textual — <https://textual.textualize.io/> · inline apps
  <https://textual.textualize.io/how-to/style-inline-apps/> · streaming anatomy
  <https://textual.textualize.io/blog/2024/09/15/anatomy-of-a-textual-user-interface/>
- Posting — <https://github.com/darrenburns/posting> · Harlequin —
  <https://harlequin.sh/> · Elia — <https://terminaltrove.com/elia/>
- Charm Crush — <https://github.com/charmbracelet/crush> · tui-design
  principles — <https://lobehub.com/skills/kastheco-kasmos-tui-design>
- Claude Code permissions/UX — <https://code.claude.com/docs/en/permissions>
- awesome-tuis — <https://github.com/rothgar/awesome-tuis>
