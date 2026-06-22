#!/usr/bin/env python3
"""
Cardinal — static type checker (DESIGN.md §5, §10).

A separate ahead-of-time pass over the AST produced by `interpreter.py`. It
enforces the static rules the interpreter only checked dynamically:

  * no implicit numeric promotion — operands of an op share a type, conversions
    need an explicit `(as v T)` cast;
  * numeric literals are context-typed; an uninferable literal is an error;
  * `null` is assignable to any reference type; deref/field rules;
  * struct field / array element / function argument / return types must match;
  * conditions are `bool`; `set` targets are mutable and type-compatible;
  * module visibility (`export`) and `::` path resolution.

This mirrors the interpreter's coercion logic at the type level. It is used by
the `cardinal` driver; run it before evaluation to catch errors early.
"""

from __future__ import annotations
import os
from dataclasses import dataclass

from interpreter import (
    lex, Parser, CardinalError,
    Module, Import, FuncDecl, StructDecl, EnumDecl, SumDecl, ConstDecl,
    Let, Set, Do, If, While, ForNum, ForIn, Loop, Break, Continue, Return, Checked, Match, Pass,
    IntLit, FloatLit, BoolLit, CharLit, StrLit, NullLit, Name, Path,
    FieldAccess, Index, Call, OpCall, StructLit, ArrayLit, ArrayNew,
    VecLit, VecNew, MapNew, Cast, Ref, FuncLit,
    TyName, TyArray, TyVec, TyMap, TyFunc,
    INT_TYPES, FLOAT_TYPES,
)


# --------------------------------------------------------------------------- #
# Type representation
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class IntT:    name: str
@dataclass(frozen=True)
class FloatT:  name: str
@dataclass(frozen=True)
class PrimT:   name: str        # bool, char, str, unit, handle
@dataclass(frozen=True)
class StructT: name: str
@dataclass(frozen=True)
class EnumT:   name: str
@dataclass(frozen=True)
class SumT:    name: str        # tagged-union / sum type
@dataclass(frozen=True)
class ArrayT:  elem: object
@dataclass(frozen=True)
class VecT:    elem: object        # builtin generic growable vector {T}
@dataclass(frozen=True)
class MapT:    key: object; val: object   # builtin generic map {K V}
@dataclass(frozen=True)
class FuncT:   params: tuple; ret: object
@dataclass(frozen=True)
class UntypedIntT:   pass        # an unsuffixed integer literal
@dataclass(frozen=True)
class UntypedFloatT: pass        # an unsuffixed float literal
@dataclass(frozen=True)
class NullT:   pass              # the type of `null` (assignable to any ref type)
@dataclass(frozen=True)
class NeverT:  pass              # `panic(...)` — bottom type, fits anywhere

BOOL = PrimT("bool"); CHAR = PrimT("char"); STR = PrimT("str")
UNIT = PrimT("unit"); HANDLE = PrimT("handle")
U64 = IntT("u64")
UINT_LIT = UntypedIntT(); UFLOAT_LIT = UntypedFloatT()
NULL = NullT(); NEVER = NeverT()

REF_KINDS = (StructT, EnumT, ArrayT, FuncT, PrimT)   # PrimT only str/handle are refs


def tystr(t):
    if isinstance(t, (IntT, FloatT, PrimT)): return t.name
    if isinstance(t, StructT): return t.name
    if isinstance(t, EnumT): return t.name
    if isinstance(t, SumT): return t.name
    if isinstance(t, ArrayT): return f"[{tystr(t.elem)}]"
    if isinstance(t, VecT): return f"{{{tystr(t.elem)}}}"
    if isinstance(t, MapT): return f"{{{tystr(t.key)} {tystr(t.val)}}}"
    if isinstance(t, FuncT):
        return f"func({' '.join(tystr(p) for p in t.params)} -> {tystr(t.ret)})"
    if isinstance(t, UntypedIntT): return "{untyped int}"
    if isinstance(t, UntypedFloatT): return "{untyped float}"
    if isinstance(t, NullT): return "null"
    if isinstance(t, NeverT): return "never"
    return str(t)


def is_hashable(t):
    """Types usable as map keys (value-semantic equality + cheap hash)."""
    return (isinstance(t, (IntT, EnumT))
            or (isinstance(t, PrimT) and t.name in ("str", "char", "bool")))


def is_reference_type(t):
    if isinstance(t, (StructT, EnumT, SumT, ArrayT, VecT, MapT, FuncT)):
        return True
    if isinstance(t, PrimT) and t.name in ("str", "handle"):
        return True
    return False


# --------------------------------------------------------------------------- #
# Module signatures
# --------------------------------------------------------------------------- #

