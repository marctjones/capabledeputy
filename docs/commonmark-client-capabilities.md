# CommonMark client capability matrix

CapDep treats assistant prose as CommonMark when a client surface can render it,
but each surface renders only what it can support reliably. All surfaces first
neutralize terminal control sequences, raw HTML outside fenced code blocks, and
unsafe link schemes such as `javascript:`, `data:`, and `vbscript:`.

| Surface | Capability level | Renders | Predictable fallback |
|---|---|---|---|
| CapDepMac | Rich | paragraphs, emphasis, links, lists, blockquotes, inline code, fenced code, trusted image attachments | raw HTML removed; unsafe links rewritten; oversized/unsupported tables remain readable text |
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
