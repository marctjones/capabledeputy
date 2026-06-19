from uuid import UUID, uuid4

from capabledeputy.patterns.reference_handle import ReferenceHandleStore
from capabledeputy.policy.labels import CategoryTag, LabelState, Tier
from capabledeputy.tools.native.memory import LabeledMemoryStore, make_memory_tools
from capabledeputy.tools.registry import ToolContext


def _ctx(label_state: LabelState | None = None) -> ToolContext:
    return ToolContext(session_id=uuid4(), label_state=label_state or LabelState())


def _ctx_with_handles(
    label_state: LabelState | None = None,
    handle_store: ReferenceHandleStore | None = None,
) -> ToolContext:
    return ToolContext(
        session_id=uuid4(),
        label_state=label_state or LabelState(),
        handle_store=handle_store,
    )


async def test_write_then_read_round_trips_value() -> None:
    store = LabeledMemoryStore()
    tools = {t.name: t for t in make_memory_tools(store)}

    await tools["memory.write"].handler({"key": "k", "value": "hello"}, _ctx())
    result = await tools["memory.read"].handler({"key": "k"}, _ctx())
    assert result.output == {"found": True, "value": "hello"}


async def test_write_records_label_set_at_call_time() -> None:
    store = LabeledMemoryStore()
    tools = {t.name: t for t in make_memory_tools(store)}

    label_state = LabelState(
        a=frozenset(
            {
                CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared"),
                CategoryTag("personal", Tier.REGULATED, assignment_provenance="source-declared"),
            }
        )
    )
    ctx = _ctx(label_state)
    await tools["memory.write"].handler({"key": "k", "value": "x"}, ctx)
    assert store.labels_of("k") == label_state


async def test_read_returns_labels_as_additional() -> None:
    store = LabeledMemoryStore()
    tools = {t.name: t for t in make_memory_tools(store)}

    write_label_state = LabelState(
        a=frozenset(
            {CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared")}
        )
    )
    write_ctx = _ctx(write_label_state)
    await tools["memory.write"].handler({"key": "k", "value": "v"}, write_ctx)

    read_result = await tools["memory.read"].handler({"key": "k"}, _ctx())
    assert (
        CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared")
        in read_result.additional_tags.a
    )


async def test_read_unknown_returns_not_found() -> None:
    store = LabeledMemoryStore()
    tools = {t.name: t for t in make_memory_tools(store)}

    result = await tools["memory.read"].handler({"key": "missing"}, _ctx())
    assert result.output == {"found": False}
    assert result.additional_tags == LabelState()


async def test_overwrite_replaces_value_and_labels() -> None:
    store = LabeledMemoryStore()
    tools = {t.name: t for t in make_memory_tools(store)}

    health_label_state = LabelState(
        a=frozenset(
            {CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared")}
        )
    )
    personal_label_state = LabelState(
        a=frozenset(
            {CategoryTag("personal", Tier.REGULATED, assignment_provenance="source-declared")}
        )
    )

    await tools["memory.write"].handler(
        {"key": "k", "value": "first"},
        _ctx(health_label_state),
    )
    await tools["memory.write"].handler(
        {"key": "k", "value": "second"},
        _ctx(personal_label_state),
    )

    read = await tools["memory.read"].handler({"key": "k"}, _ctx())
    assert read.output == {"found": True, "value": "second"}
    assert read.additional_tags == personal_label_state


async def test_keys_lists_all_keys_sorted() -> None:
    store = LabeledMemoryStore()
    tools = {t.name: t for t in make_memory_tools(store)}

    for k in ("c", "a", "b"):
        await tools["memory.write"].handler({"key": k, "value": k}, _ctx())
    assert store.keys() == ["a", "b", "c"]


def test_tool_metadata() -> None:
    tools = make_memory_tools(LabeledMemoryStore())
    by_name = {t.name: t for t in tools}
    assert "memory.read" in by_name
    assert "memory.handle" in by_name
    assert "memory.write" in by_name
    assert by_name["memory.read"].target_arg == "key"
    assert by_name["memory.read"].source_label_lookup is not None
    assert by_name["memory.read"].forbid_restricted_source is True
    assert by_name["memory.handle"].target_arg == "key"
    assert by_name["memory.handle"].source_label_lookup is not None
    assert by_name["memory.handle"].forbid_restricted_source is False
    assert by_name["memory.write"].target_arg == "key"


async def test_memory_handle_returns_reference_without_raw_value() -> None:
    store = LabeledMemoryStore()
    handle_store = ReferenceHandleStore()
    tools = {t.name: t for t in make_memory_tools(store)}
    label_state = LabelState(
        a=frozenset(
            {CategoryTag("health", Tier.RESTRICTED, assignment_provenance="source-declared")}
        )
    )
    ctx = _ctx_with_handles(label_state, handle_store)

    await tools["memory.write"].handler({"key": "secret", "value": "BP=120/80"}, ctx)
    result = await tools["memory.handle"].handler({"key": "secret"}, ctx)

    assert result.output["found"] is True
    assert result.output["key"] == "secret"
    assert result.output["handle"] != "BP=120/80"
    assert "value" not in result.output
    bound = handle_store.bind(
        session_id=ctx.session_id,
        handle_id=UUID(result.output["handle"]),
        destination_canonical_id="tool:test",
        tool="test.consumer",
        audit_id=uuid4(),
    )
    assert bound == "BP=120/80"
