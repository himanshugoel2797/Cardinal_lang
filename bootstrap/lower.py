#!/usr/bin/env python3
"""
Cardinal — lowering pass: typed AST -> target-independent IR (ir.py).

Runs after type-checking (which annotates each expression node with `.ctype`).
Lowers every loaded module's functions into a single `IRModule` with mangled
names. Control flow becomes basic blocks + branches; Cardinal variables become
named IR locals; intermediate results become temporaries.

Supported in this first cut: sized ints/floats/bool/unit, value structs, arrays,
enums, functions/recursion, cross-module calls, all control flow, casts, the
`io`/`len`/`panic` builtins. Deferred (raise a clean error): closures /
function-valued expressions, `(ref …)`, `null`/handles.
"""

from __future__ import annotations

from interpreter import (
    CardinalError, FuncDecl, StructDecl, EnumDecl,
    Let, Set, Do, If, While, ForNum, ForIn, Loop, Break, Continue, Return, Checked,
    IntLit, FloatLit, BoolLit, CharLit, StrLit, NullLit, Name, Path,
    FieldAccess, Index, Call, OpCall, StructLit, ArrayLit, ArrayNew, Cast, Ref, FuncLit,
)
import typecheck as tc
import ir


UNIT = tc.UNIT; BOOL = tc.BOOL; CHAR = tc.CHAR; STR = tc.STR; U64 = tc.U64


def mangle(mod, name):
    return f"cl_{mod}__{name}"


class Box:
    """A captured (by-reference) variable: lives in a heap cell. `ptr` is an IR
    value of pointer type; reads are *ptr, writes are *ptr = v."""
    __slots__ = ("ptr", "ty")
    def __init__(self, ptr, ty):
        self.ptr = ptr
        self.ty = ty


# --------------------------------------------------------------------------- #
# Free-variable analysis (for closure conversion)
# --------------------------------------------------------------------------- #

def lambda_free(funclit):
    """Names referenced in a FuncLit but bound outside it (its captures)."""
    out = set()
    bound = {p[0] for p in funclit.params}
    _fv_stmts(funclit.body, bound, out)
    return out


def direct_funclits(stmts):
    """FuncLits lexically inside these statements but not nested in another
    FuncLit (the direct children of the current function)."""
    out = []
    for s in stmts:
        _collect_funclits_stmt(s, out)
    return out


def _fv_stmts(stmts, bound, out):
    bound = set(bound)
    for s in stmts:
        _fv_stmt(s, bound, out)


def _fv_stmt(s, bound, out):
    k = type(s)
    if k is Let:
        _fv_expr(s.expr, bound, out); bound.add(s.name)
    elif k is Set:
        _fv_expr(s.target, bound, out); _fv_expr(s.expr, bound, out)
    elif k is Do:
        _fv_expr(s.call, bound, out)
    elif k is If:
        for cond, body in s.branches:
            _fv_expr(cond, bound, out); _fv_stmts(body, bound, out)
        if s.orelse is not None:
            _fv_stmts(s.orelse, bound, out)
    elif k is While:
        _fv_expr(s.cond, bound, out); _fv_stmts(s.body, bound, out)
    elif k is ForNum:
        _fv_expr(s.start, bound, out); _fv_expr(s.end, bound, out)
        if s.step: _fv_expr(s.step, bound, out)
        _fv_stmts(s.body, bound | {s.var}, out)
    elif k is ForIn:
        _fv_expr(s.iterable, bound, out)
        _fv_stmts(s.body, bound | {s.var}, out)
    elif k is Loop:
        _fv_stmts(s.body, bound, out)
    elif k is Return:
        if s.expr is not None: _fv_expr(s.expr, bound, out)
    elif k is Checked:
        _fv_stmts(s.body, bound, out)


