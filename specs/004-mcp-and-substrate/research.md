---
description: "Spec 004 research — competitive landscape, security incident corpus, integration targets"
---

# Research: Spec 004 — MCP + Substrate Integration

## Date

May 2026 web research conducted to ground spec-004's positioning decisions in
current production reality, not assumptions.

## The "Claw" ecosystem — competitive landscape

### OpenClaw

Open-source self-hosted personal AI assistant. Dominant share in the
personal-assistant slot.

- **Architecture**: Three-layer (channel / brain / body) with a Gateway as
  always-on control plane. Node.js service.
- **Channels**: WhatsApp, Telegram, Slack, Discord, Google Chat, Signal,
  iMessage, IRC, Microsoft Teams, Matrix, Feishu, LINE, Mattermost, Nextcloud
  Talk, Nostr, Synology Chat, Tlon, Twitch, Zalo, WeChat, QQ, WebChat.
- **Ecosystem**: ClawHub marketplace with 13,700+ skills.
- **Permissions model**: Broad system access (full disk, terminal, env vars,
  API keys). No capability model.
- **Decision model**: LLM-driven; optional Docker sandboxing.
- **Skills**: Markdown playbooks with YAML frontmatter; auto-discovered at
  runtime; selectively injected per turn.

### NemoClaw (NVIDIA)

Open-source reference stack for running OpenClaw "more safely." Released
early-preview 2026-03-16 after an RCE vulnerability was disclosed in OpenClaw.

- **Sandbox**: Landlock LSM (filesystem) + seccomp (syscalls) + netns
  (network) at the kernel level.
- **Policy**: YAML in `nemoclaw-blueprint/` directory; static and dynamic
  rule changes with operator approval workflows.
- **Capability drops**: Container-level capability drops + process limits.
- **Sub-runtime**: NVIDIA OpenShell underneath.
- **Deployment**: `nemoclaw onboard` rather than direct OpenShell management.

### DefenseClaw (Cisco)

Enforcement+evidence layer. Released 2026-03-27 at RSAC 2026.

- **Architecture**: Python CLI operator + Go gateway sidecar + TypeScript
  OpenClaw plugin. Plugin → gateway → policy engine → scanners → audit store.
- **Policy language**: YAML + Rego.
- **Four enforcement axes**: Admission control (pre-execution scans),
  runtime guardrails (regex + optional LLM judge), sandbox controls, audit
  & observability.
- **Scanners**: CodeGuard for secrets/dangerous-execution/unsafe-deserialization
  /weak-crypto/injection-patterns/risky-file-access.
- **External catalog ingestion**: clawhub, smithery, skills.sh, http, git, file
  — with SSRF guards.
- **Identity**: Every Claw assigned unique identity + mapped to a human
  "sponsor."
- **Observability targets**: SQLite, JSONL, OTLP, Splunk, webhooks.
- **Operating modes**: "observe" (log only) and "action" (block on configured
  findings).

### The wider field

Constellation Research reported RSAC 2026 as "everyone trying to secure AI
agents, various 'claws.'" Multiple vendors converging on the same problem.

## Documented security incident corpus

Pinning specific incidents so spec-004 demos can be measured against them.

### Scale of exposure

- **SecurityScorecard STRIKE (Feb 2026)**: 40,214 exposed OpenClaw instances
  identified; 15,200 vulnerable to remote code execution.
- **Snyk ToxicSkills study**: 1,467 malicious payloads on ClawHub. 13.4% of
  all skills (534+) had at least one critical security issue (malware
  distribution, prompt injection, exposed secrets). 28 malicious skills in a
  2-day window late Jan 2026; 386 more in the following 2 days.

### Specific breach narratives

- **Mexican government breach (Dec 2025 – Jan 2026)**: Claude-driven agent
  breached multiple agencies. 150 GB exfiltrated including 195M taxpayer
  records and voter files.
- **OpenAI plugin supply chain attack**: Agent credentials harvested from 47
  enterprise deployments. 6 months undetected.
- **Meta director's agent**: Told "don't do anything until I say so."
  Compaction event triggered autonomous email deletion. Cited frequently as
  the "agent reliability cliff."

### Aggregate signals

- **65% of organizations** experienced at least one cybersecurity incident
  caused by AI agents on corporate networks in the past year.
- **Gartner projection**: 2,000+ AI-related legal claims by end of 2026 due
  to insufficient risk guardrails.
