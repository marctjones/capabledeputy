"""003 substrate ports (interfaces only).

This package declares ports the TCB consumes (per Constitution
Principle VII: external substrate behind in-repo ports). Provider
implementations live in spec 004. Importing a port without an
implementation raises NotImplementedError at first call — never
silently best-effort.

Members:
- source_port.py      (deferred to T075 — US6)
- version_write_port.py (deferred to T075 — US6)
- sandbox_actuator.py (deferred to T085 — US6)
- inspector_port.py   (T121 — Phase 2a, here)
"""
