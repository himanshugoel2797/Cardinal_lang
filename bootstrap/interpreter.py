#!/usr/bin/env python3
"""
Cardinal — bootstrap tree-walking interpreter.

This is the *throwaway* tier-1 implementation described in DESIGN.md §12: a
correctness-first interpreter in a high-level host language whose only job is to
run Cardinal source (and eventually host the self-hosted compiler). Its runtime
is a disposable stand-in — we lean on Python's own objects/GC rather than
implementing the canonical handle-table + mark-and-sweep collector. That real
runtime is written in Cardinal later.

Implemented: modules + imports, exported/private decls, structs (value
semantics), arrays (reference, bounds-checked), enums, functions, closures
(capture by reference), let/const/set, if/elsif/else, while, numeric + foreach
for, loop/break/continue, return, do, checked blocks (lexical overflow trap),
sized-int wrapping arithmetic, context-typed numeric literals, comparison /
logical (short-circuit) / bitwise ops, casts, null + panic, the `io` module.

Usage:  python3 interpreter.py path/to/program.cardinal
"""

from __future__ import annotations
import sys
import os
from dataclasses import dataclass, field
from typing import Optional


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #

class CardinalError(Exception):
    """Compile-time error (lexer/parser/type)."""
    def __init__(self, msg, line=None):
        self.line = line
        super().__init__(f"line {line}: {msg}" if line else msg)


class Panic(Exception):
    """Runtime panic — the unrecoverable error mechanism (DESIGN.md §11)."""
    def __init__(self, msg):
        self.msg = msg
        super().__init__(msg)


# --------------------------------------------------------------------------- #
# Lexer
# --------------------------------------------------------------------------- #

KEYWORDS = {
    "module", "import", "export", "func", "struct", "packed", "enum", "end",
    "type", "match", "case",
    "let", "const", "set", "do", "if", "elsif", "else", "while", "for",
    "to", "step", "in", "loop", "break", "continue", "return", "checked", "pass",
    "and", "or", "not", "as", "true", "false", "null",
    "band", "bor", "bxor", "bnot", "shl", "shr",
}
TYPE_NAMES = {
    "i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64",
    "f32", "f64", "bool", "char", "str", "handle", "unit",
}

# multi-char punctuation, longest first
PUNCT = ["::", "->", "<=", ">=", "==", "!=",
         "(", ")", "[", "]", "{", "}", ":", ".", "=", "+", "-", "*", "/", "%", "<", ">"]

INT_TYPES = {
    "i8": (8, True), "i16": (16, True), "i32": (32, True), "i64": (64, True),
    "u8": (8, False), "u16": (16, False), "u32": (32, False), "u64": (64, False),
}
FLOAT_TYPES = {"f32", "f64"}


@dataclass
class Tok:
    kind: str      # 'int','float','char','str','ident','kw','type','punct','newline','eof'
    value: object
    line: int


def lex(src: str) -> list[Tok]:
    toks: list[Tok] = []
    i, n = 0, len(src)
    line = 1
    depth = 0  # () / [] nesting; newlines inside are continuations

    def err(m):
        raise CardinalError(m, line)

    while i < n:
        c = src[i]

        # whitespace (not newline)
        if c in " \t\r":
            i += 1
            continue

        # comment to end of line
        if c == "#":
            while i < n and src[i] != "\n":
                i += 1
            continue

        # newline -> statement terminator (suppressed inside brackets)
        if c == "\n":
            if depth == 0:
                if toks and toks[-1].kind != "newline":
                    toks.append(Tok("newline", "\n", line))
            line += 1
            i += 1
            continue

        # string literal
        if c == '"':
            i += 1
            buf = []
            while i < n and src[i] != '"':
                ch = src[i]
                if ch == "\\":
                    i += 1
                    if i >= n:
                        err("unterminated string escape")
                    buf.append(_escape(src[i], err))
                    i += 1
                elif ch == "\n":
                    err("newline in string literal")
                else:
                    buf.append(ch)
                    i += 1
            if i >= n:
                err("unterminated string literal")
            i += 1  # closing quote
            toks.append(Tok("str", "".join(buf), line))
            continue

        # char literal
        if c == "'":
            i += 1
            if i >= n:
                err("unterminated char literal")
            if src[i] == "\\":
                i += 1
                cp = ord(_escape(src[i], err))
                i += 1
            else:
                cp = ord(src[i])
                i += 1
            if i >= n or src[i] != "'":
                err("unterminated char literal")
            i += 1
            toks.append(Tok("char", cp, line))
            continue

        # number
        if c.isdigit():
            j, tok = _lex_number(src, i, line, err)
            toks.append(tok)
            i = j
            continue

        # identifier / keyword / type name
        if c.isalpha() or c == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            word = src[i:j]
            i = j
            if word in KEYWORDS:
                toks.append(Tok("kw", word, line))
            elif word in TYPE_NAMES:
                toks.append(Tok("type", word, line))
            else:
                toks.append(Tok("ident", word, line))
            continue

        # punctuation
        for p in PUNCT:
            if src.startswith(p, i):
                if p in ("(", "[", "{"):
                    depth += 1
                elif p in (")", "]", "}"):
                    depth = max(0, depth - 1)
                toks.append(Tok("punct", p, line))
                i += len(p)
                break
        else:
            err(f"unexpected character {c!r}")

    if toks and toks[-1].kind != "newline":
        toks.append(Tok("newline", "\n", line))
    toks.append(Tok("eof", None, line))
    return toks


def _escape(ch, err):
    return {"n": "\n", "t": "\t", "r": "\r", "\\": "\\",
            "'": "'", '"': '"', "0": "\0"}.get(ch) or err(f"bad escape \\{ch}")


def _lex_number(src, i, line, err):
    n = len(src)
    start = i
    base = 10
    if src.startswith(("0x", "0X"), i):
        base = 16; i += 2
    elif src.startswith(("0b", "0B"), i):
        base = 2; i += 2
    elif src.startswith(("0o", "0O"), i):
        base = 8; i += 2

    digits = []
    is_float = False
    while i < n:
        ch = src[i]
        if ch == "_":
            i += 1
            continue
        if base == 16 and ch in "0123456789abcdefABCDEF":
            digits.append(ch); i += 1
        elif base != 16 and ch.isdigit():
            digits.append(ch); i += 1
        elif base == 10 and ch == "." and i + 1 < n and src[i + 1].isdigit():
            is_float = True; digits.append("."); i += 1
        elif base == 10 and ch in "eE" and not is_float and False:
            pass
        else:
            break
    # exponent for floats (decimal only)
    if base == 10 and i < n and src[i] in "eE":
        is_float = True
        digits.append("e"); i += 1
        if i < n and src[i] in "+-":
            digits.append(src[i]); i += 1
        while i < n and (src[i].isdigit() or src[i] == "_"):
            if src[i] != "_":
                digits.append(src[i])
            i += 1

    # optional type suffix
    suffix = None
    if i < n and (src[i].isalpha()):
        j = i
        while j < n and (src[j].isalnum()):
            j += 1
        suffix = src[i:j]
        i = j
        if suffix not in INT_TYPES and suffix not in FLOAT_TYPES:
            err(f"bad numeric suffix {suffix!r}")

    text = "".join(digits)
    if is_float or (suffix in FLOAT_TYPES):
        if suffix in INT_TYPES:
            err("float literal with integer suffix")
        return i, Tok("float", (float(text), suffix), line)
    value = int(text, base)
    return i, Tok("int", (value, suffix), line)


# --------------------------------------------------------------------------- #
# AST
# --------------------------------------------------------------------------- #

@dataclass
class Module:
    name: str
    imports: list
    decls: list

@dataclass
class Import:
    name: str
    names: Optional[list]   # selective import, or None for whole module

@dataclass
class FuncDecl:
    name: str
    params: list            # list[(name, Type)]
    ret: object             # Type or None (=> unit)
    body: list
    exported: bool = False

@dataclass
class StructDecl:
    name: str
    fields: list            # list[(name, Type)]
    packed: bool = False
    exported: bool = False

@dataclass
class EnumDecl:
    name: str
    variants: list          # list[str]
    exported: bool = False

@dataclass
class SumDecl:
    name: str
    variants: list          # list[(vname, [(fname, Type)])]
    exported: bool = False

@dataclass
class ConstDecl:
    name: str
    ty: object
    expr: object
    exported: bool = False

# statements
@dataclass
class Let:        name: str; ty: object; expr: object; mutable: bool
@dataclass
class Set:        target: object; expr: object
@dataclass
class Do:         call: object
@dataclass
class If:         branches: list; orelse: Optional[list]   # branches: [(cond, body)]
@dataclass
class While:      cond: object; body: list
@dataclass
class ForNum:     var: str; start: object; end: object; step: object; body: list
@dataclass
class ForIn:      var: str; iterable: object; body: list
@dataclass
class Loop:       body: list
@dataclass
class Break:      pass
@dataclass
class Continue:   pass
@dataclass
class Pass:       pass
@dataclass
class Return:     expr: object
@dataclass
class Checked:    body: list
@dataclass
class Match:      scrutinee: object; arms: list; orelse: object   # arms: [(vname, [binding], body)]