class ModSig:
    def __init__(self, name):
        self.name = name
        self.module = None       # parsed Module AST (None for builtins like io)
        self.funcs = {}          # name -> FuncT
        self.consts = {}         # name -> Type
        self.structs = {}        # name -> dict(field -> Type)
        self.enums = {}          # name -> list[variant]
        self.sums = {}           # name -> {variant: [(field, Type)]}
        self.variants = {}       # variant name -> (sum name, [(field, Type)])
        self.exports = set()
        self.imported = {}       # alias -> ModSig
        self.selective = {}      # name -> Type (function/const pulled in by name)

    def lookup_value(self, name):
        if name in self.funcs: return self.funcs[name]
        if name in self.consts: return self.consts[name]
        if name in self.selective: return self.selective[name]
        return None


# --------------------------------------------------------------------------- #
# Checker
# --------------------------------------------------------------------------- #

class TypeError_(CardinalError):
    pass


class Checker:
    def __init__(self, search_dirs):
        self.search_dirs = search_dirs
        self.sigs = {}           # module name -> ModSig
        self.errors = []

    # ---- driver ---- #
    def check_main(self, path):
        d = os.path.dirname(os.path.abspath(path))
        if d not in self.search_dirs:
            self.search_dirs.insert(0, d)
        with open(path) as f:
            src = f.read()
        mod = Parser(lex(src)).parse_module()
        self.build_sig(mod)
        # check bodies of every loaded module (main + imports), not just main
        for ms in list(self.sigs.values()):
            if ms.module is not None:
                self.check_module_bodies(ms.module, ms)
        return self.errors

    def _find(self, name):
        for d in self.search_dirs:
            p = os.path.join(d, name + ".cardinal")
            if os.path.exists(p):
                return p
        return None

    def load_sig(self, name):
        if name in self.sigs:
            return self.sigs[name]
        if name == "io":
            sig = ModSig("io")
            anyfn = FuncT((), UNIT)   # placeholder; io calls are checked specially
            sig.funcs["println"] = anyfn
            sig.funcs["print"] = anyfn
            self.sigs[name] = sig
            return sig
        if name == "strings":
            sig = ModSig("strings")
            sig.funcs["chars"] = FuncT((STR,), ArrayT(CHAR))
            sig.funcs["concat"] = FuncT((STR, STR), STR)
            sig.funcs["substr"] = FuncT((STR, U64, U64), STR)
            sig.funcs["from_char"] = FuncT((CHAR,), STR)
            sig.funcs["eq"] = FuncT((STR, STR), BOOL)
            self.sigs[name] = sig
            return sig
        if name == "convert":
            sig = ModSig("convert")
            sig.funcs["ord"] = FuncT((CHAR,), IntT("u32"))
            sig.funcs["chr"] = FuncT((IntT("u32"),), CHAR)
            sig.funcs["int_to_str"] = FuncT((IntT("i64"),), STR)
            sig.funcs["str_to_int"] = FuncT((STR,), IntT("i64"))
            self.sigs[name] = sig
            return sig
        if name == "fs":
            sig = ModSig("fs")
            sig.funcs["read_file"] = FuncT((STR,), STR)
            sig.funcs["write_file"] = FuncT((STR, STR), UNIT)
            sig.funcs["read_file_cb"] = FuncT((STR, FuncT((STR,), UNIT)), UNIT)
            sig.funcs["write_file_cb"] = FuncT((STR, STR, FuncT((BOOL,), UNIT)), UNIT)
            sig.funcs["exists"] = FuncT((STR,), BOOL)
            self.sigs[name] = sig
            return sig
        if name == "sys":
            sig = ModSig("sys")
            sig.funcs["args"] = FuncT((), VecT(STR))
            self.sigs[name] = sig
            return sig
        path = self._find(name)
        if not path:
            raise TypeError_(f"module {name!r} not found")
        with open(path) as f:
            src = f.read()
        mod = Parser(lex(src)).parse_module()
        return self.build_sig(mod)

    # ---- signature building ---- #
    def build_sig(self, mod: Module):
        sig = ModSig(mod.name)
        sig.module = mod
        self.sigs[mod.name] = sig          # register early for recursion
        self._cur = sig
        self._modname = mod.name

        # imports (this recurses into other modules, changing _cur/_modname)
        for imp in mod.imports:
            other = self.load_sig(imp.name)
            if imp.names is None:
                sig.imported[imp.name] = other
            else:
                for nm in imp.names:
                    v = other.lookup_value(nm)
                    if v is None:
                        self.err(f"module {imp.name} has no value {nm!r}")
                    elif other.exports and nm not in other.exports:
                        self.err(f"{nm!r} is not exported from {imp.name}")
                    else:
                        sig.selective[nm] = v if v is not None else UNIT
                # imported struct/enum types also become referrable
                sig.imported[imp.name] = other

        # restore context for *this* module after recursive import builds
        self._cur = sig
        self._modname = mod.name

        # register type names first (so recursive/mutual references resolve)
        for d in mod.decls:
            if isinstance(d, StructDecl):
                sig.structs[d.name] = None     # placeholder
            elif isinstance(d, EnumDecl):
                sig.enums[d.name] = list(d.variants)
                if d.exported: sig.exports.add(d.name)
            elif isinstance(d, SumDecl):
                sig.sums[d.name] = {}          # placeholder
                if d.exported: sig.exports.add(d.name)

        # resolve struct fields
        for d in mod.decls:
            if isinstance(d, StructDecl):
                sig.structs[d.name] = {fn: self.resolve(ft, sig)
                                       for fn, ft in d.fields}
                if d.exported: sig.exports.add(d.name)

        # resolve sum-type variant fields
        for d in mod.decls:
            if isinstance(d, SumDecl):
                table = {}
                for vname, fields in d.variants:
                    rfields = [(fn, self.resolve(ft, sig)) for fn, ft in fields]
                    table[vname] = rfields
                    if vname in sig.variants:
                        self.err(f"duplicate variant name {vname!r}")
                    sig.variants[vname] = (d.name, rfields)
                sig.sums[d.name] = table

        # function signatures and consts
        for d in mod.decls:
            if isinstance(d, FuncDecl):
                params = tuple(self.resolve(pt, sig) for _, pt in d.params)
                ret = self.resolve(d.ret, sig) if d.ret is not None else UNIT
                sig.funcs[d.name] = FuncT(params, ret)
                if d.exported: sig.exports.add(d.name)
            elif isinstance(d, ConstDecl):
                t = self.resolve(d.ty, sig) if d.ty is not None else None
                sig.consts[d.name] = t   # may refine when checking
                if d.exported: sig.exports.add(d.name)
        return sig

    # ---- type resolution ---- #
    def resolve(self, node, sig):
        if node is None:
            return UNIT
        if isinstance(node, TyName):
            n = node.name
            if n in INT_TYPES: return IntT(n)
            if n in FLOAT_TYPES: return FloatT(n)
            if n in ("bool", "char", "str", "unit", "handle"): return PrimT(n)
            if n in sig.structs: return StructT(n)
            if n in sig.enums: return EnumT(n)
            if n in sig.sums: return SumT(n)
            for mod in sig.imported.values():
                if n in mod.structs: return StructT(n)
                if n in mod.enums: return EnumT(n)
                if n in mod.sums: return SumT(n)
            self.err(f"unknown type {n!r}")
            return UNIT
        if isinstance(node, TyArray):
            return ArrayT(self.resolve(node.elem, sig))
        if isinstance(node, TyVec):
            return VecT(self.resolve(node.elem, sig))
        if isinstance(node, TyMap):
            kt = self.resolve(node.key, sig)
            vt = self.resolve(node.val, sig)
            if not is_hashable(kt):
                self.err(f"map key type {tystr(kt)} is not hashable "
                         f"(use str/int/char/bool/enum)")
            return MapT(kt, vt)
        if isinstance(node, TyFunc):
            return FuncT(tuple(self.resolve(p, sig) for p in node.params),
                         self.resolve(node.ret, sig))
        self.err(f"bad type node {node!r}")
        return UNIT

    # ---- module bodies ---- #
    def check_module_bodies(self, mod, sig):
        self._cur = sig
        self._modname = sig.name
        for d in mod.decls:
            if isinstance(d, ConstDecl):
                expected = sig.consts.get(d.name)
                t = self.check_expr(d.expr, {}, sig, expected)
                resolved = self.coerce(t, expected, d.expr) if expected else self.concrete(t, d.expr, d.name)
                sig.consts[d.name] = resolved
            elif isinstance(d, FuncDecl):
                ft = sig.funcs[d.name]
                scope = {}
                for (pname, _), pty in zip(d.params, ft.params):
                    scope[pname] = (pty, True)
                self.check_block(d.body, scope, sig, ft.ret)

    # ---- statements ---- #
    def check_block(self, body, scope, sig, ret):
        inner = dict(scope)
        for s in body:
            self.check_stmt(s, inner, sig, ret)

    def check_stmt(self, s, scope, sig, ret):
        k = type(s)
        if k is Let:
            expected = self.resolve(s.ty, sig) if s.ty is not None else None
            t = self.check_expr(s.expr, scope, sig, expected)
            if expected is not None:
                t = self.coerce(t, expected, s.expr)
            else:
                t = self.concrete(t, s.expr, s.name)
            scope[s.name] = (t, s.mutable)
        elif k is Set:
            self.check_set(s, scope, sig)
        elif k is Do:
            self.check_expr(s.call, scope, sig, None)
        elif k is If:
            for cond, body in s.branches:
                ct = self.check_expr(cond, scope, sig, BOOL)
                self.expect_bool(ct, cond)
                self.check_block(body, scope, sig, ret)
            if s.orelse is not None:
                self.check_block(s.orelse, scope, sig, ret)
        elif k is While:
            self.expect_bool(self.check_expr(s.cond, scope, sig, BOOL), s.cond)
            self.check_block(s.body, scope, sig, ret)
        elif k is ForNum:
            st = self.check_expr(s.start, scope, sig, None)
            et = self.check_expr(s.end, scope, sig, None)
            step = self.check_expr(s.step, scope, sig, None) if s.step else None
            ity = self.unify_num([x for x in (st, et, step) if x is not None], s.start)
            if isinstance(ity, (UntypedIntT,)):
                ity = IntT("i32")   # loop var falls back to a concrete int if unconstrained
            inner = dict(scope); inner[s.var] = (ity, True)
            self.check_block(s.body, inner, sig, ret)
        elif k is ForIn:
            it = self.check_expr(s.iterable, scope, sig, None)
            if not isinstance(it, (ArrayT, VecT)):
                self.err("for-in requires an array or vector", s.iterable)
                elem = UNIT
            else:
                elem = it.elem
            inner = dict(scope); inner[s.var] = (elem, True)
            self.check_block(s.body, inner, sig, ret)
        elif k is Loop:
            self.check_block(s.body, scope, sig, ret)
        elif k in (Break, Continue, Pass):
            pass
        elif k is Return:
            t = self.check_expr(s.expr, scope, sig, ret) if s.expr is not None else UNIT
            self.coerce(t, ret, s)
        elif k is Checked:
            self.check_block(s.body, scope, sig, ret)
        elif k is Match:
            self.check_match(s, scope, sig, ret)
        else:
            self.err(f"unknown statement {k.__name__}")

    def check_set(self, s, scope, sig):
        target = s.target
        tt = self.check_place(target, scope, sig)
        if tt is None:
            return
        ty, mutable = tt
        if not mutable:
            self.err("cannot assign to an immutable binding", target)
        vt = self.check_expr(s.expr, scope, sig, ty)
        self.coerce(vt, ty, s.expr)

    def check_place(self, node, scope, sig):
        """Return (Type, mutable) for an lvalue, or None on error."""
        if isinstance(node, Name):
            if node.ident in scope:
                return scope[node.ident]
            v = sig.lookup_value(node.ident)
            if v is not None:
                return (v, False)
            self.err(f"undefined name {node.ident!r}", node)
            return None
        if isinstance(node, FieldAccess):
            base = self.check_place(node.obj, scope, sig)
            bt = base[0] if base else self.check_expr(node.obj, scope, sig, None)
            mutable = base[1] if base else True
            ft = self.field_type(bt, node.field, node)
            return (ft, mutable)
        if isinstance(node, Index):
            base = self.check_place(node.obj, scope, sig)
            bt = base[0] if base else self.check_expr(node.obj, scope, sig, None)
            mutable = base[1] if base else True
            if isinstance(bt, MapT):
                kt = self.check_expr(node.index, scope, sig, bt.key)
                self.coerce(kt, bt.key, node.index)
                return (bt.val, mutable)
            if not isinstance(bt, (ArrayT, VecT)):
                self.err("indexing a non-array/vector/map", node); return (UNIT, mutable)
            it = self.check_expr(node.index, scope, sig, U64)
            if not isinstance(it, (IntT, UntypedIntT)):
                self.err(f"index must be an integer, got {tystr(it)}", node)
            return (bt.elem, mutable)
        # other expressions are not lvalues
        self.err("invalid assignment target", node)
        return None

    # ---- expressions ---- #
    def check_expr(self, e, scope, sig, expected):
        t = self._check_expr(e, scope, sig, expected)
        e.ctype = t                      # annotate AST for the lowering pass
        return t

    def _check_expr(self, e, scope, sig, expected):
        k = type(e)
        if k is IntLit:
            return IntT(e.ty) if e.ty else UINT_LIT
        if k is FloatLit:
            return FloatT(e.ty) if e.ty else UFLOAT_LIT
        if k is BoolLit:
            return BOOL
        if k is CharLit:
            return CHAR
        if k is StrLit:
            return STR
        if k is NullLit:
            return NULL
        if k is Name:
            return self.check_name(e, scope, sig)
        if k is Path:
            return self.check_path(e, sig)
        if k is FieldAccess:
            bt = self.check_expr(e.obj, scope, sig, None)
            return self.field_type(bt, e.field, e)
        if k is Index:
            bt = self.check_expr(e.obj, scope, sig, None)
            if isinstance(bt, MapT):
                kt = self.check_expr(e.index, scope, sig, bt.key)
                self.coerce(kt, bt.key, e.index)
                return bt.val
            it = self.check_expr(e.index, scope, sig, U64)
            if not isinstance(bt, (ArrayT, VecT)):
                self.err("indexing a non-array/vector/map", e); return UNIT
            if not isinstance(it, (IntT, UntypedIntT)):
                self.err(f"index must be an integer, got {tystr(it)}", e)
            return bt.elem
        if k is OpCall:
            return self.check_op(e, scope, sig)
        if k is Cast:
            return self.check_cast(e, scope, sig)
        if k is Ref:
            self.check_expr(e.place, scope, sig, None)
            return HANDLE
        if k is Call:
            return self.check_call(e, scope, sig)
        if k is StructLit:
            return self.check_struct_lit(e, scope, sig)
        if k is ArrayLit:
            return self.check_array_lit(e, scope, sig, expected)
        if k is ArrayNew:
            self.check_expr(e.count, scope, sig, U64)
            return ArrayT(self.resolve(e.elem, sig))
        if k is VecNew:
            return VecT(self.resolve(e.elem, sig))
        if k is VecLit:
            return self.check_vec_lit(e, scope, sig, expected)
        if k is MapNew:
            return MapT(self.resolve(e.key, sig), self.resolve(e.val, sig))
        if k is FuncLit:
            return self.check_func_lit(e, scope, sig)
        self.err(f"cannot type {k.__name__}", e)
        return UNIT

    def check_name(self, e, scope, sig):
        if e.ident in scope:
            return scope[e.ident][0]
        v = sig.lookup_value(e.ident)
        if v is not None:
            return v
        if e.ident in ("len",):
            return FuncT((), U64)        # checked specially in check_call
        if e.ident == "panic":
            return FuncT((), NEVER)
        vinfo = self.find_variant(e.ident, sig)      # nullary variant used as a value
        if vinfo is not None:
            sumname, vfields = vinfo
            if vfields:
                self.err(f"variant {e.ident} needs fields; use ({e.ident} ...)", e)
            return SumT(sumname)
        self.err(f"undefined name {e.ident!r}", e)
        return UNIT

    def check_path(self, e, sig):
        parts = e.parts
        if len(parts) == 2 and parts[0] in sig.enums:
            if parts[1] not in sig.enums[parts[0]]:
                self.err(f"no variant {parts[1]} in enum {parts[0]}", e)
            return EnumT(parts[0])
        # imported enum
        for mod in sig.imported.values():
            if len(parts) == 2 and parts[0] in mod.enums:
                if parts[1] not in mod.enums[parts[0]]:
                    self.err(f"no variant {parts[1]} in enum {parts[0]}", e)
                return EnumT(parts[0])
        mod = sig.imported.get(parts[0]) or self.sigs.get(parts[0])
        if mod is not None and len(parts) == 2:
            if mod.exports and parts[1] not in mod.exports:
                self.err(f"{parts[1]!r} is not exported from {parts[0]}", e)
            v = mod.lookup_value(parts[1])
            if v is None:
                self.err(f"{parts[0]} has no value {parts[1]!r}", e)
                return UNIT
            return v
        self.err(f"cannot resolve path {'::'.join(parts)}", e)
        return UNIT

    def check_op(self, e, scope, sig):
        op = e.op
        if op in ("and", "or"):
            for a in e.args:
                self.expect_bool(self.check_expr(a, scope, sig, BOOL), a)
            return BOOL
        if op == "not":
            self.expect_bool(self.check_expr(e.args[0], scope, sig, BOOL), e.args[0])
            return BOOL
        argts = [self.check_expr(a, scope, sig, None) for a in e.args]
        if op in ("+", "-", "*", "/", "%"):
            return self.unify_num(argts, e)
        if op in ("<", "<=", ">", ">=", "==", "!="):
            self.unify_comparable(op, argts, e)
            return BOOL
        if op in ("band", "bor", "bxor", "shl", "shr"):
            return self.unify_int_only(argts, e)
        if op == "bnot":
            return self.unify_int_only(argts, e)
        self.err(f"unknown operator {op}", e)
        return UNIT

    def check_cast(self, e, scope, sig):
        src = self.check_expr(e.expr, scope, sig, None)
        ty = self.resolve(e.ty, sig)
        if not isinstance(ty, (IntT, FloatT)):
            self.err("cast target must be a numeric type", e)
        if not isinstance(src, (IntT, FloatT, UntypedIntT, UntypedFloatT)):
            self.err(f"cannot cast {tystr(src)} to {tystr(ty)}", e)
        return ty

    def check_call(self, e, scope, sig):
        # builtins with special signatures
        callee = e.callee
        if isinstance(callee, Name) and callee.ident == "len":
            if len(e.args) != 1:
                self.err("len takes 1 argument", e)
            else:
                at = self.check_expr(e.args[0], scope, sig, None)
                if not (isinstance(at, (ArrayT, VecT, MapT)) or at == STR):
                    self.err(f"len expects array/vector/map/str, got {tystr(at)}", e)
            return U64
        if isinstance(callee, Name) and callee.ident in ("map_has", "map_del", "map_keys"):
            op = callee.ident
            nargs = 1 if op == "map_keys" else 2
            if len(e.args) != nargs:
                self.err(f"{op} takes {nargs} argument(s)", e)
                return BOOL if op == "map_has" else UNIT
            mt = self.check_expr(e.args[0], scope, sig, None)
            if not isinstance(mt, MapT):
                self.err(f"{op} expects a map, got {tystr(mt)}", e)
                return BOOL if op == "map_has" else UNIT
            if nargs == 2:
                self.coerce(self.check_expr(e.args[1], scope, sig, mt.key), mt.key, e.args[1])
            if op == "map_has":
                return BOOL
            if op == "map_keys":
                return VecT(mt.key)
            return UNIT
        if isinstance(callee, Name) and callee.ident == "push":
            if len(e.args) != 2:
                self.err("push takes (vector, value)", e)
                return UNIT
            vt = self.check_expr(e.args[0], scope, sig, None)
            if not isinstance(vt, VecT):
                self.err(f"push expects a vector, got {tystr(vt)}", e)
            else:
                at = self.check_expr(e.args[1], scope, sig, vt.elem)
                self.coerce(at, vt.elem, e.args[1])
            return UNIT
        if isinstance(callee, Name) and callee.ident == "pop":
            if len(e.args) != 1:
                self.err("pop takes (vector)", e); return UNIT
            vt = self.check_expr(e.args[0], scope, sig, None)
            if not isinstance(vt, VecT):
                self.err(f"pop expects a vector, got {tystr(vt)}", e); return UNIT
            return vt.elem
        if isinstance(callee, Name) and callee.ident == "panic":
            for a in e.args:
                self.check_expr(a, scope, sig, None)
            return NEVER
        if isinstance(callee, Path) and callee.parts[0] in ("io",) \
                and (sig.imported.get("io") or self.sigs.get("io")):
            for a in e.args:
                self.check_expr(a, scope, sig, None)   # io accepts anything
            return UNIT

        ft = self.check_expr(callee, scope, sig, None)
        if not isinstance(ft, FuncT):
            self.err(f"calling a non-function value of type {tystr(ft)}", e)
            for a in e.args:
                self.check_expr(a, scope, sig, None)
            return UNIT
        if len(e.args) != len(ft.params):
            self.err(f"expected {len(ft.params)} arguments, got {len(e.args)}", e)
        for a, pt in zip(e.args, ft.params):
            at = self.check_expr(a, scope, sig, pt)
            self.coerce(at, pt, a)
        return ft.ret

    def check_struct_lit(self, e, scope, sig):
        tn = e.typename
        name = tn.ident if isinstance(tn, Name) else tn.parts[-1]
        vinfo = self.find_variant(name, sig)
        if vinfo is not None:
            return self.check_variant_lit(e, scope, sig, name, vinfo)
        fields = self.struct_fields(name, sig)
        if fields is None:
            self.err(f"unknown struct or variant {name!r}", e)
            return UNIT
        given = set()
        for fname, fexpr in e.fields:
            if fname not in fields:
                self.err(f"struct {name} has no field {fname!r}", e)
                self.check_expr(fexpr, scope, sig, None)
                continue
            ft = self.check_expr(fexpr, scope, sig, fields[fname])
            self.coerce(ft, fields[fname], fexpr)
            given.add(fname)
        for fname in fields:
            if fname not in given:
                self.err(f"missing field {fname!r} in {name}", e)
        return StructT(name)

    def check_array_lit(self, e, scope, sig, expected):
        elem = expected.elem if isinstance(expected, ArrayT) else None
        elemts = []
        for el in e.elements:
            t = self.check_expr(el, scope, sig, elem)
            if elem is not None:
                self.coerce(t, elem, el)
            elemts.append((t, el))
        if elem is None:
            concrete = {t for t, _ in elemts if not isinstance(t, (UntypedIntT, UntypedFloatT))}
            if not elemts or len(concrete) != 1:
                self.err("cannot infer array element type — annotate, "
                         "e.g. let xs [i32] = [...]", e)
                return ArrayT(UNIT)
            elem = concrete.pop()
        return ArrayT(elem)

    def check_variant_lit(self, e, scope, sig, vname, vinfo):
        sumname, vfields = vinfo
        ftypes = dict(vfields)
        given = set()
        for fname, fexpr in e.fields:
            if fname not in ftypes:
                self.err(f"variant {vname} has no field {fname!r}", e)
                self.check_expr(fexpr, scope, sig, None)
                continue
            t = self.check_expr(fexpr, scope, sig, ftypes[fname])
            self.coerce(t, ftypes[fname], fexpr)
            given.add(fname)
        for fname in ftypes:
            if fname not in given:
                self.err(f"missing field {fname!r} in {vname}", e)
        return SumT(sumname)

    def check_match(self, s, scope, sig, ret):
        st = self.check_expr(s.scrutinee, scope, sig, None)
        if not isinstance(st, SumT):
            self.err(f"match on a non-sum value of type {tystr(st)}", s)
            return
        table = self.sum_table(st.name, sig)
        covered = set()
        for vname, bindings, body in s.arms:
            inner = dict(scope)
            if table is None or vname not in table:
                self.err(f"{vname!r} is not a variant of {st.name}", s)
            else:
                vfields = table[vname]
                if len(bindings) != len(vfields):
                    self.err(f"variant {vname} has {len(vfields)} field(s), "
                             f"pattern binds {len(bindings)}", s)
                for b, (fn, ft) in zip(bindings, vfields):
                    inner[b] = (ft, False)
                covered.add(vname)
            self.check_block(body, inner, sig, ret)
        if s.orelse is not None:
            self.check_block(s.orelse, scope, sig, ret)
        elif table is not None and covered != set(table.keys()):
            missing = ", ".join(sorted(set(table.keys()) - covered))
            self.err(f"non-exhaustive match on {st.name}; missing: {missing}", s)

    def find_variant(self, name, sig):
        if name in sig.variants:
            return sig.variants[name]
        for mod in sig.imported.values():
            if name in mod.variants:
                return mod.variants[name]
        return None

    def sum_table(self, name, sig):
        if name in sig.sums and sig.sums[name]:
            return sig.sums[name]
        for mod in sig.imported.values():
            if name in mod.sums and mod.sums[name]:
                return mod.sums[name]
        return None

    def check_vec_lit(self, e, scope, sig, expected):
        elem = expected.elem if isinstance(expected, VecT) else None
        elemts = []
        for el in e.elements:
            t = self.check_expr(el, scope, sig, elem)
            if elem is not None:
                self.coerce(t, elem, el)
            elemts.append(t)
        if elem is None:
            concrete = {t for t in elemts if not isinstance(t, (UntypedIntT, UntypedFloatT))}
            if not elemts or len(concrete) != 1:
                self.err("cannot infer vector element type — annotate, "
                         "e.g. let xs {i32} = {...}", e)
                return VecT(UNIT)
            elem = concrete.pop()
        return VecT(elem)

    def check_func_lit(self, e, scope, sig):
        params = tuple(self.resolve(pt, sig) for _, pt in e.params)
        ret = self.resolve(e.ret, sig) if e.ret is not None else UNIT
        inner = dict(scope)
        for (pname, _), pt in zip(e.params, params):
            inner[pname] = (pt, True)
        self.check_block(e.body, inner, sig, ret)
        return FuncT(params, ret)

    # ---- helpers ---- #
    def field_type(self, bt, field, node):
        if not isinstance(bt, StructT):
            self.err(f"field access .{field} on non-struct {tystr(bt)}", node)
            return UNIT
        fields = self.struct_fields(bt.name, self._cur)
        if fields is None or field not in fields:
            self.err(f"no field {field!r} on {bt.name}", node)
            return UNIT
        return fields[field]

    def struct_fields(self, name, sig):
        if name in sig.structs and sig.structs[name] is not None:
            return sig.structs[name]
        for mod in sig.imported.values():
            if name in mod.structs and mod.structs[name] is not None:
                return mod.structs[name]
        return None

    def unify_num(self, types, node):
        ints = all(isinstance(t, (IntT, UntypedIntT)) for t in types)
        floats = any(isinstance(t, (FloatT, UntypedFloatT)) for t in types)
        if floats:
            for t in types:
                if isinstance(t, IntT):
                    self.err("cannot mix int and float without a cast", node)
                    return UNIT
            concrete = {t for t in types if isinstance(t, FloatT)}
            if len(concrete) > 1:
                self.err(f"mismatched float types {[tystr(t) for t in concrete]}", node)
            return concrete.pop() if concrete else UFLOAT_LIT
        if ints:
            concrete = {t for t in types if isinstance(t, IntT)}
            if len(concrete) > 1:
                self.err(f"mismatched integer types {sorted(t.name for t in concrete)} "
                         f"(no implicit promotion)", node)
            return concrete.pop() if concrete else UINT_LIT
        self.err(f"arithmetic on non-numeric operands "
                 f"({', '.join(tystr(t) for t in types)})", node)
        return UNIT

    def unify_int_only(self, types, node):
        for t in types:
            if not isinstance(t, (IntT, UntypedIntT)):
                self.err(f"bitwise op needs integers, got {tystr(t)}", node)
                return UNIT
        concrete = {t for t in types if isinstance(t, IntT)}
        if len(concrete) > 1:
            self.err(f"mismatched integer types {sorted(t.name for t in concrete)}", node)
        return concrete.pop() if concrete else UINT_LIT

    def unify_comparable(self, op, types, node):
        a, b = types[0], types[1]
        numa = isinstance(a, (IntT, FloatT, UntypedIntT, UntypedFloatT))
        numb = isinstance(b, (IntT, FloatT, UntypedIntT, UntypedFloatT))
        if numa and numb:
            self.unify_num([a, b], node)
            return
        if a == CHAR and b == CHAR:        # chars compare by codepoint
            return
        if op in ("==", "!="):
            if a == b or isinstance(a, NullT) or isinstance(b, NullT):
                return
            if isinstance(a, (StructT, EnumT)) and a == b:
                return
            self.err(f"cannot compare {tystr(a)} and {tystr(b)}", node)
        else:
            self.err(f"ordering comparison needs numbers, got "
                     f"{tystr(a)} and {tystr(b)}", node)

    def coerce(self, got, expected, node):
        r = self._coerce(got, expected, node)
        # stamp the concrete target type so literals/args carry it for lowering
        if node is not None and expected is not None and r is not None:
            node.ctype = r
        return r

    def _coerce(self, got, expected, node):
        """Check `got` is assignable to declared `expected`; return expected."""
        if expected is None:
            return got
        if isinstance(got, NeverT):
            return expected
        if isinstance(expected, IntT):
            if isinstance(got, UntypedIntT):
                return expected
            if isinstance(got, IntT) and got.name == expected.name:
                return expected
            self.err(f"expected {expected.name}, got {tystr(got)}"
                     + ("" if not isinstance(got, IntT) else " (use a cast)"), node)
            return expected
        if isinstance(expected, FloatT):
            if isinstance(got, UntypedFloatT):
                return expected
            if isinstance(got, FloatT) and got.name == expected.name:
                return expected
            self.err(f"expected {expected.name}, got {tystr(got)}", node)
            return expected
        if isinstance(expected, (StructT, EnumT, SumT, ArrayT, VecT, MapT, FuncT)) or \
           (isinstance(expected, PrimT) and expected.name in ("str", "handle")):
            if isinstance(got, NullT):
                return expected
        if expected == got:
            return expected
        # untyped int into nothing-numeric, etc.
        if isinstance(expected, PrimT) and expected.name == "unit":
            return expected
        if isinstance(expected, PrimT) and expected.name == "handle":
            return expected     # handle accepts anything heap-ish in the bootstrap
        self.err(f"expected {tystr(expected)}, got {tystr(got)}", node)
        return expected

    def concrete(self, t, node, name):
        """For `let x = e` with no annotation: t must not stay untyped."""
        if isinstance(t, (UntypedIntT, UntypedFloatT)):
            self.err(f"cannot infer type of {name!r} "
                     f"(untyped numeric literal needs context)", node)
            return IntT("i32")
        if isinstance(t, (NullT, NeverT)):
            self.err(f"cannot infer type of {name!r} from {tystr(t)}", node)
            return UNIT
        return t

    def expect_bool(self, t, node):
        if t != BOOL:
            self.err(f"condition must be bool, got {tystr(t)}", node)

    def err(self, msg, node=None):
        line = getattr(node, "line", None)
        parts = []
        if getattr(self, "_modname", None):
            parts.append(self._modname)
        if line is not None:
            parts.append(f"line {line}")
        prefix = (": ".join(parts) + ": ") if parts else ""
        self.errors.append(prefix + msg)


# --------------------------------------------------------------------------- #
def check_file(path, search_dirs):
    return Checker(list(search_dirs)).check_main(path)
