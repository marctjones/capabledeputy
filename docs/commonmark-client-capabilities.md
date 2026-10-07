# CommonMark client capability matrix

CapDep treats assistant prose as CommonMark when a client surface can render it,
but each surface renders only what it can support reliably. All surfaces first
neutralize terminal control sequences, raw HTML outside fenced code blocks, and
unsafe link schemes such as `javascript:`, `data:`, and `vbscript:`.

| Surface | Capability level | Renders | Predictable fallback |
|---|---|---|---|
| CapDepMac | Native text blocks; image-route fallback | TextKit paragraphs/headings, authored ordered and nested unordered lists, quotes, rules, inline/fenced/indented code, links, GFM tables/strikethrough/inert task lists/autolinks; continuous text selection for messages without Markdown image syntax | image-bearing messages retain separate admitted image/code/text views; oversized messages retain sanitized source; raw HTML removed and unsafe links inert; full heading/table AX relationships and all-route artifact admission remain tracked in #455/#453 |
| CLI/TUI | Terminal | Rich CommonMark renderables, width-aware code blocks, links when terminal supports them, trusted terminal images when supported | raw HTML removed; unsafe links rewritten; images become local paths/labels when terminal graphics are unavailable |
| MCP-control | Structured | sanitized text content, structuredContent, ImageContent for local images when host supports it | hosts that only render text receive readable sanitized CommonMark/plain text with no terminal escapes |
| Plain/log surfaces | Plain | plain text, code text, link text | formatting, tables, and images degrade to text labels or source paths |

In modern terminals such as Ghostty, kitty, iTerm2, WezTerm, and Alacritty, the
CLI/TUI renderer uses Rich's terminal Markdown output rather than raw
CommonMark: headings, emphasis, lists, blockquotes, links, tables, and fenced
code are styled for the terminal, code blocks use a truecolor syntax theme, and
trusted local images can render inline when the terminal advertises compatible
graphics support. In dumb terminals, redirected output, scripts, and logs, the
same sanitized source degrades to deterministic text.

The shared fixture corpus lives under `tests/fixtures/commonmark/` and covers
headings, lists, links, fenced code, images, raw HTML, unsafe links, malformed
or partial image syntax, and terminal-control injection. Client tests assert the
same fixtures at their declared capability level instead of pretending every
interface can render identical rich output.

## Native text contract implemented — 2026-10-07

The no-image transcript path uses the pinned Swift Markdown 0.6.0 AST and
swift-cmark 0.9.0, with exact package constraints and `Package.resolved`
revisions. Both packages use Apache 2.0 with the Swift runtime exception.
[Swift Markdown](https://github.com/swiftlang/swift-markdown/tree/0.6.0)
uses cmark-gfm; no HTML renderer or network-capable document view is introduced.
AST nodes become AppKit attributed paragraphs in a single selectable NSTextView;
GFM cells use NSTextTable blocks with header emphasis and declared alignment.
Tables wrap inside the message width. This does not yet promise horizontal
scrolling for extremely wide tables or semantic VoiceOver header/cell navigation.

The supported block/inlines are paragraphs, ATX/setext headings, ordered and
unordered nested lists, blockquotes, thematic breaks, inline/fenced/indented
code, emphasis/strong, links including reference links, GFM strikethrough,
task-list indicators and autolinks. Authored ordered markers come from source
locations, so `4.` followed by `7.` remains `4.` followed by `7.`. Task indicators
are inert text. Links activate only explicit http/https/mailto destinations;
file and custom schemes remain text. Raw HTML is removed; no scripts run.
Unicode uses native text direction and line breaking. Math, footnotes, diagram
languages and float metadata remain source text/deferred extensions.

Copy Markdown writes the original message string without changing wording,
numbering or whitespace. Native selection copies the rendered text, including
visible list markers and breaks; native rich copy retains table layout while
plain-text table selection follows source cell order. Copy code uses cmark's
code content (including leading blank lines, indentation and trailing newline),
without fence markers or language metadata. Reference/escaped markup is interpreted
for display and retained by Copy Markdown. Selection across text blocks is one
native range. Image-bearing messages still use separate views and do not yet
provide continuous selection across figures. Approval envelopes use their
existing exact-byte review view, independently of transcript formatting.

Body text defaults to 14 macOS points and offers 16/18/22-point enlargement.
Headings use proportional native bold fonts; code uses a monospaced font.
Messages exceeding 256,000 UTF-8 bytes fall back to sanitized source, with no
content truncation. Unchanged messages avoid rebuilding their native text
storage; completed-code menus parse only when source changes. Streaming content
is reparsed within the message budget; large-transcript token latency and
VoiceOver announcement budgets still require #348/#451 acceptance.

The primary chat is Work. Its conversation menu identifies the current session
by ID, its posture bar shows daemon-listed intent, and Browse all conversations
opens a searchable list of persisted daemon sessions. Console remains the
secondary session/artifact/security route; Email Replies retains its selected
source session. Approvals and Activity retain existing routes. No new
Sessions/Outputs/Profiles shell or authorization source is introduced.
Unsaved composer text is retained per session for the lifetime of the chat
view. Conversation switching is disabled during a turn, until completion or
cancellation, to avoid moving control/status to a different conversation.

Reading follows the latest content only while following is already enabled.
A user scroll away from the bottom pauses following; appending content alone
does not change that intent. Jump to latest restores following. Session changes
reset follow intent; reconnect/history updates retain the existing scroll intent.
Fine-grained restored-history anchor identity and image-spanning selection remain
#472 acceptance work.

Shared fixture `tests/fixtures/commonmark/native-semantic.md` covers paragraphs,
headings, authored/nonconsecutive list numbers, nesting, tasks, quotes, table
alignment/wrapping, code whitespace, links, strikethrough and Unicode. Swift
regressions cover malformed fences, unsafe destinations, large-input fallback,
native table attributes, selection-copy and compact/enlarged layout. These tests
are distinct from full-app native/AX/keyboard and streaming acceptance.