# expressions
@dataclass
class IntLit:     value: int; ty: Optional[str]
@dataclass
class FloatLit:   value: float; ty: Optional[str]
@dataclass
class BoolLit:    value: bool
@dataclass
class CharLit:    cp: int
@dataclass
class StrLit:     value: str
@dataclass
class NullLit:    pass
@dataclass
class Name:       ident: str
@dataclass
class Path:       parts: list
@dataclass
class FieldAccess: obj: object; field: str
@dataclass
class Index:      obj: object; index: object
@dataclass
class Call:       callee: object; args: list
@dataclass
class OpCall:     op: str; args: list
@dataclass
class StructLit:  typename: object; fields: list   # list[(name, expr)]
@dataclass
class ArrayLit:   elements: list
@dataclass
class ArrayNew:   elem: object; count: object    # (array T n) zero-initialized
@dataclass
class VecLit:     elements: list                 # {e1 e2 ...} growable vector literal
@dataclass
class VecNew:     elem: object                   # (vec T) empty vector
@dataclass
class MapNew:     key: object; val: object        # (map K V) empty map
@dataclass
class Cast:       expr: object; ty: object
@dataclass
class Ref:        place: object
@dataclass
class FuncLit:    params: list; ret: object; body: list

# types
@dataclass
class TyName:     name: str
@dataclass
class TyArray:    elem: object
@dataclass
class TyVec:      elem: object
@dataclass
class TyMap:      key: object; val: object
@dataclass
class TyFunc:     params: list; ret: object


# --------------------------------------------------------------------------- #
# Parser  (hand-written recursive descent)
# --------------------------------------------------------------------------- #

OPERATOR_SYMS = {"+", "-", "*", "/", "%", "<", "<=", ">", ">=", "==", "!="}
WORD_OPS = {"and", "or", "not", "band", "bor", "bxor", "bnot", "shl", "shr"}