def _fv_expr(e, bound, out):
    k = type(e)
    if k is Name:
        if e.ident not in bound:
            out.add(e.ident)
    elif k is OpCall:
        for a in e.args: _fv_expr(a, bound, out)
    elif k is Call:
        _fv_expr(e.callee, bound, out)
        for a in e.args: _fv_expr(a, bound, out)
    elif k is FieldAccess:
        _fv_expr(e.obj, bound, out)
    elif k is Index:
        _fv_expr(e.obj, bound, out); _fv_expr(e.index, bound, out)
    elif k is StructLit:
        for _, fe in e.fields: _fv_expr(fe, bound, out)
    elif k is ArrayLit:
        for el in e.elements: _fv_expr(el, bound, out)
    elif k is ArrayNew:
        _fv_expr(e.count, bound, out)
    elif k is Cast:
        _fv_expr(e.expr, bound, out)
    elif k is Ref:
        _fv_expr(e.place, bound, out)
    elif k is FuncLit:
        for nm in lambda_free(e):
            if nm not in bound:
                out.add(nm)
    # Path / literals: no local-variable references


def _collect_funclits_stmt(s, out):
    k = type(s)
    if k is Let: _collect_funclits_expr(s.expr, out)
    elif k is Set:
        _collect_funclits_expr(s.target, out); _collect_funclits_expr(s.expr, out)
    elif k is Do: _collect_funclits_expr(s.call, out)
    elif k is If:
        for cond, body in s.branches:
            _collect_funclits_expr(cond, out)
            for st in body: _collect_funclits_stmt(st, out)
        if s.orelse:
            for st in s.orelse: _collect_funclits_stmt(st, out)
    elif k is While:
        _collect_funclits_expr(s.cond, out)
        for st in s.body: _collect_funclits_stmt(st, out)
    elif k in (ForNum, ForIn):
        if k is ForNum:
            _collect_funclits_expr(s.start, out); _collect_funclits_expr(s.end, out)
            if s.step: _collect_funclits_expr(s.step, out)
        else:
            _collect_funclits_expr(s.iterable, out)
        for st in s.body: _collect_funclits_stmt(st, out)
    elif k is Loop:
        for st in s.body: _collect_funclits_stmt(st, out)
    elif k is Return:
        if s.expr is not None: _collect_funclits_expr(s.expr, out)
    elif k is Checked:
        for st in s.body: _collect_funclits_stmt(st, out)


def _collect_funclits_expr(e, out):
    k = type(e)
    if k is FuncLit:
        out.append(e)                      # do NOT descend into the lambda
    elif k is OpCall:
        for a in e.args: _collect_funclits_expr(a, out)
    elif k is Call:
        _collect_funclits_expr(e.callee, out)
        for a in e.args: _collect_funclits_expr(a, out)
    elif k is FieldAccess:
        _collect_funclits_expr(e.obj, out)
    elif k is Index:
        _collect_funclits_expr(e.obj, out); _collect_funclits_expr(e.index, out)
    elif k is StructLit:
        for _, fe in e.fields: _collect_funclits_expr(fe, out)
    elif k is ArrayLit:
        for el in e.elements: _collect_funclits_expr(el, out)
    elif k is ArrayNew:
        _collect_funclits_expr(e.count, out)
    elif k is Cast:
        _collect_funclits_expr(e.expr, out)
    elif k is Ref:
        _collect_funclits_expr(e.place, out)


