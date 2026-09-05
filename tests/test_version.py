import tomllib
from pathlib import Path

from capabledeputy import __version__


def _pyproject_version() -> str:
    with Path("pyproject.toml").open("rb") as source:
        return tomllib.load(source)["project"]["version"]


def test_version_is_non_empty_string() -> None:
    assert isinstance(__version__, str)
    assert len(__version__) > 0


def test_module_and_pyproject_versions_match() -> None:
    assert __version__ == _pyproject_version()
