from uuid import uuid4

from capabledeputy.policy.labels import Label
from capabledeputy.tools.native.memory import LabeledMemoryStore, make_memory_tools
from capabledeputy.tools.registry import ToolContext


def _ctx(label_set: frozenset[Label] = frozenset()) -> ToolContext:
    return ToolContext(session_id=uuid4(), label_set=label_set)


async def test_write_then_read_round_trips_value() -> None:
    store = LabeledMemoryStore()
    tools = {t.name: t for t in make_memory_tools(store)}

    await tools["memory.write"].handler({"key": "k", "value": "hello"}, _ctx())
    result = await tools["memory.read"].handler({"key": "k"}, _ctx())
    assert result.output == {"found": True, "value": "hello"}


async def test_write_records_label_set_at_call_time() -> None:
    store = LabeledMemoryStore()
    tools = {t.name: t for t in make_memory_tools(store)}

    ctx = _ctx(frozenset({Label.CONFIDENTIAL_HEALTH, Label.CONFIDENTIAL_PERSONAL}))
    await tools["memory.write"].handler({"key": "k", "value": "x"}, ctx)
    assert store.labels_of("k") == frozenset(
        {Label.CONFIDENTIAL_HEALTH, Label.CONFIDENTIAL_PERSONAL},
    )


async def test_read_returns_labels_as_additional() -> None:
    store = LabeledMemoryStore()
    tools = {t.name: t for t in make_memory_tools(store)}

    write_ctx = _ctx(frozenset({Label.CONFIDENTIAL_HEALTH}))
    await tools["memory.write"].handler({"key": "k", "value": "v"}, write_ctx)

    read_result = await tools["memory.read"].handler({"key": "k"}, _ctx())
    assert Label.CONFIDENTIAL_HEALTH in read_result.additional_labels


async def test_read_unknown_returns_not_found() -> None:
    store = LabeledMemoryStore()
    tools = {t.name: t for t in make_memory_tools(store)}

    result = await tools["memory.read"].handler({"key": "missing"}, _ctx())
    assert result.output == {"found": False}
    assert result.additional_labels == frozenset()


async def test_overwrite_replaces_value_and_labels() -> None:
    store = LabeledMemoryStore()
    tools = {t.name: t for t in make_memory_tools(store)}

    await tools["memory.write"].handler(
        {"key": "k", "value": "first"},
        _ctx(frozenset({Label.CONFIDENTIAL_HEALTH})),
    )
    await tools["memory.write"].handler(
        {"key": "k", "value": "second"},
        _ctx(frozenset({Label.CONFIDENTIAL_PERSONAL})),
    )

    read = await tools["memory.read"].handler({"key": "k"}, _ctx())
    assert read.output == {"found": True, "value": "second"}
    assert read.additional_labels == frozenset({Label.CONFIDENTIAL_PERSONAL})


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
    assert "memory.write" in by_name
    assert by_name["memory.read"].target_arg == "key"
    assert by_name["memory.write"].target_arg == "key"