class Lowerer:
    def __init__(self, checker):
        self.checker = checker
        self.sigs = checker.sigs
        self.module = ir.IRModule("cardinal")
        self._lambda_n = 0
        self.captured = set()

    # ---- driver ---- #
    def lower_all(self):
        seen_struct = set()
        for sig in self.sigs.values():
            if sig.module is None:
                continue
            for d in sig.module.decls:
                if isinstance(d, StructDecl):
                    if d.name in seen_struct:
                        raise CardinalError(f"C backend needs globally-unique "
                                            f"struct names; {d.name!r} duplicated")
                    seen_struct.add(d.name)
                    self.module.structs.append(ir.IRStruct(
                        d.name, [(fn, self.checker.resolve(ft, sig))
                                 for fn, ft in d.fields]))
        for sig in self.sigs.values():
            if sig.module is None:
                continue
            self.cur = sig
            self._build_callmap(sig)
            for d in sig.module.decls:
                if isinstance(d, FuncDecl):
                    self.lower_func(d, sig)
        return self.module

    def _build_callmap(self, sig):
        m = {}
        for d in sig.module.decls:
            if isinstance(d, FuncDecl):
                m[d.name] = mangle(sig.name, d.name)
        for imp in sig.module.imports:
            if imp.names:
                for nm in imp.names:
                    m[nm] = mangle(imp.name, nm)
        self.callmap = m

    # ---- functions ---- #
    def lower_func(self, d, sig):
        params = [(pn, self.checker.resolve(pt, sig)) for pn, pt in d.params]
        ret = self.checker.resolve(d.ret, sig) if d.ret is not None else UNIT
        self.b = ir.FuncBuilder(mangle(sig.name, d.name), params, ret)
        self.ret_ty = ret
        self._uniq = 0
        self.captured = set()
        for fl in direct_funclits(d.body):
            self.captured |= lambda_free(fl)
        scope = {}
        for pn, pty in params:
            ploc = ir.Local(pn, pty)
            if pn in self.captured:
                scope[pn] = Box(self.make_box(pty, ploc), pty)
            else:
                scope[pn] = ploc
        self.lower_block(d.body, scope)
        if not self.b.terminated():
            self.b.emit(ir.Ret(None))
        self.module.funcs.append(self.b.func)

    def make_box(self, ty, init=None):
        ptr = self.b.temp(ir.PtrT(ty))
        self.b.emit(ir.Alloc(ptr, ty))
        if init is not None:
            self.b.emit(ir.Assign(ir.PDeref(ptr, ty), init))
        return ptr

    # ---- closures ---- #
    def func_value(self, mname, ft):
        """A top-level/imported function used as a value -> closure with thunk."""
        self.module.thunks[mname] = ft
        dst = self.b.temp(ft)
        self.b.emit(ir.MakeClosure(dst, mname + "__thunk", ir.Imm(None, ir.PtrT(None))))
        return dst

    def lower_funclit(self, e, scope):
        caps = [nm for nm in sorted(lambda_free(e)) if nm in scope]
        for nm in caps:
            if not isinstance(scope[nm], Box):
                raise CardinalError(f"C backend: cannot capture {nm!r} "
                                    f"(capturing loop variables is unsupported)")
        cap_types = [scope[nm].ty for nm in caps]
        if caps:
            env = self.b.temp(ir.PtrT(None))
            self.b.emit(ir.EnvNew(env, len(caps)))
            for i, nm in enumerate(caps):
                self.b.emit(ir.EnvStore(env, i, scope[nm].ptr))
        else:
            env = ir.Imm(None, ir.PtrT(None))
        ptys = [self.checker.resolve(pt, self.cur) for _, pt in e.params]
        ret = self.checker.resolve(e.ret, self.cur) if e.ret is not None else UNIT
        ft = tc.FuncT(tuple(ptys), ret)
        self._lambda_n += 1
        lname = f"cl_{self.cur.name}__lambda{self._lambda_n}"
        dst = self.b.temp(ft)
        self.b.emit(ir.MakeClosure(dst, lname, env))
        self.lower_lambda_body(lname, e, caps, cap_types, ptys, ret)
        return dst

    def lower_lambda_body(self, lname, e, caps, cap_types, ptys, ret):
        saved = (self.b, self.ret_ty, self.captured, self._uniq)
        irparams = [("__env", ir.PtrT(None))] + \
                   [(pn, pt) for (pn, _), pt in zip(e.params, ptys)]
        self.b = ir.FuncBuilder(lname, irparams, ret)
        self.ret_ty = ret
        self._uniq = 0
        self.captured = set()
        for fl in direct_funclits(e.body):
            self.captured |= lambda_free(fl)
        scope = {}
        envloc = ir.Local("__env", ir.PtrT(None))
        for i, nm in enumerate(caps):
            ty = cap_types[i]
            ptr = self.b.temp(ir.PtrT(ty))
            self.b.emit(ir.EnvLoad(ptr, envloc, i, ty))
            scope[nm] = Box(ptr, ty)
        for (pn, _), pty in zip(e.params, ptys):
            ploc = ir.Local(pn, pty)
            if pn in self.captured:
                scope[pn] = Box(self.make_box(pty, ploc), pty)
            else:
                scope[pn] = ploc
        self.lower_block(e.body, scope)
        if not self.b.terminated():
            self.b.emit(ir.Ret(None))
        self.module.funcs.append(self.b.func)
        self.b, self.ret_ty, self.captured, self._uniq = saved

    def uniq(self, base):
        self._uniq += 1
        return f"{base}_{self._uniq}"

    # ---- statements ---- #
    def lower_block(self, stmts, scope):
        inner = dict(scope)
        for s in stmts:
            self.lower_stmt(s, inner)

    def lower_stmt(self, s, scope):
        k = type(s)
        if k is Let:
            vty = (self.checker.resolve(s.ty, self.cur) if s.ty is not None
                   else self.concrete_ty(s.expr))
            val = self.lower_expr(s.expr, scope, vty)
            if s.name in self.captured:
                scope[s.name] = Box(self.make_box(vty, val), vty)
            else:
                loc = self.b.local(self.uniq(s.name), vty)
                self.b.emit(ir.Assign(ir.PLocal(loc.name, vty), val))
                scope[s.name] = loc
        elif k is Set:
            self.lower_set(s, scope)
        elif k is Do:
            self.lower_expr(s.call, scope, None)
        elif k is If:
            self.lower_if(s, scope)
        elif k is While:
            self.lower_while(s, scope)
        elif k is Loop:
            self.lower_loop(s, scope)
        elif k is ForNum:
            self.lower_fornum(s, scope)
        elif k is ForIn:
            self.lower_forin(s, scope)
        elif k is Break:
            self.b.emit(ir.Br(self._brk[-1]))
        elif k is Continue:
            self.b.emit(ir.Br(self._cont[-1]))
        elif k is Return:
            if s.expr is None:
                self.b.emit(ir.Ret(None))
            else:
                v = self.lower_expr(s.expr, scope, self.ret_ty)
                self.b.emit(ir.Ret(v))
        elif k is Checked:
            saved = getattr(self, "_checked", False)
            self._checked = True
            self.lower_block(s.body, scope)
            self._checked = saved
        else:
            raise CardinalError(f"C backend: cannot lower {k.__name__}")

    def lower_if(self, s, scope):
        end = self.b.label("if_end")
        for cond, body in s.branches:
            cv = self.lower_expr(cond, scope, BOOL)
            then = self.b.label("then")
            els = self.b.label("else")
            self.b.emit(ir.CondBr(cv, then, els))
            self.b.new_block(then)
            self.lower_block(body, scope)
            if not self.b.terminated():
                self.b.emit(ir.Br(end))
            self.b.new_block(els)
        if s.orelse is not None:
            self.lower_block(s.orelse, scope)
        if not self.b.terminated():
            self.b.emit(ir.Br(end))
        self.b.new_block(end)

    def lower_while(self, s, scope):
        head = self.b.label("while_head")
        body = self.b.label("while_body")
        end = self.b.label("while_end")
        self.b.emit(ir.Br(head)); self.b.new_block(head)
        cv = self.lower_expr(s.cond, scope, BOOL)
        self.b.emit(ir.CondBr(cv, body, end))
        self.b.new_block(body)
        self._brk = getattr(self, "_brk", []) + [end]
        self._cont = getattr(self, "_cont", []) + [head]
        self.lower_block(s.body, scope)
        self._brk.pop(); self._cont.pop()
        if not self.b.terminated():
            self.b.emit(ir.Br(head))
        self.b.new_block(end)

    def lower_loop(self, s, scope):
        head = self.b.label("loop_head")
        end = self.b.label("loop_end")
        self.b.emit(ir.Br(head)); self.b.new_block(head)
        self._brk = getattr(self, "_brk", []) + [end]
        self._cont = getattr(self, "_cont", []) + [head]
        self.lower_block(s.body, scope)
        self._brk.pop(); self._cont.pop()
        if not self.b.terminated():
            self.b.emit(ir.Br(head))
        self.b.new_block(end)

    def lower_fornum(self, s, scope):
        if s.var in self.captured:
            raise CardinalError("C backend: capturing a for-loop variable is unsupported")
        ity = self.concrete_ty(s.start)
        if isinstance(ity, tc.UntypedIntT):
            ity = tc.IntT("i32")
        i = self.b.local(self.uniq(s.var), ity)
        start = self.lower_expr(s.start, scope, ity)
        end = self.lower_expr(s.end, scope, ity)
        step = self.lower_expr(s.step, scope, ity) if s.step else ir.Imm(1, ity)
        self.b.emit(ir.Assign(ir.PLocal(i.name, ity), start))
        head = self.b.label("for_head"); body = self.b.label("for_body")
        post = self.b.label("for_post"); fin = self.b.label("for_end")
        self.b.emit(ir.Br(head)); self.b.new_block(head)
        cond = self.b.temp(BOOL)
        self.b.emit(ir.Bin(cond, "<", i, end, ity))
        self.b.emit(ir.CondBr(cond, body, fin))
        self.b.new_block(body)
        inner = dict(scope); inner[s.var] = i
        self._brk = getattr(self, "_brk", []) + [fin]
        self._cont = getattr(self, "_cont", []) + [post]
        self.lower_block(s.body, inner)
        self._brk.pop(); self._cont.pop()
        if not self.b.terminated():
            self.b.emit(ir.Br(post))
        self.b.new_block(post)
        nxt = self.b.temp(ity)
        self.b.emit(ir.Bin(nxt, "+", i, step, ity))
        self.b.emit(ir.Assign(ir.PLocal(i.name, ity), nxt))
        self.b.emit(ir.Br(head))
        self.b.new_block(fin)

    def lower_forin(self, s, scope):
        if s.var in self.captured:
            raise CardinalError("C backend: capturing a for-loop variable is unsupported")
        arr = self.lower_expr(s.iterable, scope, None)
        elem = arr.ty.elem
        idx = self.b.local(self.uniq("i"), U64)
        n = self.b.temp(U64)
        self.b.emit(ir.Assign(ir.PLocal(idx.name, U64), ir.Imm(0, U64)))
        self.b.emit(ir.ArrLen(n, arr))
        head = self.b.label("fi_head"); body = self.b.label("fi_body")
        post = self.b.label("fi_post"); fin = self.b.label("fi_end")
        self.b.emit(ir.Br(head)); self.b.new_block(head)
        cond = self.b.temp(BOOL)
        self.b.emit(ir.Bin(cond, "<", idx, n, U64))
        self.b.emit(ir.CondBr(cond, body, fin))
        self.b.new_block(body)
        elemv = self.b.temp(elem)
        self.b.emit(ir.ArrGet(elemv, arr, idx, elem))
        var = self.b.local(self.uniq(s.var), elem)
        self.b.emit(ir.Assign(ir.PLocal(var.name, elem), elemv))
        inner = dict(scope); inner[s.var] = var
        self._brk = getattr(self, "_brk", []) + [fin]
        self._cont = getattr(self, "_cont", []) + [post]
        self.lower_block(s.body, inner)
        self._brk.pop(); self._cont.pop()
        if not self.b.terminated():
            self.b.emit(ir.Br(post))
        self.b.new_block(post)
        nxt = self.b.temp(U64)
        self.b.emit(ir.Bin(nxt, "+", idx, ir.Imm(1, U64), U64))
        self.b.emit(ir.Assign(ir.PLocal(idx.name, U64), nxt))
        self.b.emit(ir.Br(head))
        self.b.new_block(fin)

    def lower_set(self, s, scope):
        t = s.target
        if isinstance(t, Index):
            arr = self.lower_expr(t.obj, scope, None)
            idx = self.lower_expr(t.index, scope, U64)
            elem = arr.ty.elem
            val = self.lower_expr(s.expr, scope, elem)
            self.b.emit(ir.ArrSet(arr, idx, val, elem))
        else:
            place, pty = self.lower_place(t, scope)
            val = self.lower_expr(s.expr, scope, pty)
            self.b.emit(ir.Assign(place, val))

    def lower_place(self, node, scope):
        if isinstance(node, Name):
            v = scope[node.ident]
            if isinstance(v, Box):
                return ir.PDeref(v.ptr, v.ty), v.ty
            return ir.PLocal(v.name, v.ty), v.ty
        if isinstance(node, FieldAccess):
            base, bty = self.lower_place(node.obj, scope)
            fty = self.struct_field_ty(bty, node.field)
            return ir.PField(base, node.field, fty), fty
        raise CardinalError("C backend: unsupported assignment target")

    # ---- expressions ---- #
    def lower_expr(self, e, scope, expected):
        k = type(e)
        if k is IntLit:
            return ir.Imm(e.value, self.int_ty(e, expected))
        if k is FloatLit:
            ty = tc.FloatT(e.ty) if e.ty else (expected if isinstance(expected, tc.FloatT)
                                               else tc.FloatT("f64"))
            return ir.Imm(e.value, ty)
        if k is BoolLit:
            return ir.Imm(e.value, BOOL)
        if k is CharLit:
            return ir.Imm(e.cp, CHAR)
        if k is StrLit:
            return ir.Imm(e.value, STR)
        if k is NullLit:
            raise CardinalError("C backend: null/handles not supported yet")
        if k is Name:
            if e.ident in scope:
                v = scope[e.ident]
                if isinstance(v, Box):
                    dst = self.b.temp(v.ty)
                    self.b.emit(ir.Load(dst, v.ptr, v.ty))
                    return dst
                return v
            if e.ident in self.callmap:           # top-level function used as a value
                ft = self.cur.lookup_value(e.ident)
                return self.func_value(self.callmap[e.ident], ft)
            raise CardinalError(f"C backend: cannot use {e.ident!r} as a value")
        if k is Path:
            return self.lower_path(e)
        if k is OpCall:
            return self.lower_op(e, scope)
        if k is FieldAccess:
            base = self.lower_expr(e.obj, scope, None)
            fty = self.struct_field_ty(base.ty, e.field)
            dst = self.b.temp(fty)
            self.b.emit(ir.LoadField(dst, base, e.field, fty))
            return dst
        if k is Index:
            arr = self.lower_expr(e.obj, scope, None)
            idx = self.lower_expr(e.index, scope, U64)
            elem = arr.ty.elem
            dst = self.b.temp(elem)
            self.b.emit(ir.ArrGet(dst, arr, idx, elem))
            return dst
        if k is Call:
            return self.lower_call(e, scope)
        if k is StructLit:
            return self.lower_struct_lit(e, scope)
        if k is ArrayLit:
            return self.lower_array_lit(e, scope, expected)
        if k is ArrayNew:
            cnt = self.lower_expr(e.count, scope, U64)
            elem = self.checker.resolve(e.elem, self.cur)
            dst = self.b.temp(tc.ArrayT(elem))
            self.b.emit(ir.ArrNew(dst, elem, cnt))
            return dst
        if k is Cast:
            inner = self.lower_expr(e.expr, scope, None)
            toty = self.checker.resolve(e.ty, self.cur)
            dst = self.b.temp(toty)
            self.b.emit(ir.Cast(dst, inner, toty))
            return dst
        if k is FuncLit:
            return self.lower_funclit(e, scope)
        if k is Ref:
            raise CardinalError("C backend: (ref ...) not supported yet")
        raise CardinalError(f"C backend: cannot lower expr {k.__name__}")

    def lower_path(self, e):
        parts = e.parts
        # enum variant
        enums = self.cur.enums
        if len(parts) == 2 and parts[0] in enums:
            idx = enums[parts[0]].index(parts[1])
            dst = self.b.temp(tc.EnumT(parts[0]))
            self.b.emit(ir.EnumConst(dst, parts[0], parts[1], idx))
            return dst
        for mod in self.cur.imported.values():
            if len(parts) == 2 and parts[0] in mod.enums:
                idx = mod.enums[parts[0]].index(parts[1])
                dst = self.b.temp(tc.EnumT(parts[0]))
                self.b.emit(ir.EnumConst(dst, parts[0], parts[1], idx))
                return dst
        # a module function used as a value -> closure with thunk
        mod = self.cur.imported.get(parts[0]) or self.sigs.get(parts[0])
        if mod is not None and len(parts) == 2:
            v = mod.lookup_value(parts[1])
            if isinstance(v, tc.FuncT):
                return self.func_value(mangle(parts[0], parts[1]), v)
        raise CardinalError(f"C backend: cannot lower path {'::'.join(parts)}")

    def lower_op(self, e, scope):
        op = e.op
        if op == "and":
            return self.lower_andor(e.args, scope, short_true=False)
        if op == "or":
            return self.lower_andor(e.args, scope, short_true=True)
        if op == "not":
            v = self.lower_expr(e.args[0], scope, BOOL)
            dst = self.b.temp(BOOL)
            self.b.emit(ir.Un(dst, "not", v, BOOL))
            return dst
        rty = e.ctype
        checked = getattr(self, "_checked", False)
        if op in ("<", "<=", ">", ">=", "==", "!="):
            opty = self.operand_ty(e.args)
            a = self.lower_expr(e.args[0], scope, opty)
            b = self.lower_expr(e.args[1], scope, opty)
            dst = self.b.temp(BOOL)
            self.b.emit(ir.Bin(dst, op, a, b, opty))
            return dst
        if op == "bnot":
            v = self.lower_expr(e.args[0], scope, rty)
            dst = self.b.temp(rty)
            self.b.emit(ir.Un(dst, "bnot", v, rty, checked))
            return dst
        if op == "-" and len(e.args) == 1:
            v = self.lower_expr(e.args[0], scope, rty)
            dst = self.b.temp(rty)
            self.b.emit(ir.Un(dst, "-", v, rty, checked))
            return dst
        # n-ary arithmetic / bitwise: fold left
        vals = [self.lower_expr(a, scope, rty) for a in e.args]
        acc = vals[0]
        for v in vals[1:]:
            dst = self.b.temp(rty)
            self.b.emit(ir.Bin(dst, op, acc, v, rty, checked))
            acc = dst
        return acc

    def lower_andor(self, args, scope, short_true):
        res = self.b.local(self.uniq("sc"), BOOL)
        end = self.b.label("sc_end")
        for a in args:
            av = self.lower_expr(a, scope, BOOL)
            self.b.emit(ir.Assign(ir.PLocal(res.name, BOOL), av))
            nxt = self.b.label("sc_nx")
            if short_true:                       # or: true short-circuits
                self.b.emit(ir.CondBr(av, end, nxt))
            else:                                # and: false short-circuits
                self.b.emit(ir.CondBr(av, nxt, end))
            self.b.new_block(nxt)
        self.b.emit(ir.Br(end))
        self.b.new_block(end)
        return res

    def lower_call(self, e, scope):
        callee = e.callee
        # builtins
        if isinstance(callee, Name) and callee.ident == "len":
            arg = self.lower_expr(e.args[0], scope, None)
            dst = self.b.temp(U64)
            if isinstance(arg.ty, tc.ArrayT):
                self.b.emit(ir.ArrLen(dst, arg))
            else:                                # str
                self.module.externs.add("cl_str_len")
                self.b.emit(ir.Call(dst, "cl_str_len", [arg], U64))
            return dst
        if isinstance(callee, Name) and callee.ident == "panic":
            msg = self.lower_expr(e.args[0], scope, STR)
            self.module.externs.add("cl_panic")
            self.b.emit(ir.Panic(msg))
            return ir.Imm(None, UNIT)
        if isinstance(callee, Path) and callee.parts[0] == "io":
            return self.lower_io(callee.parts[1], e.args, scope)

        # direct call to a named top-level function (not shadowed by a local)
        direct = ((isinstance(callee, Name) and callee.ident not in scope
                   and callee.ident in self.callmap)
                  or (isinstance(callee, Path) and self.is_func_path(callee)))
        if direct:
            mname = self.resolve_callee(callee)
            ft = self.callee_sig(callee)
            args = [self.lower_expr(a, scope, pt) for a, pt in zip(e.args, ft.params)]
            if isinstance(ft.ret, tc.PrimT) and ft.ret.name == "unit":
                self.b.emit(ir.Call(None, mname, args, UNIT))
                return ir.Imm(None, UNIT)
            dst = self.b.temp(ft.ret)
            self.b.emit(ir.Call(dst, mname, args, ft.ret))
            return dst

        # indirect call: the callee evaluates to a closure value
        clos = self.lower_expr(callee, scope, None)
        ft = getattr(callee, "ctype", None) or clos.ty
        args = [self.lower_expr(a, scope, pt) for a, pt in zip(e.args, ft.params)]
        if isinstance(ft.ret, tc.PrimT) and ft.ret.name == "unit":
            self.b.emit(ir.CallClosure(None, clos, args, ft.ret, list(ft.params)))
            return ir.Imm(None, UNIT)
        dst = self.b.temp(ft.ret)
        self.b.emit(ir.CallClosure(dst, clos, args, ft.ret, list(ft.params)))
        return dst

    def is_func_path(self, callee):
        mod = self.cur.imported.get(callee.parts[0]) or self.sigs.get(callee.parts[0])
        if mod is not None and len(callee.parts) == 2:
            return isinstance(mod.lookup_value(callee.parts[1]), tc.FuncT)
        return False

    def lower_io(self, fn, args, scope):
        for a in args:
            v = self.lower_expr(a, scope, None)
            sym = self.print_sym(v.ty)
            self.module.externs.add(sym)
            self.b.emit(ir.Call(None, sym, [v], UNIT))
        if fn == "println":
            self.module.externs.add("cl_print_nl")
            self.b.emit(ir.Call(None, "cl_print_nl", [], UNIT))
        return ir.Imm(None, UNIT)

    def print_sym(self, ty):
        if isinstance(ty, tc.IntT):
            return "cl_print_i64" if ty.name[0] == "i" else "cl_print_u64"
        if isinstance(ty, tc.FloatT):
            return "cl_print_f64"
        if ty == BOOL:
            return "cl_print_bool"
        if ty == STR:
            return "cl_print_str"
        raise CardinalError(f"C backend: cannot print value of type {tc.tystr(ty)}")

    def lower_struct_lit(self, e, scope):
        name = e.typename.ident if isinstance(e.typename, Name) else e.typename.parts[-1]
        fields = self.struct_fields(name)
        order = [fn for fn, _ in fields]
        given = {}
        for fn, fexpr in e.fields:
            fty = dict(fields)[fn]
            given[fn] = self.lower_expr(fexpr, scope, fty)
        dst = self.b.temp(tc.StructT(name))
        self.b.emit(ir.StructNew(dst, name, [(fn, given[fn]) for fn in order]))
        return dst

    def lower_array_lit(self, e, scope, expected):
        elem = expected.elem if isinstance(expected, tc.ArrayT) else \
            (e.ctype.elem if isinstance(e.ctype, tc.ArrayT) else None)
        if elem is None:
            raise CardinalError("C backend: cannot infer array element type")
        vals = [self.lower_expr(el, scope, elem) for el in e.elements]
        dst = self.b.temp(tc.ArrayT(elem))
        self.b.emit(ir.ArrLit(dst, elem, vals))
        return dst

    # ---- type / name helpers ---- #
    def int_ty(self, lit, expected):
        if lit.ty:
            return tc.IntT(lit.ty)
        if isinstance(expected, tc.IntT):
            return expected
        if isinstance(lit.ctype, tc.IntT):
            return lit.ctype
        raise CardinalError("C backend: uninferable integer literal")

    def operand_ty(self, args):
        for a in args:
            t = getattr(a, "ctype", None)
            if isinstance(t, (tc.IntT, tc.FloatT, tc.PrimT, tc.EnumT, tc.StructT)):
                return t
        return getattr(args[0], "ctype", None)

    def concrete_ty(self, e):
        t = getattr(e, "ctype", None)
        return t

    def resolve_callee(self, callee):
        if isinstance(callee, Name):
            if callee.ident in self.callmap:
                return self.callmap[callee.ident]
            raise CardinalError(f"C backend: unknown function {callee.ident!r}")
        if isinstance(callee, Path):
            return mangle(callee.parts[0], callee.parts[1])
        raise CardinalError("C backend: unsupported call target")

    def callee_sig(self, callee):
        if isinstance(callee, Name):
            v = self.cur.lookup_value(callee.ident)
            if isinstance(v, tc.FuncT):
                return v
        if isinstance(callee, Path):
            mod = self.cur.imported.get(callee.parts[0]) or self.sigs.get(callee.parts[0])
            if mod:
                v = mod.lookup_value(callee.parts[1])
                if isinstance(v, tc.FuncT):
                    return v
        raise CardinalError(f"C backend: cannot resolve call signature")

    def struct_fields(self, name):
        if name in self.cur.structs and self.cur.structs[name] is not None:
            return list(self.cur.structs[name].items())
        for mod in self.cur.imported.values():
            if name in mod.structs and mod.structs[name] is not None:
                return list(mod.structs[name].items())
        raise CardinalError(f"C backend: unknown struct {name!r}")

    def struct_field_ty(self, sty, field):
        for fn, ft in self.struct_fields(sty.name):
            if fn == field:
                return ft
        raise CardinalError(f"C backend: no field {field!r} on {sty.name}")


def node_in(scope, name):
    return name in scope