class Parser:
    def __init__(self, toks):
        self.toks = toks
        self.pos = 0

    def peek(self, k=0):
        return self.toks[self.pos + k]

    def next(self):
        t = self.toks[self.pos]
        self.pos += 1
        return t

    def at(self, kind, value=None):
        t = self.peek()
        return t.kind == kind and (value is None or t.value == value)

    def eat(self, kind, value=None):
        t = self.peek()
        if t.kind != kind or (value is not None and t.value != value):
            want = value if value is not None else kind
            raise CardinalError(f"expected {want!r}, got {t.value!r}", t.line)
        return self.next()

    def skip_newlines(self):
        while self.at("newline"):
            self.next()

    def eat_terminator(self):
        if self.at("eof"):
            return
        self.eat("newline")
        self.skip_newlines()

    # -- program ----------------------------------------------------------- #
    def parse_module(self):
        self.skip_newlines()
        self.eat("kw", "module")
        name = self.eat("ident").value
        self.eat_terminator()
        imports, decls = [], []
        while not self.at("eof"):
            self.skip_newlines()
            if self.at("eof"):
                break
            if self.at("kw", "import"):
                imports.append(self.parse_import())
            else:
                decls.append(self.parse_toplevel())
        return Module(name, imports, decls)

    def parse_import(self):
        self.eat("kw", "import")
        name = self.eat("ident").value
        names = None
        if self.at("punct", "("):
            self.next()
            names = []
            while not self.at("punct", ")"):
                names.append(self.eat("ident").value)
            self.eat("punct", ")")
        self.eat_terminator()
        return Import(name, names)

    def parse_toplevel(self):
        exported = False
        if self.at("kw", "export"):
            self.next()
            exported = True
        t = self.peek()
        if t.kind == "kw" and t.value == "func":
            d = self.parse_func_decl()
        elif t.kind == "kw" and t.value in ("struct", "packed"):
            d = self.parse_struct_decl()
        elif t.kind == "kw" and t.value == "enum":
            d = self.parse_enum_decl()
        elif t.kind == "kw" and t.value == "type":
            d = self.parse_sum_decl()
        elif t.kind == "kw" and t.value == "const":
            d = self.parse_const_decl()
        else:
            raise CardinalError(f"unexpected top-level {t.value!r}", t.line)
        d.exported = exported
        return d

    def parse_params(self):
        params = []
        while self.at("punct", "("):
            self.next()
            if self.at("punct", ")"):       # empty () => no parameters
                self.next()
                continue
            pname = self.eat("ident").value
            pty = self.parse_type()
            self.eat("punct", ")")
            params.append((pname, pty))
        return params

    def parse_func_decl(self):
        self.eat("kw", "func")
        name = self.eat("ident").value
        params = self.parse_params()
        ret = None
        if self.at("punct", "->"):
            self.next()
            ret = self.parse_type()
        self.eat_terminator()
        body = self.parse_block()
        self.eat("kw", "end")
        self.eat_terminator()
        return FuncDecl(name, params, ret, body)

    def parse_struct_decl(self):
        packed = False
        if self.at("kw", "packed"):
            self.next()
            packed = True
        self.eat("kw", "struct")
        name = self.eat("ident").value
        self.eat_terminator()
        fields = []
        while not self.at("kw", "end"):
            self.skip_newlines()
            if self.at("kw", "end"):
                break
            fname = self.eat("ident").value
            fty = self.parse_type()
            self.eat_terminator()
            fields.append((fname, fty))
        self.eat("kw", "end")
        self.eat_terminator()
        return StructDecl(name, fields, packed)

    def parse_enum_decl(self):
        self.eat("kw", "enum")
        name = self.eat("ident").value
        self.eat_terminator()
        variants = []
        while not self.at("kw", "end"):
            self.skip_newlines()
            if self.at("kw", "end"):
                break
            variants.append(self.eat("ident").value)
            self.eat_terminator()
        self.eat("kw", "end")
        self.eat_terminator()
        return EnumDecl(name, variants)

    def parse_sum_decl(self):
        self.eat("kw", "type")
        name = self.eat("ident").value
        self.eat_terminator()
        variants = []
        while not self.at("kw", "end"):
            self.skip_newlines()
            if self.at("kw", "end"):
                break
            vname = self.eat("ident").value
            fields = []
            while self.at("punct", "("):
                self.next()
                fname = self.eat("ident").value
                fty = self.parse_type()
                self.eat("punct", ")")
                fields.append((fname, fty))
            self.eat_terminator()
            variants.append((vname, fields))
        self.eat("kw", "end")
        self.eat_terminator()
        return SumDecl(name, variants)

    def parse_const_decl(self):
        self.eat("kw", "const")
        name = self.eat("ident").value
        ty = None
        if not self.at("punct", "="):
            ty = self.parse_type()
        self.eat("punct", "=")
        expr = self.parse_expr()
        self.eat_terminator()
        return ConstDecl(name, ty, expr)

    # 'end' / 'elsif' / 'else' close a block
    def parse_block(self, terminators=("end",)):
        body = []
        self.skip_newlines()
        while not (self.at("kw") and self.peek().value in terminators):
            if self.at("eof"):
                raise CardinalError("unexpected end of file in block", self.peek().line)
            body.append(self.parse_stmt())
            self.skip_newlines()
        return body

    # -- statements -------------------------------------------------------- #
    def parse_stmt(self):
        t = self.peek()
        if t.kind == "kw":
            m = getattr(self, "stmt_" + t.value, None)
            if m:
                node = m()
                if getattr(node, "line", None) is None:
                    node.line = t.line
                return node
        raise CardinalError(f"expected statement, got {t.value!r}", t.line)

    def stmt_let(self):
        self.eat("kw", "let")
        name = self.eat("ident").value
        ty = None
        if not self.at("punct", "="):
            ty = self.parse_type()
        self.eat("punct", "=")
        expr = self.parse_expr()
        self.eat_terminator()
        return Let(name, ty, expr, mutable=True)

    def stmt_const(self):
        self.eat("kw", "const")
        name = self.eat("ident").value
        ty = None
        if not self.at("punct", "="):
            ty = self.parse_type()
        self.eat("punct", "=")
        expr = self.parse_expr()
        self.eat_terminator()
        return Let(name, ty, expr, mutable=False)

    def stmt_set(self):
        self.eat("kw", "set")
        target = self.parse_expr()
        self.eat("punct", "=")
        expr = self.parse_expr()
        self.eat_terminator()
        return Set(target, expr)

    def stmt_do(self):
        self.eat("kw", "do")
        call = self.parse_expr()
        self.eat_terminator()
        return Do(call)

    def stmt_if(self):
        self.eat("kw", "if")
        cond = self.parse_expr()
        self.eat_terminator()
        branches = [(cond, self.parse_block(("elsif", "else", "end")))]
        orelse = None
        while self.at("kw", "elsif"):
            self.next()
            c = self.parse_expr()
            self.eat_terminator()
            branches.append((c, self.parse_block(("elsif", "else", "end"))))
        if self.at("kw", "else"):
            self.next()
            self.eat_terminator()
            orelse = self.parse_block(("end",))
        self.eat("kw", "end")
        self.eat_terminator()
        return If(branches, orelse)

    def stmt_while(self):
        self.eat("kw", "while")
        cond = self.parse_expr()
        self.eat_terminator()
        body = self.parse_block(("end",))
        self.eat("kw", "end")
        self.eat_terminator()
        return While(cond, body)

    def stmt_for(self):
        self.eat("kw", "for")
        var = self.eat("ident").value
        if self.at("punct", "="):
            self.next()
            start = self.parse_expr()
            self.eat("kw", "to")
            end = self.parse_expr()
            step = None
            if self.at("kw", "step"):
                self.next()
                step = self.parse_expr()
            self.eat_terminator()
            body = self.parse_block(("end",))
            self.eat("kw", "end")
            self.eat_terminator()
            return ForNum(var, start, end, step, body)
        else:
            self.eat("kw", "in")
            it = self.parse_expr()
            self.eat_terminator()
            body = self.parse_block(("end",))
            self.eat("kw", "end")
            self.eat_terminator()
            return ForIn(var, it, body)

    def stmt_loop(self):
        self.eat("kw", "loop")
        self.eat_terminator()
        body = self.parse_block(("end",))
        self.eat("kw", "end")
        self.eat_terminator()
        return Loop(body)

    def stmt_break(self):
        self.eat("kw", "break")
        self.eat_terminator()
        return Break()

    def stmt_continue(self):
        self.eat("kw", "continue")
        self.eat_terminator()
        return Continue()

    def stmt_pass(self):
        self.eat("kw", "pass")
        self.eat_terminator()
        return Pass()

    def stmt_return(self):
        self.eat("kw", "return")
        expr = None
        if not self.at("newline") and not self.at("eof"):
            expr = self.parse_expr()
        self.eat_terminator()
        return Return(expr)

    def stmt_checked(self):
        self.eat("kw", "checked")
        self.eat_terminator()
        body = self.parse_block(("end",))
        self.eat("kw", "end")
        self.eat_terminator()
        return Checked(body)

    def stmt_match(self):
        self.eat("kw", "match")
        scrutinee = self.parse_expr()
        self.eat_terminator()
        arms = []
        orelse = None
        while self.at("kw", "case"):
            self.next()
            if self.at("punct", "("):
                self.next()
                vname = self.eat("ident").value
                bindings = []
                while not self.at("punct", ")"):
                    bindings.append(self.eat("ident").value)
                self.eat("punct", ")")
            else:
                vname = self.eat("ident").value
                bindings = []
            self.eat_terminator()
            body = self.parse_block(("case", "else", "end"))
            arms.append((vname, bindings, body))
        if self.at("kw", "else"):
            self.next()
            self.eat_terminator()
            orelse = self.parse_block(("end",))
        self.eat("kw", "end")
        self.eat_terminator()
        return Match(scrutinee, arms, orelse)

    # -- expressions ------------------------------------------------------- #
    def parse_expr(self):
        ln = self.peek().line
        node = self._parse_expr()
        if getattr(node, "line", None) is None:
            node.line = ln
        return node

    def _parse_expr(self):
        t = self.peek()
        if t.kind == "punct" and t.value == "(":
            return self.parse_paren_form()
        if t.kind == "punct" and t.value == "[":
            return self.parse_array_lit()
        if t.kind == "punct" and t.value == "{":
            return self.parse_vec_lit()
        if t.kind == "kw" and t.value == "func":
            return self.parse_func_lit()
        return self.parse_postfix(self.parse_atom())

    def parse_vec_lit(self):
        self.eat("punct", "{")
        elems = []
        while not self.at("punct", "}"):
            elems.append(self.parse_expr())
        self.eat("punct", "}")
        return self.parse_postfix(VecLit(elems))

    def parse_func_lit(self):
        self.eat("kw", "func")
        params = self.parse_params()
        ret = None
        if self.at("punct", "->"):
            self.next()
            ret = self.parse_type()
        self.eat_terminator()
        body = self.parse_block(("end",))
        self.eat("kw", "end")
        return FuncLit(params, ret, body)

    def parse_array_lit(self):
        self.eat("punct", "[")
        elems = []
        while not self.at("punct", "]"):
            elems.append(self.parse_expr())
        self.eat("punct", "]")
        return self.parse_postfix(ArrayLit(elems))

    def parse_paren_form(self):
        line = self.peek().line
        self.eat("punct", "(")
        t = self.peek()

        # special forms
        if t.kind == "kw" and t.value == "as":
            self.next()
            val = self.parse_expr()
            ty = self.parse_type()
            self.eat("punct", ")")
            return self.parse_postfix(Cast(val, ty))
        if t.kind == "ident" and t.value == "ref":
            self.next()
            place = self.parse_expr()
            self.eat("punct", ")")
            return self.parse_postfix(Ref(place))
        # `(array T n)` allocation — but not `array::member` (a module path)
        if t.kind == "ident" and t.value == "array" and \
           not (self.peek(1).kind == "punct" and self.peek(1).value == "::"):
            self.next()
            elem = self.parse_type()
            count = self.parse_expr()
            self.eat("punct", ")")
            return self.parse_postfix(ArrayNew(elem, count))
        if t.kind == "ident" and t.value == "vec" and \
           not (self.peek(1).kind == "punct" and self.peek(1).value == "::"):
            self.next()
            elem = self.parse_type()
            self.eat("punct", ")")
            return self.parse_postfix(VecNew(elem))
        if t.kind == "ident" and t.value == "map" and \
           not (self.peek(1).kind == "punct" and self.peek(1).value == "::"):
            self.next()
            kt = self.parse_type()
            vt = self.parse_type()
            self.eat("punct", ")")
            return self.parse_postfix(MapNew(kt, vt))

        # operator forms
        if (t.kind == "punct" and t.value in OPERATOR_SYMS) or \
           (t.kind == "kw" and t.value in WORD_OPS):
            op = self.next().value
            args = []
            while not self.at("punct", ")"):
                args.append(self.parse_expr())
            self.eat("punct", ")")
            return self.parse_postfix(OpCall(op, args))

        # struct-literal vs. call: head then maybe `label:` pairs
        head = self.parse_postfix(self.parse_atom())
        if self.at("ident") and self.peek(1).kind == "punct" and self.peek(1).value == ":":
            fields = []
            while not self.at("punct", ")"):
                fname = self.eat("ident").value
                self.eat("punct", ":")
                fexpr = self.parse_expr()
                fields.append((fname, fexpr))
            self.eat("punct", ")")
            return self.parse_postfix(StructLit(head, fields))

        # ordinary call
        args = []
        while not self.at("punct", ")"):
            args.append(self.parse_expr())
        self.eat("punct", ")")
        return self.parse_postfix(Call(head, args))

    def parse_atom(self):
        ln = self.peek().line
        node = self._parse_atom()
        if getattr(node, "line", None) is None:
            node.line = ln
        return node

    def _parse_atom(self):
        t = self.next()
        if t.kind == "int":
            return IntLit(t.value[0], t.value[1])
        if t.kind == "float":
            return FloatLit(t.value[0], t.value[1])
        if t.kind == "char":
            return CharLit(t.value)
        if t.kind == "str":
            return StrLit(t.value)
        if t.kind == "kw" and t.value == "true":
            return BoolLit(True)
        if t.kind == "kw" and t.value == "false":
            return BoolLit(False)
        if t.kind == "kw" and t.value == "null":
            return NullLit()
        if t.kind in ("ident", "type"):
            # name, possibly a :: path
            if self.at("punct", "::"):
                parts = [t.value]
                while self.at("punct", "::"):
                    self.next()
                    parts.append(self.eat("ident").value if self.at("ident")
                                 else self.eat("type").value)
                return Path(parts)
            return Name(t.value)
        raise CardinalError(f"unexpected token {t.value!r}", t.line)

    def parse_postfix(self, node):
        while True:
            if self.at("punct", "."):
                ln = self.peek().line
                self.next()
                fld = self.eat("ident").value
                node = FieldAccess(node, fld)
                node.line = ln
            elif self.at("punct", "["):
                ln = self.peek().line
                self.next()
                idx = self.parse_expr()
                self.eat("punct", "]")
                node = Index(node, idx)
                node.line = ln
            else:
                return node

    # -- types ------------------------------------------------------------- #
    def parse_type(self):
        t = self.peek()
        if t.kind == "punct" and t.value == "[":
            self.next()
            elem = self.parse_type()
            self.eat("punct", "]")
            return TyArray(elem)
        if t.kind == "punct" and t.value == "{":
            self.next()
            t1 = self.parse_type()
            if self.at("punct", "}"):
                self.next()
                return TyVec(t1)
            t2 = self.parse_type()
            self.eat("punct", "}")
            return TyMap(t1, t2)
        if t.kind == "kw" and t.value == "func":
            self.next()
            self.eat("punct", "(")
            params = []
            while not self.at("punct", "->"):
                params.append(self.parse_type())
            self.eat("punct", "->")
            ret = self.parse_type()
            self.eat("punct", ")")
            return TyFunc(params, ret)
        if t.kind in ("type", "ident"):
            self.next()
            return TyName(t.value)
        raise CardinalError(f"expected type, got {t.value!r}", t.line)


