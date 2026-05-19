# Contract: Source/Location Binding & Adapter Port (003)

The port the in-TCB Source/Location Binding resolver (FR-043) expects every source adapter to honor. Adapters (filesystem MCP, Microsoft Graph, network shares, channels) live **outside** the TCB behind this port (Constitution VII); the contract is mandatory.

## Resolver interface (in-TCB)

```python
class SourceBindingResolver(Protocol):
    def resolve_read(self, resource_handle: ResourceHandle) -> ResolvedLabels:
        """At ingest: map a canonical resource handle to (axis_a, axis_b, optional reversibility/mutability/write-discipline) via the operator-declared bindings. Most-specific subtree wins; overlapping bindings compose most-restrictive. Unbound or non-canonicalizable handle → fail-closed (most-restrictive default; FR-023/FR-043)."""
        ...

    def resolve_destination(self, destination_canonical_id: str) -> BindingMatch | None:
        """At write/egress: match a canonical destination id against named bindings for named-rule lookup (e.g., HR-folder → TeamSharePoint deny). Returns the matching binding or None if unbound; the caller MUST fail-closed on unbound or non-canonicalizable destinations (FR-048)."""
        ...
```

## Adapter contract (port, outside TCB)

Every adapter that surfaces *read* or *write/egress* effects MUST provide:

```python
class SourcePort(Protocol):
    # READ side
    def read(self, query: ReadQuery) -> ReadResult:
        """Return content + a canonical ResourceHandle the resolver can label."""
        ...

    def canonical_resource_handle(self, raw_handle: Any) -> ResourceHandle:
        """Canonicalize: symlink → realpath; UNC alias → normalized; SharePoint drive-item → site URL + item id; etc. If the input cannot be decidably canonicalized, raise NotCanonicalizable (the caller MUST treat as fail-closed; FR-043 edge case)."""
        ...

    # WRITE/EGRESS side
    def write(self, target: TargetRef, content: Content) -> WriteResult:
        """Perform the write/egress effect. MUST return a WriteResult including the canonical destination identifier the runtime will pass to decide()."""
        ...

    def canonical_destination_id(self, target: TargetRef) -> str:
        """Canonical destination identifier for the target (FR-048). MUST be deterministic and match the namespace used in source_bindings (e.g., 'https://acme.sharepoint.com/sites/team/**'). MUST raise NotCanonicalizable rather than return an ambiguous string; the caller MUST then fail-closed."""
        ...

    def surfaces_destination_id(self) -> bool:
        """Adapter capability self-declaration (matches ToolDefinition.surfaces_destination_id). MUST return True; an adapter that cannot satisfy FR-048 MUST be refused registration (Principle VI)."""
        ...
```

## Invariants

1. **Canonicalization is total or fail-closed.** No adapter MAY return an ambiguous handle/destination silently; `NotCanonicalizable` is the only sanctioned non-answer (FR-048).
2. **Labeling is applied at ingest, not at use.** The resolver runs on every read; labels are attached to the value at the moment the runtime acquires it (FR-022 assignment provenance recorded inline).
3. **AI-read-only.** No adapter call MAY mint or mutate bindings; the binding store is human-declared config.
4. **Out-of-TCB.** A compromised adapter MUST NOT be able to relax outcomes — every effect still passes `decide()` with the labels the resolver applied; the adapter's failure mode is at worst denial-of-service or visible refusal, never a permissive bypass (Constitution VII).

## CI invariant tests required

- `test_unbound_resource_fail_closed`: an unbound read resolves to most-restrictive (`prohibited` if `fixed-high` was declared, else the category default's strictest tier).
- `test_unidentifiable_destination_fail_closed`: `canonical_destination_id` raising `NotCanonicalizable` produces `DENY` in `decide()` for any rule referencing the binding family (FR-048).
- `test_adapter_registration_refuses_missing_surfaces_destination_id`: a `ToolDefinition` or adapter that returns `surfaces_destination_id() = False` is refused at daemon startup.
- `test_overlapping_bindings_most_restrictive`: subtree binding on `~/HR/employees/<x>/**` may only *raise* restriction over the parent `~/HR/**` binding.
