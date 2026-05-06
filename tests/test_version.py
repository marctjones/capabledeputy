from capabledeputy import __version__


def test_version_is_non_empty_string() -> None:
    assert isinstance(__version__, str)
    assert len(__version__) > 0