# --------------------------------------------------------------------------- #
# Runtime values
# --------------------------------------------------------------------------- #

@dataclass
class CInt:
    val: int
    ty: Optional[str]   # int type name, or None for untyped literal

@dataclass
class CFloat:
    val: float
    ty: Optional[str]   # 'f32'/'f64' or None

@dataclass
class CChar:
    cp: int

@dataclass
class StructV:
    typename: str
    fields: dict        # name -> value  (value semantics: copied on move)
    defmod: object = None   # defining module name, for to_str-override dispatch (§5.5)

@dataclass
class ArrayV:
    elem: object        # element Type
    items: list         # reference semantics

@dataclass
class VecV:
    elem: object        # element Type
    items: list         # growable; reference semantics

@dataclass
class MapV:
    key: object         # key Type
    val: object         # value Type
    data: dict          # normalized-key -> (key value, value); reference semantics

@dataclass
class EnumV:
    enum: str
    variant: str
    intval: int
    defmod: object = None   # defining module name, for to_str-override dispatch (§5.5)

@dataclass
class SumV:
    sum: str                # sum-type name
    variant: str
    fields: dict            # field name -> value; reference semantics (not copied)
    defmod: object = None   # defining module name, for to_str-override dispatch (§5.5)

@dataclass
class Closure:
    params: list
    ret: object
    body: list
    env: "Env"
    name: str = "<closure>"

@dataclass
class Builtin:
    name: str
    fn: object

@dataclass
class Ref:
    cell: "Cell"

class _Null:
    _inst = None
    def __repr__(self): return "null"
NULL = _Null()

class _Unit:
    def __repr__(self): return "unit"
UNIT = _Unit()


# --------------------------------------------------------------------------- #
# Environments  (variables live in Cells -> capture-by-reference works)
# --------------------------------------------------------------------------- #

@dataclass
class Cell:
    value: object
    mutable: bool = True

class Env:
    def __init__(self, parent=None):
        self.vars: dict[str, Cell] = {}
        self.parent = parent

    def define(self, name, value, mutable=True):
        self.vars[name] = Cell(value, mutable)

    def cell(self, name):
        e = self
        while e:
            if name in e.vars:
                return e.vars[name]
            e = e.parent
        return None


# --------------------------------------------------------------------------- #
# Control-flow signals
# --------------------------------------------------------------------------- #

class BreakSignal(Exception): pass
class ContinueSignal(Exception): pass
class ReturnSignal(Exception):
    def __init__(self, value): self.value = value


# --------------------------------------------------------------------------- #
# Interpreter
# --------------------------------------------------------------------------- #

