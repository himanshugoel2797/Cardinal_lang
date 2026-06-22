#!/usr/bin/env python3
"""
Cardinal — target-independent intermediate representation.

A small SSA-free linear IR: functions made of basic blocks; each block is a list
of instructions ending in a terminator (Br / CondBr / Ret). Operands are `Val`s
(temporaries, locals, or immediates). Cardinal variables become named locals you
read/write directly; intermediate results become fresh temporaries. This is low
enough for a native backend to register-allocate, and structured enough that the
C backend can emit it directly.

Types reuse `typecheck`'s type lattice (IntT/FloatT/PrimT/StructT/EnumT/ArrayT)
so the whole pipeline speaks one type language.
"""

from __future__ import annotations
from dataclasses import dataclass, field

from typecheck import IntT, FloatT, PrimT, StructT, EnumT, ArrayT, FuncT  # noqa


# --------------------------------------------------------------------------- #
# Operands
# --------------------------------------------------------------------------- #

@dataclass
class Temp:
    id: int
    ty: object
    def __str__(self): return f"%t{self.id}"

@dataclass
class Local:
    name: str
    ty: object
    def __str__(self): return f"%{self.name}"

@dataclass
class Imm:
    value: object        # int / float / bool / str / None(unit)
    ty: object
    def __str__(self): return f"{self.value!r}"


# --------------------------------------------------------------------------- #
# Places (lvalues) for stores into struct fields / variables
# --------------------------------------------------------------------------- #

@dataclass
class PLocal:
    name: str
    ty: object

@dataclass
class PField:
    base: object         # a Place
    field: str
    ty: object

@dataclass
class PDeref:            # *ptr  (used for by-reference / boxed captured variables)
    ptr: object          # a Val of pointer type
    ty: object


# --------------------------------------------------------------------------- #
# Pointer type (IR-only; the source language has no raw pointers)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class PtrT:
    pointee: object      # an IR/typecheck type, or None for void*


# --------------------------------------------------------------------------- #
# Instructions
# --------------------------------------------------------------------------- #

@dataclass
class Const:    dst: Temp; value: object; ty: object
@dataclass
class Bin:      dst: Temp; op: str; lhs: object; rhs: object; ty: object; checked: bool = False
@dataclass
class Un:       dst: Temp; op: str; val: object; ty: object; checked: bool = False
@dataclass
class Cast:     dst: Temp; val: object; toty: object
@dataclass
class Assign:   place: object; src: object          # store into a Place (var/field)
@dataclass
class LoadField: dst: Temp; base: object; field: str; ty: object
@dataclass
class Call:     dst: object; callee: str; args: list; ty: object   # dst None => void
@dataclass
class StructNew: dst: Temp; struct: str; fields: list   # [(name, Val)] in declared order
@dataclass
class ArrNew:   dst: Temp; elem: object; count: object
@dataclass
class ArrLit:   dst: Temp; elem: object; elems: list
@dataclass
class ArrGet:   dst: Temp; arr: object; idx: object; ty: object
@dataclass
class ArrSet:   arr: object; idx: object; val: object; elem: object
@dataclass
class ArrLen:   dst: Temp; arr: object
@dataclass
class EnumConst: dst: Temp; enum: str; variant: str; intval: int

# --- closures (closure-converted) --- #
@dataclass
class Alloc:       dst: Temp; ty: object              # heap box: dst = ptr to fresh cell of ty
@dataclass
class Load:        dst: Temp; ptr: object; ty: object  # dst = *ptr
@dataclass
class EnvNew:      dst: Temp; n: int                   # dst = void**[n]
@dataclass
class EnvStore:    env: object; idx: int; ptr: object  # env[idx] = ptr
@dataclass
class EnvLoad:     dst: Temp; env: object; idx: int; ty: object  # dst = (ty*)env[idx]
@dataclass
class MakeClosure: dst: Temp; fn: str; env: object     # dst = {fn, env}
@dataclass
class CallClosure: dst: object; clos: object; args: list; ret: object; ptys: list

# terminators
@dataclass
class Br:       target: str
@dataclass
class CondBr:   cond: object; then: str; els: str
@dataclass
class Ret:      val: object                          # None for unit
@dataclass
class Panic:    msg: object


TERMINATORS = (Br, CondBr, Ret)


# --------------------------------------------------------------------------- #
# Containers
# --------------------------------------------------------------------------- #

@dataclass
class Block:
    label: str
    instrs: list = field(default_factory=list)
    def terminated(self):
        return self.instrs and isinstance(self.instrs[-1], TERMINATORS)

@dataclass
class IRFunc:
    name: str                        # mangled, target-ready
    params: list                     # [(name, ty)]
    ret: object
    blocks: list = field(default_factory=list)
    temps: list = field(default_factory=list)     # all Temp (for declaration)
    locals: list = field(default_factory=list)    # [(name, ty)] non-param locals

@dataclass
class IRStruct:
    name: str
    fields: list                     # [(name, ty)]

@dataclass
class IRModule:
    name: str
    structs: list = field(default_factory=list)   # IRStruct
    funcs: list = field(default_factory=list)      # IRFunc
    externs: set = field(default_factory=set)      # builtin/runtime symbols used
    thunks: dict = field(default_factory=dict)     # mangled fn name -> FuncT (function-as-value)


# --------------------------------------------------------------------------- #
# Builder — helper used by the lowering pass
# --------------------------------------------------------------------------- #

class FuncBuilder:
    def __init__(self, name, params, ret):
        self.func = IRFunc(name, params, ret)
        self._tc = 0
        self._bc = 0
        self.block = None
        self.new_block("entry")

    def temp(self, ty):
        t = Temp(self._tc, ty); self._tc += 1
        self.func.temps.append(t)
        return t

    def local(self, name, ty):
        self.func.locals.append((name, ty))
        return Local(name, ty)

    def label(self, hint="L"):
        self._bc += 1
        return f"{hint}{self._bc}"

    def new_block(self, label):
        b = Block(label)
        self.func.blocks.append(b)
        self.block = b
        return b

    def emit(self, instr):
        if self.block.terminated():
            # unreachable code after a terminator — start a dead block
            self.new_block(self.label("dead"))
        self.block.instrs.append(instr)
        return instr

    def terminated(self):
        return self.block.terminated()
