#!/usr/bin/env python3
"""
Cardinal — backend interface (DESIGN.md §12).

The compiler pipeline is:

    AST  ->  type-check (annotates types)  ->  lower to IR  ->  Backend.emit(IR)

Everything above the IR is target-independent. A `Backend` consumes an `IRModule`
and produces output for one target. The C backend (`backend_c.CBackend`) emits
portable C and defers optimization to the host C compiler. The planned native
backend (`backend_x86.X86Backend`) will consume the *same IR* and do its own
instruction selection / register allocation / relocatable-object emission
(ELF preferred, for the custom OS) — so target-independent optimization passes
belong on the IR, not inside any single backend.

To add a backend: subclass `Backend`, implement `name`, `output_suffix`, and
`emit(ir_module) -> bytes|str`, and (optionally) `build(...)` to turn the emitted
artifact into a runnable program. Register it in `BACKENDS`.
"""

from __future__ import annotations
import abc


class Backend(abc.ABC):
    #: short identifier, e.g. "c" or "x86_64"
    name: str = "abstract"
    #: file suffix for the primary emitted artifact, e.g. ".c" or ".o"
    output_suffix: str = ".out"

    @abc.abstractmethod
    def emit(self, ir_module) -> str | bytes:
        """Translate an IRModule into target text/bytes."""
        raise NotImplementedError

    def build(self, artifact_path: str, exe_path: str) -> None:
        """Optional: turn the emitted artifact into a runnable program.

        The C backend shells out to a C compiler; a native backend would link
        relocatable objects. Backends that emit a finished binary may no-op.
        """
        raise NotImplementedError(f"{self.name} backend cannot build executables")


def get_backend(name: str) -> Backend:
    if name not in BACKENDS:
        raise KeyError(f"unknown backend {name!r}; have {sorted(BACKENDS)}")
    return BACKENDS[name]()


# Registry. Imports are local to avoid a hard dependency cycle at import time.
def _make_c():
    from backend_c import CBackend
    return CBackend()


BACKENDS = {
    "c": _make_c,
    # "x86_64": _make_x86,   # future: native relocatable-object backend
}