class Interp:
    def __init__(self, search_dirs):
        self.search_dirs = search_dirs
        self.modules: dict[str, "ModuleScope"] = {}
        self.checked = False    # lexical overflow-check flag (DESIGN.md §5.2)
        self.program_args = []  # exposed via sys::args()

    # ---- module loading ---- #
    def load_module(self, name):
        if name in self.modules:
            return self.modules[name]
        if name in BUILTIN_MODULES:
            ms = BUILTIN_MODULES[name]()
            self.modules[name] = ms
            return ms
        path = self._find(name)
        if not path:
            raise CardinalError(f"module {name!r} not found")
        with open(path) as f:
            src = f.read()
        mod = Parser(lex(src)).parse_module()
        ms = ModuleScope(mod.name)
        self.modules[name] = ms          # register early (cyclic-tolerant-ish)
        self._populate(ms, mod)
        return ms

    def _find(self, name):
        for d in self.search_dirs:
            p = os.path.join(d, name + ".cardinal")
            if os.path.exists(p):
                return p
        return None

    def _populate(self, ms, mod: Module):
        # imports
        for imp in mod.imports:
            other = self.load_module(imp.name)
            if imp.names is None:
                ms.imported_modules[imp.name] = other
            else:
                for nm in imp.names:
                    ms.env.define(nm, other.get(nm), mutable=False)
        # type decls first (so functions can reference them)
        for d in mod.decls:
            if isinstance(d, StructDecl):
                ms.structs[d.name] = d
            elif isinstance(d, EnumDecl):
                ms.enums[d.name] = d
                for idx, v in enumerate(d.variants):
                    ms.enum_variants[(d.name, v)] = idx
            elif isinstance(d, SumDecl):
                ms.sums[d.name] = d
                for vname, _ in d.variants:
                    ms.variants[vname] = d
        # functions and consts
        for d in mod.decls:
            if isinstance(d, FuncDecl):
                clos = Closure(d.params, d.ret, d.body, ms.env, d.name)
                ms.env.define(d.name, clos, mutable=False)
                if d.exported:
                    ms.exports.add(d.name)
            elif isinstance(d, ConstDecl):
                val = self.eval(d.expr, ms.env)
                if d.ty is not None:
                    val = self.coerce(val, d.ty, ms)
                ms.env.define(d.name, val, mutable=False)
                if d.exported:
                    ms.exports.add(d.name)
        ms.interp = self

    # ---- program entry ---- #
    def run(self, path):
        d = os.path.dirname(os.path.abspath(path))
        if d not in self.search_dirs:
            self.search_dirs.insert(0, d)
        with open(path) as f:
            src = f.read()
        mod = Parser(lex(src)).parse_module()
        ms = ModuleScope(mod.name)
        self.modules[mod.name] = ms
        self.current = ms
        self._populate(ms, mod)
        main_cell = ms.env.cell("main")
        if not main_cell:
            raise CardinalError("no 'main' function")
        result = self.call(main_cell.value, [], ms)
        if isinstance(result, CInt):
            return result.val
        return 0

    # ---- statement execution ---- #
    def exec_block(self, stmts, env, ms):
        for s in stmts:
            self.exec_stmt(s, env, ms)

    def exec_stmt(self, s, env, ms):
        k = type(s)
        if k is Let:
            val = self.eval(s.expr, env, ms, expected=s.ty)
            if s.ty is not None:
                val = self.coerce(val, s.ty, ms)
            elif isinstance(val, CInt) and val.ty is None:
                raise CardinalError(f"cannot infer type of '{s.name}' "
                                    f"(untyped numeric literal needs context)")
            env.define(s.name, self.copy_value(val), mutable=s.mutable)
        elif k is Set:
            self.exec_set(s, env, ms)
        elif k is Do:
            self.eval(s.call, env, ms)
        elif k is If:
            for cond, body in s.branches:
                if self.truth(self.eval(cond, env, ms)):
                    self.exec_block(body, Env(env), ms)
                    return
            if s.orelse is not None:
                self.exec_block(s.orelse, Env(env), ms)
        elif k is While:
            while self.truth(self.eval(s.cond, env, ms)):
                try:
                    self.exec_block(s.body, Env(env), ms)
                except BreakSignal:
                    break
                except ContinueSignal:
                    continue
        elif k is ForNum:
            self.exec_fornum(s, env, ms)
        elif k is ForIn:
            self.exec_forin(s, env, ms)
        elif k is Loop:
            while True:
                try:
                    self.exec_block(s.body, Env(env), ms)
                except BreakSignal:
                    break
                except ContinueSignal:
                    continue
        elif k is Break:
            raise BreakSignal()
        elif k is Continue:
            raise ContinueSignal()
        elif k is Return:
            val = self.eval(s.expr, env, ms) if s.expr is not None else UNIT
            raise ReturnSignal(val)
        elif k is Checked:
            saved = self.checked
            self.checked = True
            try:
                self.exec_block(s.body, Env(env), ms)
            finally:
                self.checked = saved
        elif k is Match:
            self.exec_match(s, env, ms)
        elif k is Pass:
            pass
        else:
            raise CardinalError(f"unknown statement {k.__name__}")

    def exec_match(self, s, env, ms):
        v = self.eval(s.scrutinee, env, ms)
        if not isinstance(v, SumV):
            raise Panic("match on a non-sum value")
        for vname, bindings, body in s.arms:
            if vname == v.variant:
                armenv = Env(env)
                vd = self.find_variant(vname, ms)
                spec = [fn for fn, _ in self.variant_fields(vd, vname)]
                for b, fn in zip(bindings, spec):
                    armenv.define(b, v.fields[fn], mutable=False)
                self.exec_block(body, armenv, ms)
                return
        if s.orelse is not None:
            self.exec_block(s.orelse, Env(env), ms)
            return
        raise Panic(f"no match arm for variant {v.variant}")

    def exec_fornum(self, s, env, ms):
        start = self.eval(s.start, env, ms)
        end = self.eval(s.end, env, ms)
        step = self.eval(s.step, env, ms) if s.step else None
        sv, ev, ty = self.unify_ints([start, end] + ([step] if step else []))
        stepv = sv[2] if step else 1
        i = sv[0]
        limit = sv[1]
        while i < limit:               # half-open [start, end)
            loopenv = Env(env)
            loopenv.define(s.var, CInt(i, ty), mutable=True)
            try:
                self.exec_block(s.body, loopenv, ms)
            except BreakSignal:
                break
            except ContinueSignal:
                pass
            i += stepv
        return

    def exec_forin(self, s, env, ms):
        coll = self.eval(s.iterable, env, ms)
        if not isinstance(coll, (ArrayV, VecV)):
            raise Panic("for-in expects an array or vector")
        for item in list(coll.items):
            loopenv = Env(env)
            loopenv.define(s.var, self.copy_value(item), mutable=True)
            try:
                self.exec_block(s.body, loopenv, ms)
            except BreakSignal:
                break
            except ContinueSignal:
                continue

    def exec_set(self, s, env, ms):
        rhs = self.eval(s.expr, env, ms)
        t = s.target
        if isinstance(t, Name):
            cell = env.cell(t.ident)
            if cell is None:
                raise CardinalError(f"assignment to undefined '{t.ident}'")
            if not cell.mutable:
                raise CardinalError(f"cannot assign to immutable '{t.ident}'")
            rhs = self.coerce_like(rhs, cell.value, ms)
            cell.value = self.copy_value(rhs)
        elif isinstance(t, FieldAccess):
            obj = self.eval_place(t.obj, env, ms)
            obj = self.deref(obj)
            if not isinstance(obj, StructV):
                raise Panic("field assignment on non-struct")
            cur = obj.fields.get(t.field)
            rhs = self.coerce_like(rhs, cur, ms)
            obj.fields[t.field] = self.copy_value(rhs)
        elif isinstance(t, Index):
            obj = self.deref(self.eval_place(t.obj, env, ms))
            if isinstance(obj, MapV):
                key = self.coerce(self.eval(t.index, env, ms), obj.key, ms)
                val = self.coerce(rhs, obj.val, ms)
                obj.data[_norm_key(key)] = (key, self.copy_value(val))
                return
            if not isinstance(obj, (ArrayV, VecV)):
                raise Panic("index assignment on non-array")
            idx = self.index_int(self.eval(t.index, env, ms))
            if idx < 0 or idx >= len(obj.items):
                raise Panic(f"index out of bounds: {idx} (len {len(obj.items)})")
            rhs = self.coerce(rhs, obj.elem, ms)
            obj.items[idx] = self.copy_value(rhs)
        else:
            raise CardinalError("invalid assignment target")

    def eval_place(self, node, env, ms):
        # like eval, but returns the actual stored object (no copy) for set targets
        return self.eval(node, env, ms)

    # ---- expression evaluation ---- #
    def eval(self, e, env, ms=None, expected=None):
        ms = ms or self.current
        k = type(e)
        if k is IntLit:
            return CInt(e.value, e.ty)
        if k is FloatLit:
            return CFloat(e.value, e.ty)
        if k is BoolLit:
            return e.value
        if k is CharLit:
            return CChar(e.cp)
        if k is StrLit:
            return e.value
        if k is NullLit:
            return NULL
        if k is Name:
            return self.lookup(e.ident, env, ms)
        if k is Path:
            return self.lookup_path(e.parts, ms)
        if k is FieldAccess:
            obj = self.deref(self.eval(e.obj, env, ms))
            if not isinstance(obj, StructV):
                raise Panic(f"field access '.{e.field}' on non-struct")
            if e.field not in obj.fields:
                raise Panic(f"no field '{e.field}' on {obj.typename}")
            return obj.fields[e.field]
        if k is Index:
            obj = self.deref(self.eval(e.obj, env, ms))
            if isinstance(obj, MapV):
                key = self.coerce(self.eval(e.index, env, ms), obj.key, ms)
                nk = _norm_key(key)
                if nk not in obj.data:
                    raise Panic("map key not found")
                return obj.data[nk][1]
            if not isinstance(obj, (ArrayV, VecV)):
                raise Panic("indexing non-array")
            idx = self.index_int(self.eval(e.index, env, ms))
            if idx < 0 or idx >= len(obj.items):
                raise Panic(f"index out of bounds: {idx} (len {len(obj.items)})")
            return obj.items[idx]
        if k is OpCall:
            return self.eval_op(e, env, ms)
        if k is Cast:
            return self.eval_cast(e, env, ms)
        if k is Ref:
            return self.eval_ref(e, env, ms)
        if k is Call:
            return self.eval_call(e, env, ms)
        if k is StructLit:
            return self.eval_struct_lit(e, env, ms, expected)
        if k is ArrayLit:
            return self.eval_array_lit(e, env, ms, expected)
        if k is ArrayNew:
            count = self.index_int(self.eval(e.count, env, ms))
            if count < 0:
                raise Panic("array length cannot be negative")
            zero = self.zero_value(e.elem, ms)
            return ArrayV(e.elem, [self.copy_value(zero) for _ in range(count)])
        if k is VecNew:
            return VecV(e.elem, [])
        if k is MapNew:
            return MapV(e.key, e.val, {})
        if k is VecLit:
            elem_ty = expected.elem if isinstance(expected, TyVec) else None
            items = []
            for el in e.elements:
                v = self.eval(el, env, ms, expected=elem_ty)
                if elem_ty is not None:
                    v = self.coerce(v, elem_ty, ms)
                items.append(self.copy_value(v))
            if elem_ty is None:
                tys = {self.type_of(v) for v in items}
                if not items or None in tys or len(tys) != 1:
                    raise CardinalError("cannot infer vector element type — "
                                        "annotate, e.g. let xs {i32} = {...}")
                elem_ty = TyName(tys.pop())
            return VecV(elem_ty, items)
        if k is FuncLit:
            return Closure(e.params, e.ret, e.body, env, "<closure>")
        raise CardinalError(f"cannot evaluate {k.__name__}")

    def lookup(self, name, env, ms):
        cell = env.cell(name)
        if cell is not None:
            return cell.value
        cell = ms.env.cell(name)
        if cell is not None:
            return cell.value
        if name in GLOBAL_BUILTINS:
            return GLOBAL_BUILTINS[name]
        vd = self.find_variant(name, ms)
        if vd is not None:
            if self.variant_fields(vd, name):
                raise CardinalError(f"variant {name} needs fields; use ({name} ...)")
            return SumV(vd.name, name, {}, self.variant_defmod(name, ms))
        raise CardinalError(f"undefined name '{name}'")

    def lookup_path(self, parts, ms):
        # enum variant?  Enum::Variant
        if len(parts) == 2 and parts[0] in ms.enums:
            ed = ms.enums[parts[0]]
            if parts[1] not in ed.variants:
                raise CardinalError(f"no variant {parts[1]} in enum {parts[0]}")
            return EnumV(parts[0], parts[1], ed.variants.index(parts[1]), ms.name)
        # module member  mod::name
        mod = ms.imported_modules.get(parts[0])
        if mod is None:
            mod = self.modules.get(parts[0])
        if mod is not None and len(parts) == 2:
            return mod.get(parts[1])
        raise CardinalError(f"cannot resolve path {'::'.join(parts)}")

    def eval_call(self, e, env, ms):
        callee = self.eval(e.callee, env, ms)
        # a Name/Path head that resolves to a struct type => construction (positional)
        args = [self.eval(a, env, ms) for a in e.args]
        return self.call(callee, args, ms)

    def call(self, callee, args, ms):
        if isinstance(callee, Builtin):
            return callee.fn(self, args)
        if not isinstance(callee, Closure):
            raise Panic(f"attempt to call non-function {callee!r}")
        defining_ms = getattr(callee.env, "owner_ms", ms)
        call_env = Env(callee.env)
        if len(args) != len(callee.params):
            raise CardinalError(
                f"{callee.name}: expected {len(callee.params)} args, got {len(args)}")
        for (pname, pty), a in zip(callee.params, args):
            a = self.coerce(a, pty, defining_ms)
            call_env.define(pname, self.copy_value(a), mutable=True)
        saved = self.checked
        self.checked = False           # `checked` is lexical: does not cross calls
        try:
            self.exec_block(callee.body, call_env, defining_ms)
            ret = UNIT
        except ReturnSignal as r:
            ret = r.value
        finally:
            self.checked = saved
        if callee.ret is not None:
            ret = self.coerce(ret, callee.ret, defining_ms)
        return self.copy_value(ret)

    def eval_struct_lit(self, e, env, ms, expected):
        if isinstance(e.typename, Name):
            tyname = e.typename.ident
        elif isinstance(e.typename, Path):
            tyname = e.typename.parts[-1]
        else:
            raise CardinalError("struct construction needs a type name")
        vd = self.find_variant(tyname, ms)
        if vd is not None:
            return self.construct_variant(vd, tyname, e.fields, env, ms)
        sd = self.find_struct(tyname, ms)
        if sd is None:
            raise CardinalError(f"unknown struct '{tyname}'")
        fld_types = dict(sd.fields)
        vals = {}
        for fname, fexpr in e.fields:
            if fname not in fld_types:
                raise CardinalError(f"struct {tyname} has no field '{fname}'")
            v = self.eval(fexpr, env, ms, expected=fld_types[fname])
            vals[fname] = self.copy_value(self.coerce(v, fld_types[fname], ms))
        # Store fields in DECLARATION order (not literal order) so display is
        # canonical and deterministic, matching the compiled struct layout.
        given = {}
        for fname, _ in sd.fields:
            if fname not in vals:
                raise CardinalError(f"missing field '{fname}' in {tyname}")
            given[fname] = vals[fname]
        return StructV(tyname, given, self.find_struct_ms(tyname, ms))

    def eval_array_lit(self, e, env, ms, expected):
        elem_ty = None
        if isinstance(expected, TyArray):
            elem_ty = expected.elem
        items = []
        for el in e.elements:
            v = self.eval(el, env, ms, expected=elem_ty)
            if elem_ty is not None:
                v = self.coerce(v, elem_ty, ms)
            items.append(v)
        if elem_ty is None:
            # infer from concretely-typed elements, else error
            tys = {self.type_of(v) for v in items}
            if len(items) == 0 or None in tys or len(tys) != 1:
                raise CardinalError(
                    "cannot infer array element type — annotate, e.g. let xs [i32] = [...]")
            elem_ty = TyName(tys.pop())
        return ArrayV(elem_ty, items)

    def eval_ref(self, e, env, ms):
        t = e.place
        if isinstance(t, Name):
            cell = env.cell(t.ident) or ms.env.cell(t.ident)
            if cell is None:
                raise CardinalError(f"cannot ref undefined '{t.ident}'")
            return Ref(cell)
        raise CardinalError("ref currently supports simple variables only")

    # ---- operators ---- #
    def eval_op(self, e, env, ms):
        op = e.op
        if op == "and":
            for a in e.args:
                if not self.truth(self.eval(a, env, ms)):
                    return False
            return True
        if op == "or":
            for a in e.args:
                if self.truth(self.eval(a, env, ms)):
                    return True
            return False
        if op == "not":
            return not self.truth(self.eval(e.args[0], env, ms))

        vals = [self.eval(a, env, ms) for a in e.args]

        if op in ("+", "-", "*", "/", "%"):
            return self.arith(op, vals)
        if op in ("<", "<=", ">", ">=", "==", "!="):
            return self.compare(op, vals)
        if op in ("band", "bor", "bxor", "shl", "shr", "bnot"):
            return self.bitwise(op, vals)
        raise CardinalError(f"unknown operator {op}")

    def arith(self, op, vals):
        # unary minus
        if op == "-" and len(vals) == 1:
            v = vals[0]
            if isinstance(v, CFloat):
                return CFloat(-v.val, v.ty)
            iv = self._as_int(v)
            return self.wrap_int(-iv.val, iv.ty)
        if any(isinstance(v, CFloat) for v in vals):
            fvals, ty = self.unify_floats(vals)
            acc = fvals[0]
            for x in fvals[1:]:
                # if/elif (not a dict literal) so only the selected op evaluates —
                # a dict eagerly ran every branch, hitting _fzero()/%-by-zero for
                # ANY op whenever the RHS was 0.0.
                if op == "+": acc = acc + x
                elif op == "-": acc = acc - x
                elif op == "*": acc = acc * x
                elif op == "/": acc = acc / x if x != 0 else self._fzero()
                elif op == "%": acc = acc % x if x != 0 else self._fzero()
            return CFloat(self._round_float(acc, ty), ty)
        ivals, _, ty = self.unify_ints(vals)
        acc = ivals[0]
        for x in ivals[1:]:
            if op == "+": acc = acc + x
            elif op == "-": acc = acc - x
            elif op == "*": acc = acc * x
            elif op == "/":
                if x == 0:
                    raise Panic("integer division by zero")
                acc = int(acc / x)       # truncate toward zero
            elif op == "%":
                if x == 0:
                    raise Panic("integer modulo by zero")
                acc = acc - int(acc / x) * x
        return self.wrap_int(acc, ty)

    def bitwise(self, op, vals):
        if op == "bnot":
            iv = self._as_int(vals[0])
            return self.wrap_int(~iv.val, iv.ty)
        if op in ("shl", "shr"):
            a = self._as_int(vals[0]); b = self._as_int(vals[1])
            r = a.val << b.val if op == "shl" else a.val >> b.val
            return self.wrap_int(r, a.ty)
        ivals, _, ty = self.unify_ints(vals)
        acc = ivals[0]
        for x in ivals[1:]:
            if op == "band": acc &= x
            elif op == "bor": acc |= x
            elif op == "bxor": acc ^= x
        return self.wrap_int(acc, ty)

    def compare(self, op, vals):
        a, b = vals[0], vals[1]
        if isinstance(a, CFloat) or isinstance(b, CFloat):
            fs, _ = self.unify_floats([a, b]); x, y = fs[0], fs[1]
        elif isinstance(a, CInt) or isinstance(b, CInt):
            xs, _, _ = self.unify_ints([a, b]); x, y = xs[0], xs[1]
        elif isinstance(a, CChar) and isinstance(b, CChar):
            x, y = a.cp, b.cp
        else:
            # equality-only types: enums, structs, bool, str, null
            if op == "==": return self._eq(a, b)
            if op == "!=": return not self._eq(a, b)
            raise Panic(f"ordering comparison not supported for {self.disp_type(a)}")
        if op == "<":  return x < y
        if op == "<=": return x <= y
        if op == ">":  return x > y
        if op == ">=": return x >= y
        if op == "==": return x == y
        return x != y

    def _eq(self, a, b):
        if a is NULL or b is NULL:
            return a is NULL and b is NULL
        if isinstance(a, EnumV) and isinstance(b, EnumV):
            return a.enum == b.enum and a.variant == b.variant
        return a == b

    # ---- casts ---- #
    def eval_cast(self, e, env, ms):
        v = self.eval(e.expr, env, ms)
        ty = e.ty
        if not isinstance(ty, TyName):
            raise CardinalError("cast target must be a primitive numeric type")
        name = ty.name
        if name in INT_TYPES:
            src = v.val if isinstance(v, (CInt, CFloat)) else None
            if src is None:
                raise Panic("cannot cast non-numeric to int")
            return self.wrap_int(int(src), name)
        if name in FLOAT_TYPES:
            if isinstance(v, (CInt, CFloat)):
                return CFloat(self._round_float(float(v.val), name), name)
            raise Panic("cannot cast non-numeric to float")
        raise CardinalError(f"unsupported cast target {name}")

    # ---- numeric helpers ---- #
    def wrap_int(self, value, ty):
        if ty is None:
            return CInt(value, None)
        bits, signed = INT_TYPES[ty]
        mask = (1 << bits) - 1
        v = value & mask
        if signed and v >= (1 << (bits - 1)):
            v -= (1 << bits)
        if self.checked and v != value:
            raise Panic(f"integer overflow in {ty} (checked)")
        return CInt(v, ty)

    def unify_ints(self, vals):
        ints = []
        for v in vals:
            if isinstance(v, CInt):
                ints.append(v)
            elif isinstance(v, CChar):
                ints.append(CInt(v.cp, "u32"))
            else:
                raise Panic(f"expected integer operand, got {self.disp_type(v)}")
        concrete = {v.ty for v in ints if v.ty is not None}
        if len(concrete) > 1:
            raise CardinalError(f"mismatched integer types: {sorted(concrete)} "
                                f"(no implicit promotion)")
        ty = concrete.pop() if concrete else None
        if ty is not None:
            for v in ints:
                if v.ty is None:
                    self._check_fits(v.val, ty)
        return [v.val for v in ints], None, ty

    def _as_int(self, v):
        if isinstance(v, CInt):
            return v
        if isinstance(v, CChar):
            return CInt(v.cp, "u32")
        raise Panic(f"expected integer, got {self.disp_type(v)}")

    def unify_floats(self, vals):
        fs = []
        concrete = set()
        for v in vals:
            if isinstance(v, CFloat):
                fs.append(v.val)
                if v.ty: concrete.add(v.ty)
            elif isinstance(v, CInt) and v.ty is None:
                fs.append(float(v.val))
            else:
                raise Panic("cannot mix int and float without a cast")
        if len(concrete) > 1:
            raise CardinalError(f"mismatched float types: {sorted(concrete)}")
        ty = concrete.pop() if concrete else "f64"
        return fs, ty

    def _check_fits(self, value, ty):
        bits, signed = INT_TYPES[ty]
        if signed:
            lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
        else:
            lo, hi = 0, (1 << bits) - 1
        if not (lo <= value <= hi):
            raise CardinalError(f"literal {value} does not fit in {ty}")

    def _round_float(self, v, ty):
        if ty == "f32":
            import struct
            return struct.unpack("f", struct.pack("f", v))[0]
        return v

    def _fzero(self):
        raise Panic("float division by zero")

    def index_int(self, v):
        if isinstance(v, CInt):
            return v.val
        raise Panic("array index must be an integer")

    # ---- coercion / typing ---- #
    def coerce(self, v, ty, ms):
        """Coerce value v to declared Type ty (the only implicit step: untyped
        literal -> concrete numeric; null -> any reference type)."""
        if isinstance(ty, TyName):
            name = ty.name
            if name in INT_TYPES:
                if isinstance(v, CInt):
                    if v.ty is None:
                        self._check_fits(v.val, name)
                        return CInt(v.val, name)
                    if v.ty == name:
                        return v
                    raise CardinalError(f"expected {name}, got {v.ty} (use a cast)")
                raise CardinalError(f"expected {name}, got {self.disp_type(v)}")
            if name in FLOAT_TYPES:
                if isinstance(v, CFloat):
                    if v.ty is None or v.ty == name:
                        return CFloat(self._round_float(v.val, name), name)
                    raise CardinalError(f"expected {name}, got {v.ty}")
                raise CardinalError(f"expected {name}, got {self.disp_type(v)}")
            if name == "bool":
                if isinstance(v, bool): return v
                raise CardinalError(f"expected bool, got {self.disp_type(v)}")
            if name == "char":
                if isinstance(v, CChar): return v
                raise CardinalError("expected char")
            if name == "str":
                if isinstance(v, str): return v
                raise CardinalError("expected str")
            if name == "unit":
                return UNIT
            if name == "handle":
                return v
            # struct / enum / named
            if v is NULL:
                return NULL
            if isinstance(v, StructV) and v.typename == name:
                return v
            if isinstance(v, EnumV) and v.enum == name:
                return v
            if isinstance(v, SumV) and v.sum == name:
                return v
            # allow names that are structs/enums/sums in scope even if value matches
            if isinstance(v, (StructV, EnumV, SumV)):
                return v
            raise CardinalError(f"expected {name}, got {self.disp_type(v)}")
        if isinstance(ty, TyArray):
            if v is NULL or isinstance(v, ArrayV):
                return v
            raise CardinalError(f"expected array, got {self.disp_type(v)}")
        if isinstance(ty, TyVec):
            if v is NULL or isinstance(v, VecV):
                return v
            raise CardinalError(f"expected vector, got {self.disp_type(v)}")
        if isinstance(ty, TyMap):
            if v is NULL or isinstance(v, MapV):
                return v
            raise CardinalError(f"expected map, got {self.disp_type(v)}")
        if isinstance(ty, TyFunc):
            if v is NULL or isinstance(v, (Closure, Builtin)):
                return v
            raise CardinalError("expected function")
        return v

    def coerce_like(self, v, current, ms):
        """For `set x = v`: coerce v to the type currently held by x."""
        if isinstance(current, CInt) and current.ty is not None:
            return self.coerce(v, TyName(current.ty), ms)
        if isinstance(current, CFloat) and current.ty is not None:
            return self.coerce(v, TyName(current.ty), ms)
        if isinstance(current, CInt) and isinstance(v, CInt) and v.ty is None:
            return v
        return v

    def type_of(self, v):
        if isinstance(v, CInt): return v.ty
        if isinstance(v, CFloat): return v.ty
        if isinstance(v, bool): return "bool"
        if isinstance(v, CChar): return "char"
        if isinstance(v, str): return "str"
        if isinstance(v, StructV): return v.typename
        if isinstance(v, EnumV): return v.enum
        if isinstance(v, SumV): return v.sum
        return None

    def disp_type(self, v):
        return self.type_of(v) or type(v).__name__

    # ---- misc ---- #
    def deref(self, v):
        return v.cell.value if isinstance(v, Ref) else v

    def truth(self, v):
        if isinstance(v, bool):
            return v
        raise Panic(f"condition must be bool, got {self.disp_type(v)}")

    def copy_value(self, v):
        """Value semantics for structs: deep-copy struct values on every move.
        Arrays/closures/handles keep reference semantics."""
        if isinstance(v, StructV):
            return StructV(v.typename,
                           {k: self.copy_value(x) for k, x in v.fields.items()},
                           v.defmod)
        return v

    def zero_value(self, ty, ms):
        if isinstance(ty, TyName):
            n = ty.name
            if n in INT_TYPES: return CInt(0, n)
            if n in FLOAT_TYPES: return CFloat(0.0, n)
            if n == "bool": return False
            if n == "char": return CChar(0)
            if n == "str": return ""
            sd = self.find_struct(n, ms)
            if sd is not None:
                return StructV(n, {fn: self.zero_value(ft, ms) for fn, ft in sd.fields},
                               self.find_struct_ms(n, ms))
            return NULL          # enums/handles/refs default to null
        return NULL              # arrays/functions default to null

    def find_struct_ms(self, name, ms):
        # name of the module that DEFINES struct `name` (for to_str-override dispatch)
        if name in ms.structs:
            return ms.name
        for mod in ms.imported_modules.values():
            if name in mod.structs:
                return mod.name
        return None

    def find_struct(self, name, ms):
        if name in ms.structs:
            return ms.structs[name]
        for mod in ms.imported_modules.values():
            if name in mod.structs:
                return mod.structs[name]
        return None

    def find_variant(self, name, ms):
        if name in ms.variants:
            return ms.variants[name]
        for mod in ms.imported_modules.values():
            if name in mod.variants:
                return mod.variants[name]
        return None

    def variant_defmod(self, name, ms):
        # The module that defines the sum owning variant `name` (for the §5.5
        # to_str-override dispatch, so a sum's display travels with its type).
        if name in ms.variants:
            return ms.name
        for mod in ms.imported_modules.values():
            if name in mod.variants:
                return mod.name
        return None

    def variant_fields(self, vd, vname):
        for vn, fs in vd.variants:
            if vn == vname:
                return fs
        return []

    def construct_variant(self, vd, vname, given_fields, env, ms):
        spec = self.variant_fields(vd, vname)
        fty = dict(spec)
        given = {}
        for fname, fexpr in given_fields:
            if fname not in fty:
                raise CardinalError(f"variant {vname} has no field '{fname}'")
            v = self.eval(fexpr, env, ms, expected=fty[fname])
            given[fname] = self.copy_value(self.coerce(v, fty[fname], ms))
        # Store fields in DECLARATION order (not construction-label order) so the
        # display is canonical and matches the compiled layout (DESIGN §5.5) — the
        # same normalization eval_struct does for structs.
        ordered = {}
        for fname, _ in spec:
            if fname not in given:
                raise CardinalError(f"missing field '{fname}' in {vname}")
            ordered[fname] = given[fname]
        return SumV(vd.name, vname, ordered, self.variant_defmod(vname, ms))


