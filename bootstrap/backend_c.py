#!/usr/bin/env python3
"""
Cardinal — C backend.

Emits portable C from the IR and leans on the host C compiler for optimization
(compile the output with -O2 -fwrapv). It is a stepping stone: the planned
x86_64 backend will consume the same IR and emit relocatable objects directly.

Mapping highlights:
  * sized ints -> <stdint.h> types; signed wrapping relies on -fwrapv, `checked`
    arithmetic uses __builtin_*_overflow;
  * value structs -> C structs (native value semantics);
  * arrays -> cl_array runtime handle (reference semantics, bounds-checked);
  * basic blocks -> labels + goto (only referenced labels are emitted).
"""

from __future__ import annotations
import os
import subprocess

from backend import Backend
from interpreter import INT_TYPES
import typecheck as tc
import ir

RT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime")


def cint(name):
    bits, signed = INT_TYPES[name]
    return f"{'int' if signed else 'uint'}{bits}_t"


def ctype(t):
    if isinstance(t, tc.IntT):    return cint(t.name)
    if isinstance(t, tc.FloatT):  return "float" if t.name == "f32" else "double"
    if isinstance(t, tc.PrimT):
        return {"bool": "bool", "char": "int32_t", "str": "cl_str",
                "unit": "void", "handle": "void*"}[t.name]
    if isinstance(t, tc.StructT): return f"cl_struct_{t.name}"
    if isinstance(t, tc.EnumT):   return "int32_t"
    if isinstance(t, tc.ArrayT):  return "cl_array"
    if isinstance(t, tc.FuncT):   return "cl_closure"
    if isinstance(t, ir.PtrT):    return "cl_handle"   # boxes/envs are GC handles
    raise NotImplementedError(f"C type for {t!r}")


def _is_unit(t):
    return isinstance(t, tc.PrimT) and t.name == "unit"


def _managed(t):
    """Types that may hold a GC handle and so must be shadow-stack roots."""
    return isinstance(t, (ir.PtrT, tc.FuncT, tc.ArrayT, tc.StructT))


def fnptr_cast(ret, ptys):
    """C function-pointer type taking (cl_handle env, ptys...) -> ret."""
    params = ", ".join(["cl_handle"] + [ctype(p) for p in ptys])
    return f"{ctype(ret)}(*)({params})"


BINOPS = {"+": "+", "-": "-", "*": "*", "/": "/", "%": "%",
          "band": "&", "bor": "|", "bxor": "^", "shl": "<<", "shr": ">>",
          "<": "<", "<=": "<=", ">": ">", ">=": ">=", "==": "==", "!=": "!="}
OVERFLOW_BUILTIN = {"+": "__builtin_add_overflow",
                    "-": "__builtin_sub_overflow",
                    "*": "__builtin_mul_overflow"}