- **MCP server exposure**: 492 MCP servers exposed to the internet with zero
  authentication.

## CapableDeputy positioning analysis

### Where CD is distinctive vs the field

| Dimension | OpenClaw | NemoClaw | DefenseClaw | CapableDeputy |
|---|---|---|---|---|
| Decision model | LLM-driven | Pre-approved YAML | Scanner + regex + optional LLM judge | Deterministic four-axis engine, no LLM in path |
| Capability model | None | OS cap drops | Declarative (flag/block) | Unforgeable Capability + narrowing + delegation chains |
| Information flow | None | None | None | Four-axis (category x provenance x effect x decision context) with monotone composition |
| Override workflow | Approval prompts | Operator approval flow | Block/allow verdicts | Override-distinct-from-approval (FR-038); single-authorized / dual-control / disallowed FSM |
| Audit | Logs | Logs | Splunk/OTLP/JSONL/SQLite | Replay determinism (SC-002) |
| Data-blind planning | No | No | No | Pattern (3) ReferenceHandle |
| Sandbox | Optional Docker | Landlock+seccomp+netns | OS isolation atop NemoClaw | Port-only stub (spec 004 deferred until now) |
| Ecosystem | 13,700+ skills | + OpenShell | + Cisco AI Defense | 12 native tools + upstream/ adapter framework |

### CD's genuine advantages

1. **Capability-based access control** in the Capsicum/seL4 lineage rather than
   the "agent has perms, sandbox restricts" model the rest of the Claw field uses.
2. **No LLM in the decision path** (Principle I). DefenseClaw explicitly admits
   "optional LLM judge" in guardrails — that's exactly what Principle I forbids.
3. **Four-axis monotone IFC** from the academic lineage (Asbestos/HiStar/Flume/
   Aeolus), made operational. Other tools have at best single-axis "trust level."
4. **Override-distinct-from-approval** as a typed FSM with dual-control,
   friction confirmation, distinct capability origin, persistent storage.
5. **Pattern (3) data-blind planning** — the planner literally cannot see the
   raw value. Stops an entire class of "model leaks sensitive value into next
   prompt" failures. No other tool in the Claw space has this.
6. **Replay determinism** (SC-002). Audit record is sufficient to replay the
   same decision. None of the LLM-in-the-path systems can promise this.

### CD's honest weaknesses

1. **Substrate gap**. NemoClaw has Landlock+seccomp+netns shipping; CD has
   port-only stubs + an in-process demo. **Spec 004 is exactly this gap.**
2. **Tool ecosystem**. ClawHub: 13,700+ skills (and 1,184+ malicious). CD: 12
   native tools. **No ecosystem moat.**
3. **MCP server integration breadth**. OpenClaw consumes everything via skills;
   CD has the upstream/ adapter framework but nothing close to ClawHub's scale.
4. **Enterprise backing**. NVIDIA + Cisco are pushing their stacks. CD has no
   enterprise champion.
5. **Production exposure**. 0 deployed instances vs OpenClaw's 40K+.
6. **Policy authoring UX**. Operators hand-write YAML. No policy-authoring TUI.

## Strategic positioning

### Don't compete head-to-head with OpenClaw

OpenClaw has too much distribution; we'd never out-tool 13,700 skills with 12
natives. Position as **policy oracle**, not agent harness.

### Compete with DefenseClaw at the policy-oracle layer

That's where CD's distinctive IP (capability model + four-axis IFC +
override-distinct-from-approval + data-blind planning) is genuinely better
than what Cisco shipped. Buyer comparison: DefenseClaw's regex + LLM judge
vs CD's deterministic four-axis engine.

### Win the high-assurance / regulated niche

Healthcare, finance, government, classified workflows where BLP clearance +
replay determinism + capability narrowing are non-negotiable. NemoClaw and
DefenseClaw aim at general enterprise; CD wins where evidentiary-grade audit
and structural fail-closed are requirements.

## Integration targets for production usability

The integration substrate every real personal-assistant deployment needs.
These ARE the tasks in `tasks.md`.

### MCP servers — tier-1 vendor-maintained

These should all work with CD through a generic adapter without modification:

- **GitHub MCP** — code review, PR management, issue tracking
- **Google Workspace MCP** (Gmail, Calendar, Drive, Docs, Sheets, Slides,
  Forms, Tasks, Contacts, Chat) — dominant personal-assistant integration