# --------------------------------------------------------------------------- #
# Module scope
# --------------------------------------------------------------------------- #

class ModuleScope:
    def __init__(self, name):
        self.name = name
        self.env = Env()
        self.env.owner_ms = self
        self.structs = {}
        self.enums = {}
        self.enum_variants = {}
        self.sums = {}            # name -> SumDecl
        self.variants = {}        # variant name -> SumDecl (program-unique)
        self.imported_modules = {}
        self.exports = set()
        self.interp = None

    def get(self, name):
        if self.exports and name not in self.exports:
            # builtin modules leave exports empty => everything visible
            raise CardinalError(f"'{name}' is not exported from module {self.name}")
        cell = self.env.cell(name)
        if cell is None:
            raise CardinalError(f"module {self.name} has no '{name}'")
        return cell.value


# --------------------------------------------------------------------------- #
# Builtins
# --------------------------------------------------------------------------- #

def _to_str_override(interp, defmod, typename):
    # The user-provided `<typename>_to_str (T) -> str` in the type's defining
    # module, if present (DESIGN §5.5). The checker has validated its signature.
    if defmod is None:
        return None
    msc = interp.modules.get(defmod)
    if msc is None:
        return None
    cell = msc.env.cell(typename + "_to_str")
    if cell is None:
        return None
    fn = cell.value
    if isinstance(fn, Closure) and len(fn.params) == 1:
        return fn
    return None

