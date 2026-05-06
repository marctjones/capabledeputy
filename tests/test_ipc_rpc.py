import json

from capabledeputy.ipc.rpc import (
    JSONRPC_VERSION,
    METHOD_NOT_FOUND,
    RpcRequest,
    RpcResponse,
    error,
    parse_request,
    parse_response,
)


def test_request_encode_decode_round_trip() -> None:
    req = RpcRequest(method="version", params={"foo": "bar"}, id=42)
    line = req.encode()
    decoded = parse_request(line)
    assert decoded.method == "version"
    assert decoded.params == {"foo": "bar"}
    assert decoded.id == 42
    assert decoded.jsonrpc == JSONRPC_VERSION


def test_request_encode_omits_empty_params() -> None:
    req = RpcRequest(method="ping")
    line = req.encode()
    obj = json.loads(line)
    assert "params" not in obj


def test_request_encode_terminates_with_newline() -> None:
    req = RpcRequest(method="ping", id=1)
    assert req.encode().endswith(b"\n")


def test_response_with_result_round_trip() -> None:
    resp = RpcResponse(id=1, result={"version": "0.0.1"})
    line = resp.encode()
    decoded = parse_response(line)
    assert decoded.id == 1
    assert decoded.result == {"version": "0.0.1"}
    assert decoded.error is None


def test_response_with_error_round_trip() -> None:
    err = error(METHOD_NOT_FOUND, "method not found: foo")
    resp = RpcResponse(id=2, error=err)
    line = resp.encode()
    decoded = parse_response(line)
    assert decoded.id == 2
    assert decoded.result is None
    assert decoded.error == err


def test_error_helper_sets_data_when_provided() -> None:
    err = error(METHOD_NOT_FOUND, "method not found", data={"method": "foo"})
    assert err == {
        "code": METHOD_NOT_FOUND,
        "message": "method not found",
        "data": {"method": "foo"},
    }


def test_error_helper_omits_data_when_none() -> None:
    err = error(METHOD_NOT_FOUND, "method not found")
    assert "data" not in err


def test_parse_request_rejects_non_object() -> None:
    import pytest

    with pytest.raises(ValueError):
        parse_request(b"[]")


def test_parse_request_requires_method() -> None:
    import pytest

    with pytest.raises(ValueError):
        parse_request(b'{"id": 1}')