class CBackend(Backend):
    name = "c"
    output_suffix = ".c"

    # ---- emit ---- #
    def emit(self, m: ir.IRModule) -> str:
        out = []
        out.append('#include "cardinal_rt.h"')
        out.append("")
        # struct typedefs in dependency order
        for s in self._order_structs(m.structs):
            out.append(f"typedef struct {{")
            for fn, ft in s.fields:
                out.append(f"    {ctype(ft)} {fn};")
            out.append(f"}} cl_struct_{s.name};")
            out.append("")
        # prototypes
        for f in m.funcs:
            out.append(self._proto(f) + ";")
        for name, ft in m.thunks.items():
            out.append(self._thunk_proto(name, ft) + ";")
        out.append("")
        # function-as-value thunks
        for name, ft in m.thunks.items():
            out.extend(self._thunk(name, ft))
            out.append("")
        # definitions
        for f in m.funcs:
            out.extend(self._func(f))
            out.append("")
        # C entry point
        out.append(self._main_wrapper(m))
        return "\n".join(out) + "\n"

    def _main_wrapper(self, m):
        target = None
        for f in m.funcs:
            if f.name.endswith("__main"):
                target = f
                break
        if target is None:
            return "int main(void) { int b; cl_gc_init(&b); return 0; }"
        if isinstance(target.ret, tc.IntT):
            return (f"int main(void) {{ int b; cl_gc_init(&b); "
                    f"return (int){target.name}(); }}")
        return f"int main(void) {{ int b; cl_gc_init(&b); {target.name}(); return 0; }}"

    def _proto(self, f):
        ps = ", ".join(f"{ctype(t)} v_{n}" for n, t in f.params) or "void"
        ret = ctype(f.ret)
        return f"{ret} {f.name}({ps})"

    def _thunk_proto(self, name, ft):
        ps = ", ".join(["cl_handle env"] + [f"{ctype(p)} p{i}" for i, p in enumerate(ft.params)])
        return f"{ctype(ft.ret)} {name}__thunk({ps})"

    def _thunk(self, name, ft):
        call = f"{name}(" + ", ".join(f"p{i}" for i in range(len(ft.params))) + ")"
        body = "(void)env; " + (f"return {call};" if not _is_unit(ft.ret)
                                else f"{call}; return;")
        return [self._thunk_proto(name, ft) + " {", f"    {body}", "}"]

    def _func(self, f):
        lines = [self._proto(f) + " {"]
        roots = []                       # C names of managed slots to root
        # declare temps and locals up front (managed ones zero-initialized)
        for t in f.temps:
            if _is_unit(t.ty):
                continue
            init = " = {0}" if _managed(t.ty) else ""
            lines.append(f"    {ctype(t.ty)} t{t.id}{init};")
            if _managed(t.ty):
                roots.append(f"t{t.id}")
        for n, t in f.locals:
            init = " = {0}" if _managed(t) else ""
            lines.append(f"    {ctype(t)} v_{n}{init};")
            if _managed(t):
                roots.append(f"v_{n}")
        for n, t in f.params:
            if _managed(t):
                roots.append(f"v_{n}")
        # precise rooting: register every managed slot on the shadow stack
        for r in roots:
            lines.append(f"    cl_gc_push_root(&{r}, sizeof({r}));")
        self._nroots = len(roots)
        referenced = self._referenced_labels(f)
        for blk in f.blocks:
            if blk.label in referenced:
                lines.append(f"  {blk.label}:;")
            for ins in blk.instrs:
                lines.extend("    " + s for s in self._instr(ins))
        lines.append("}")
        return lines

    def _referenced_labels(self, f):
        refs = set()
        for blk in f.blocks:
            for ins in blk.instrs:
                if isinstance(ins, ir.Br):
                    refs.add(ins.target)
                elif isinstance(ins, ir.CondBr):
                    refs.add(ins.then); refs.add(ins.els)
        return refs

    # ---- operands ---- #
    def val(self, v):
        if isinstance(v, ir.Temp):
            return f"t{v.id}"
        if isinstance(v, ir.Local):
            return f"v_{v.name}"
        if isinstance(v, ir.Imm):
            return self._imm(v)
        raise NotImplementedError(f"operand {v!r}")

    def _imm(self, v):
        t = v.ty
        if isinstance(t, tc.IntT):
            suf = "" if INT_TYPES[t.name][1] else "u"
            if INT_TYPES[t.name][0] == 64:
                suf += "ll"
            return f"(({cint(t.name)}){v.value}{suf})"
        if isinstance(t, tc.FloatT):
            return f"({repr(float(v.value))}{'f' if t.name == 'f32' else ''})"
        if t == tc.BOOL:
            return "true" if v.value else "false"
        if t == tc.CHAR:
            return f"((int32_t){v.value})"
        if t == tc.STR:
            data, n = self._cstr(v.value)
            return f"((cl_str){{{data}, {n}}})"
        if isinstance(t, tc.PrimT) and t.name == "unit":
            return "0"
        if isinstance(t, ir.PtrT) or v.value is None:
            return "0"
        raise NotImplementedError(f"immediate of type {t!r}")

    def _cstr(self, s):
        raw = s.encode("utf-8")
        out = []
        for b in raw:
            c = chr(b)
            if c == "\\": out.append("\\\\")
            elif c == '"': out.append('\\"')
            elif c == "\n": out.append("\\n")
            elif c == "\t": out.append("\\t")
            elif c == "\r": out.append("\\r")
            elif 32 <= b < 127: out.append(c)
            else: out.append(f"\\x{b:02x}")
        return '"' + "".join(out) + '"', len(raw)

    def place(self, p):
        if isinstance(p, ir.PLocal):
            return f"v_{p.name}"
        if isinstance(p, ir.PField):
            return f"{self.place(p.base)}.{p.field}"
        if isinstance(p, ir.PDeref):
            return f"(*({ctype(p.ty)}*)cl_gc_deref({self.val(p.ptr)}))"
        raise NotImplementedError(f"place {p!r}")

    # ---- instructions ---- #
    def _instr(self, ins):
        k = type(ins)
        if k is ir.Bin:    return self._bin(ins)
        if k is ir.Un:     return self._un(ins)
        if k is ir.Cast:   return [f"t{ins.dst.id} = ({ctype(ins.toty)})({self.val(ins.val)});"]
        if k is ir.Assign: return [f"{self.place(ins.place)} = {self.val(ins.src)};"]
        if k is ir.LoadField:
            return [f"t{ins.dst.id} = ({self.val(ins.base)}).{ins.field};"]
        if k is ir.Call:
            args = ", ".join(self.val(a) for a in ins.args)
            if ins.dst is None:
                return [f"{ins.callee}({args});"]
            return [f"t{ins.dst.id} = {ins.callee}({args});"]
        if k is ir.StructNew:
            inits = ", ".join(f".{fn}={self.val(v)}" for fn, v in ins.fields)
            return [f"t{ins.dst.id} = (cl_struct_{ins.struct}){{{inits}}};"]
        if k is ir.ArrNew:
            return [f"t{ins.dst.id} = cl_array_new(sizeof({ctype(ins.elem)}), "
                    f"{self.val(ins.count)});"]
        if k is ir.ArrLit:
            ec = ctype(ins.elem)
            lines = [f"t{ins.dst.id} = cl_array_new(sizeof({ec}), {len(ins.elems)});"]
            for i, v in enumerate(ins.elems):
                lines.append(f"*({ec}*)cl_array_at(t{ins.dst.id}, {i}) = {self.val(v)};")
            return lines
        if k is ir.ArrGet:
            ec = ctype(ins.ty)
            return [f"t{ins.dst.id} = *({ec}*)cl_array_at({self.val(ins.arr)}, "
                    f"{self.val(ins.idx)});"]
        if k is ir.ArrSet:
            ec = ctype(ins.elem)
            return [f"*({ec}*)cl_array_at({self.val(ins.arr)}, {self.val(ins.idx)}) = "
                    f"{self.val(ins.val)};"]
        if k is ir.ArrLen:
            return [f"t{ins.dst.id} = ({self.val(ins.arr)}).len;"]
        if k is ir.EnumConst:
            return [f"t{ins.dst.id} = {ins.intval}; /* {ins.enum}::{ins.variant} */"]
        if k is ir.Alloc:
            return [f"t{ins.dst.id} = cl_gc_alloc(sizeof({ctype(ins.ty)}));"]
        if k is ir.Load:
            return [f"t{ins.dst.id} = *({ctype(ins.ty)}*)cl_gc_deref({self.val(ins.ptr)});"]
        if k is ir.EnvNew:
            return [f"t{ins.dst.id} = cl_gc_alloc({ins.n} * sizeof(cl_handle));"]
        if k is ir.EnvStore:
            return [f"((cl_handle*)cl_gc_deref({self.val(ins.env)}))[{ins.idx}] = "
                    f"(cl_handle)({self.val(ins.ptr)});"]
        if k is ir.EnvLoad:
            return [f"t{ins.dst.id} = ((cl_handle*)cl_gc_deref({self.val(ins.env)}))[{ins.idx}];"]
        if k is ir.MakeClosure:
            return [f"t{ins.dst.id} = (cl_closure){{(void*)&{ins.fn}, "
                    f"(cl_handle)({self.val(ins.env)})}};"]
        if k is ir.CallClosure:
            cast = fnptr_cast(ins.ret, ins.ptys)
            c = self.val(ins.clos)
            args = ", ".join([f"({c}).env"] + [self.val(a) for a in ins.args])
            call = f"(({cast})({c}).fn)({args})"
            if ins.dst is None:
                return [f"{call};"]
            return [f"t{ins.dst.id} = {call};"]
        if k is ir.Br:      return [f"goto {ins.target};"]
        if k is ir.CondBr:
            return [f"if ({self.val(ins.cond)}) goto {ins.then}; else goto {ins.els};"]
        if k is ir.Ret:
            pop = [f"cl_gc_pop_roots({self._nroots});"] if self._nroots else []
            if ins.val is None:
                return pop + ["return;"]
            return pop + [f"return {self.val(ins.val)};"]
        if k is ir.Panic:
            return [f"cl_panic({self.val(ins.msg)});"]
        raise NotImplementedError(f"instruction {k.__name__}")

    def _bin(self, ins):
        d, op = f"t{ins.dst.id}", ins.op
        a, b = self.val(ins.lhs), self.val(ins.rhs)
        cty = ctype(ins.ty)
        if op in ("<", "<=", ">", ">=", "==", "!="):
            return [f"{d} = ({a} {BINOPS[op]} {b});"]
        if op in ("/", "%"):
            return [f'if (({b}) == 0) cl_panic_cstr("integer division by zero");',
                    f"{d} = ({cty})(({a}) {BINOPS[op]} ({b}));"]
        if ins.checked and op in OVERFLOW_BUILTIN:
            return [f'if ({OVERFLOW_BUILTIN[op]}({a}, {b}, &{d})) '
                    f'cl_panic_cstr("integer overflow ({op})");']
        return [f"{d} = ({cty})(({a}) {BINOPS[op]} ({b}));"]

    def _un(self, ins):
        d = f"t{ins.dst.id}"
        v = self.val(ins.val)
        cty = ctype(ins.ty)
        if ins.op == "not":
            return [f"{d} = !({v});"]
        if ins.op == "bnot":
            return [f"{d} = ({cty})(~({v}));"]
        if ins.op == "-":
            if ins.checked:
                return [f'if (__builtin_sub_overflow(({cty})0, {v}, &{d})) '
                        f'cl_panic_cstr("integer overflow (-)");']
            return [f"{d} = ({cty})(-({v}));"]
        raise NotImplementedError(ins.op)

    # ---- struct ordering ---- #
    def _order_structs(self, structs):
        by_name = {s.name: s for s in structs}
        ordered, seen = [], set()
        def visit(s):
            if s.name in seen:
                return
            seen.add(s.name)
            for _, ft in s.fields:
                if isinstance(ft, tc.StructT) and ft.name in by_name:
                    visit(by_name[ft.name])
            ordered.append(s)
        for s in structs:
            visit(s)
        return ordered

    # ---- build ---- #
    def build(self, c_path, exe_path, cc=None):
        cc = cc or os.environ.get("CC", "cc")
        cmd = [cc, "-O2", "-fwrapv", f"-I{RT_DIR}", c_path,
               os.path.join(RT_DIR, "cardinal_rt.c"),
               os.path.join(RT_DIR, "cardinal_gc.c"), "-o", exe_path]
        subprocess.run(cmd, check=True)