def _display(interp, v):
    # The value's string form (DESIGN §5.5): a user type dispatches to its
    # (overridable) per-type hook; otherwise the canonical form, recursing here
    # so nested overrides apply. io::print is print(to_str(x)).
    if isinstance(v, CInt): return str(v.val)
    if isinstance(v, CFloat): return "%g" % v.val   # DESIGN §5.5: %g, matches the C backend
    if isinstance(v, bool): return "true" if v else "false"
    if isinstance(v, CChar): return chr(v.cp)
    if isinstance(v, str): return v
    if v is NULL: return "null"
    if v is UNIT: return "unit"
    if isinstance(v, (Closure, Builtin)): return "<closure>"   # DESIGN §5.5
    if isinstance(v, EnumV):
        fn = _to_str_override(interp, v.defmod, v.enum)
        if fn is not None:
            return interp.call(fn, [v], interp.modules[v.defmod])
        return f"{v.enum}::{v.variant}"
    if isinstance(v, SumV):
        fn = _to_str_override(interp, v.defmod, v.sum)
        if fn is not None:
            return interp.call(fn, [v], interp.modules[v.defmod])
        inner = " ".join(f"{k}: {_display(interp, x)}" for k, x in v.fields.items())
        return f"({v.variant}{(' ' + inner) if inner else ''})"
    if isinstance(v, StructV):
        fn = _to_str_override(interp, v.defmod, v.typename)
        if fn is not None:
            return interp.call(fn, [v], interp.modules[v.defmod])
        inner = " ".join(f"{k}: {_display(interp, x)}" for k, x in v.fields.items())
        return f"({v.typename} {inner})"
    if isinstance(v, ArrayV):
        return "[" + " ".join(_display(interp, x) for x in v.items) + "]"
    if isinstance(v, VecV):
        return "{" + " ".join(_display(interp, x) for x in v.items) + "}"
    if isinstance(v, MapV):
        return "{" + " ".join(f"{_display(interp, k)}: {_display(interp, val)}"
                              for k, val in v.data.values()) + "}"
    return str(v)