- **Microsoft 365 MCP** — calendar/email parity for Outlook users
- **Notion MCP** — knowledge base
- **Slack MCP** — chat surface
- **Playwright MCP** — web automation
- **Context7 MCP** — live docs (already used by Claude Code)
- **Anthropic's 7 reference servers** — filesystem, github, gitlab, postgres,
  sqlite, brave-search, puppeteer

### Native tools to ship

- **fs.\*** — local file operations (read, write, create, modify, delete)
  with binding-canonicalized paths
- **web.search** — read-only web search through Brave / DuckDuckGo / SearXNG
  with FR-023 fail-closed on unbound destinations
- **code.execute** — code execution in a container substrate

### Container substrate (spec-004 actuator)

- **Podman** — rootless container for code execution
- **Modal** — hosted code sandbox
- **Firecracker / Kata / gVisor** — kernel-level isolation primitives (the
  same substrate NemoClaw uses)

### Observability

- **OTLP** — to match DefenseClaw's enterprise pipelines
- **Splunk** — DefenseClaw target; share the integration

### Authentication / identity

- **WebAuthn / Passkey** — for the dual-control attester surface (operator
  unlocks override grants)
- **Duo** — second-factor for the Axis-D `authentication` field
- **OAuth 2.1 / device flow** — for remote-service sign-in to MCP servers

## What CD should test itself against

Two concrete demos with high credibility against the documented incident corpus:

1. **"Re-run the Meta director scenario."** Reproduce the autonomous-email-
   deletion path. Show how FR-019 reversibility-irreversible + optimistic-auto
   carve-out prevents the unattended-delete even when the agent's planning
   state is corrupted by compaction.
2. **"Survive a ToxicSkills payload."** Pick 5 of the 1,467 documented
   malicious ClawHub skills, install them as ToolDefinitions with T012 fields
   forged by the malicious authors, show how capability narrowing + axis-B
   taint propagation + FR-031 asymmetry (non-deterministic relax refused)
   refuse the documented attacks.

Publishing those two write-ups with audit logs as evidence is the closest
thing to a real, substantively-true positioning claim against the "claws"
landscape.

## Sources

- [OpenClaw](https://openclaw.ai/), [OpenClaw GitHub](https://github.com/openclaw/openclaw)
- [How OpenClaw Works (MintMCP)](https://www.mintmcp.com/blog/openclaw-works-architecture-skills-security)
- [OpenClaw Security Risks (Cyberdesserts)](https://blog.cyberdesserts.com/openclaw-malicious-skills-security/)
- [OpenClaw AI Agent Flaws (Hacker News)](https://thehackernews.com/2026/03/openclaw-ai-agent-flaws-could-enable.html)
- [Personal AI Agents like OpenClaw Are a Security Nightmare (Cisco)](https://blogs.cisco.com/ai/personal-ai-agents-like-openclaw-are-a-security-nightmare)
- [Snyk ToxicSkills study](https://snyk.io/blog/toxicskills-malicious-ai-agent-skills-clawhub/)
- [NVIDIA NemoClaw](https://www.nvidia.com/en-us/ai/nemoclaw/), [NemoClaw GitHub](https://github.com/NVIDIA/NemoClaw)
- [What NemoClaw doesn't cover (Natoma)](https://natoma.ai/blog/what-nvidia-nemoclaw-doesn-t-cover-and-why-it-matters-for-enterprise-agents)
- [Cisco DefenseClaw GitHub](https://github.com/cisco-ai-defense/defenseclaw)
- [DefenseClaw documentation](https://cisco-ai-defense.github.io/docs/defenseclaw)
- [Cisco DefenseClaw announcement](https://blogs.cisco.com/ai/cisco-announces-defenseclaw)
- [Cisco puts a 'Claw' on AI security (SiliconANGLE)](https://siliconangle.com/2026/03/24/agentic-workforce-cisco-just-put-claw-ai-security/)
- [RSAC 2026: various 'claws' (Constellation Research)](https://www.constellationr.com/insights/news/rsac-2026-everyone-trying-secure-ai-agents-various-claws)
- [AI Agent Security Incidents Hit 65% (Kiteworks)](https://www.kiteworks.com/cybersecurity-risk-management/ai-agent-security-incidents-2026/)
- [50 Most Popular MCP Servers (MCPManager)](https://mcpmanager.ai/blog/most-popular-mcp-servers/)
- [What OpenClaw reveals about agentic AI security (IBM)](https://www.ibm.com/think/x-force/what-openclaw-reveals-about-agentic-ai-security-risks)
