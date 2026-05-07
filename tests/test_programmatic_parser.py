"""Programmatic-mode parser: enforces the AST-subset contract.

Every program must parse cleanly under the restricted grammar before
the evaluator ever sees it. Forbidden constructs (import, class, def,
try/except, with, while, comprehensions, attribute access, etc.) are
rejected at parse time so untrusted code paths cannot rely on them.
"""

from __future__ import annotations

import pytest

from capabledeputy.programmatic.errors import ProgramSyntaxError
from capabledeputy.programmatic.parser import parse_program


def test_simple_program_parses() -> None:
    src = """
x = 1
y = x + 2
z = call("memory.read", key="patient.notes")
"""
    parse_program(src)


def test_if_for_pass_break_continue_parse() -> None:
    src = """
total = 0
for i in range(5):
    if i == 3:
        break
    if i == 1:
        continue
    total = total + i
"""
    parse_program(src)


@pytest.mark.parametrize(
    "src",
    [
        "import os\n",
        "from os import path\n",
        "class C:\n    pass\n",
        "def f():\n    pass\n",
        "lambda x: x\n",
        "try:\n    x = 1\nexcept Exception:\n    pass\n",
        "with open('x') as f:\n    pass\n",
        "while True:\n    pass\n",
        "x = [i for i in range(3)]\n",
        "x = {i for i in range(3)}\n",
        "x = {i: i for i in range(3)}\n",
        "x = (i for i in range(3))\n",
        "del x\n",
        "raise ValueError('x')\n",
    ],
)
def test_forbidden_constructs_rejected(src: str) -> None:
    with pytest.raises(ProgramSyntaxError):
        parse_program(src)


def test_attribute_access_rejected() -> None:
    with pytest.raises(ProgramSyntaxError, match="attribute access"):
        parse_program("x = some_obj.attr\n")


def test_attribute_call_rejected() -> None:
    with pytest.raises(ProgramSyntaxError, match="attribute access"):
        parse_program("x = 'hi'.upper()\n")


def test_attribute_assignment_rejected() -> None:
    # Attribute on either side is rejected because the read is forbidden.
    with pytest.raises(ProgramSyntaxError):
        parse_program("obj.x = 1\n")


def test_kwargs_unpacking_rejected() -> None:
    with pytest.raises(ProgramSyntaxError, match="kwargs"):
        parse_program("call('memory.read', **kwargs)\n")


def test_python_syntax_error_wrapped() -> None:
    with pytest.raises(ProgramSyntaxError, match="parse error"):
        parse_program("x =\n")