def builtin_io():
    ms = ModuleScope("io")
    def _println(interp, args):
        print("".join(_display(interp, a) for a in args))
        return UNIT
    def _print(interp, args):
        sys.stdout.write("".join(_display(interp, a) for a in args))
        return UNIT
    ms.env.define("println", Builtin("io::println", _println), mutable=False)
    ms.env.define("print", Builtin("io::print", _print), mutable=False)
    return ms


def builtin_strings():
    ms = ModuleScope("strings")
    def _chars(interp, args):
        return ArrayV(TyName("char"), [CChar(ord(c)) for c in args[0]])
    def _concat(interp, args):
        return args[0] + args[1]
    def _substr(interp, args):
        s, start, count = args[0], args[1].val, args[2].val
        return s[start:start + count]
    def _from_char(interp, args):
        return chr(args[0].cp)
    def _eq(interp, args):
        return args[0] == args[1]
    ms.env.define("chars", Builtin("strings::chars", _chars), mutable=False)
    ms.env.define("concat", Builtin("strings::concat", _concat), mutable=False)
    ms.env.define("substr", Builtin("strings::substr", _substr), mutable=False)
    ms.env.define("from_char", Builtin("strings::from_char", _from_char), mutable=False)
    ms.env.define("eq", Builtin("strings::eq", _eq), mutable=False)
    return ms


def builtin_convert():
    ms = ModuleScope("convert")
    def _ord(interp, args):
        return CInt(args[0].cp, "u32")
    def _chr(interp, args):
        return CChar(args[0].val)
    def _int_to_str(interp, args):
        return str(args[0].val)
    def _str_to_int(interp, args):
        # Strict ASCII base-10 (matches the C runtime cl_convert__str_to_int).
        # NOT Python int(): no underscore separators, no Unicode digits/whitespace
        # — those are bootstrap-host accidents, not a Cardinal semantic.
        s = args[0]
        i, j = 0, len(s)
        while i < j and s[i] in " \t\n\r": i += 1
        while j > i and s[j - 1] in " \t\n\r": j -= 1
        neg = False
        if i < j and s[i] in "+-":
            neg = (s[i] == "-"); i += 1
        if i >= j:
            raise Panic(f"str_to_int: not an integer: {args[0]!r}")
        acc = 0
        for k in range(i, j):
            if not ("0" <= s[k] <= "9"):
                raise Panic(f"str_to_int: not an integer: {args[0]!r}")
            acc = acc * 10 + (ord(s[k]) - 48)
        return CInt(-acc if neg else acc, "i64")
    ms.env.define("ord", Builtin("convert::ord", _ord), mutable=False)
    ms.env.define("chr", Builtin("convert::chr", _chr), mutable=False)
    ms.env.define("int_to_str", Builtin("convert::int_to_str", _int_to_str), mutable=False)
    ms.env.define("str_to_int", Builtin("convert::str_to_int", _str_to_int), mutable=False)
    return ms


def _panic(interp, args):
    msg = _display(interp, args[0]) if args else "panic"
    raise Panic(msg)

def _norm_key(k):
    if isinstance(k, bool): return ("b", k)
    if isinstance(k, str): return ("s", k)
    if isinstance(k, CInt): return ("i", k.ty, k.val)
    if isinstance(k, CChar): return ("c", k.cp)
    if isinstance(k, EnumV): return ("e", k.enum, k.variant)
    raise Panic("unhashable map key")

def _len(interp, args):
    v = args[0]
    if isinstance(v, (ArrayV, VecV)):
        return CInt(len(v.items), "u64")
    if isinstance(v, MapV):
        return CInt(len(v.data), "u64")
    if isinstance(v, str):
        return CInt(len(v), "u64")
    raise Panic("len expects an array, vector, map, or str")

def _map_has(interp, args):
    m = args[0]
    if not isinstance(m, MapV): raise Panic("map_has expects a map")
    return _norm_key(interp.coerce(args[1], m.key, interp.current)) in m.data

def _map_del(interp, args):
    m = args[0]
    if not isinstance(m, MapV): raise Panic("map_del expects a map")
    m.data.pop(_norm_key(interp.coerce(args[1], m.key, interp.current)), None)
    return UNIT

def _map_keys(interp, args):
    m = args[0]
    if not isinstance(m, MapV): raise Panic("map_keys expects a map")
    return VecV(m.key, [kv for kv, _ in m.data.values()])

def _push(interp, args):
    v = args[0]
    if not isinstance(v, VecV):
        raise Panic("push expects a vector")
    v.items.append(interp.copy_value(interp.coerce(args[1], v.elem, interp.current)))
    return UNIT

def _pop(interp, args):
    v = args[0]
    if not isinstance(v, VecV):
        raise Panic("pop expects a vector")
    if not v.items:
        raise Panic("pop from empty vector")
    return v.items.pop()

def _to_str(interp, args):
    # The string form of a value (DESIGN §5.5). io::print is print(to_str(x)).
    return _display(interp, args[0])

GLOBAL_BUILTINS = {
    "panic": Builtin("panic", _panic),
    "len": Builtin("len", _len),
    "push": Builtin("push", _push),
    "pop": Builtin("pop", _pop),
    "map_has": Builtin("map_has", _map_has),
    "map_del": Builtin("map_del", _map_del),
    "map_keys": Builtin("map_keys", _map_keys),
    "to_str": Builtin("to_str", _to_str),
}


def builtin_fs():
    ms = ModuleScope("fs")
    def _read(interp, args):
        try:
            with open(args[0]) as f:
                return f.read()
        except OSError as e:
            raise Panic(f"read_file: {e}")
    def _write(interp, args):
        try:
            with open(args[0], "w") as f:
                f.write(args[1])
            return UNIT
        except OSError as e:
            raise Panic(f"write_file: {e}")
    def _read_cb(interp, args):
        contents = _read(interp, [args[0]])
        interp.call(args[1], [contents], interp.current)   # cb: func(str -> unit)
        return UNIT
    def _write_cb(interp, args):
        ok = True
        try:
            with open(args[0], "w") as f:
                f.write(args[1])
        except OSError:
            ok = False
        interp.call(args[2], [ok], interp.current)         # cb: func(bool -> unit)
        return UNIT
    def _exists(interp, args):
        return os.path.exists(args[0])
    ms.env.define("read_file", Builtin("fs::read_file", _read), mutable=False)
    ms.env.define("write_file", Builtin("fs::write_file", _write), mutable=False)
    ms.env.define("read_file_cb", Builtin("fs::read_file_cb", _read_cb), mutable=False)
    ms.env.define("write_file_cb", Builtin("fs::write_file_cb", _write_cb), mutable=False)
    ms.env.define("exists", Builtin("fs::exists", _exists), mutable=False)
    return ms


def builtin_sys():
    ms = ModuleScope("sys")
    def _args(interp, args):
        return VecV(TyName("str"), list(interp.program_args))
    ms.env.define("args", Builtin("sys::args", _args), mutable=False)
    return ms


BUILTIN_MODULES = {
    "io": builtin_io,
    "strings": builtin_strings,
    "convert": builtin_convert,
    "fs": builtin_fs,
    "sys": builtin_sys,
}


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def main(argv):
    if len(argv) != 2:
        print("usage: interpreter.py <program.cardinal>", file=sys.stderr)
        return 2
    path = argv[1]
    interp = Interp(search_dirs=[os.getcwd()])
    try:
        return interp.run(path)
    except CardinalError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except Panic as e:
        print(f"panic: {e.msg}", file=sys.stderr)
        return 101


if __name__ == "__main__":
    sys.exit(main(sys.argv))
