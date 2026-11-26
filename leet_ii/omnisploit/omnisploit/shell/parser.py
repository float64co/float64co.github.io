"""A small bash-ish tokenizer/parser: quotes, $VAR, $(cmd), globs, pipes, redirects."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Callable


class ParseError(Exception):
    pass


@dataclass
class Op:
    value: str


@dataclass
class Word:
    text: str
    glob: bool = False
    quoted: bool = False  # any quoting/escaping present (so it is never a reserved word)


@dataclass
class Simple:
    argv: list[Word] = field(default_factory=list)
    stdin: Word | None = None
    stdout: tuple[Word, bool] | None = None  # (target, append)
    stderr: tuple[Word, bool] | str | None = None  # file, or "out" for 2>&1


Pipeline = list[Simple]

_VAR = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def arith(expr: str, env: dict[str, str]) -> int:
    """Evaluate a $((...)) expression safely (integers, + - * / % **, comparisons)."""
    def ref(m: "re.Match[str]") -> str:
        name = m.group(1)
        if name.isdigit() or name == "#":  # positional parameters become literal numbers
            try:
                return str(int(env.get(name, "0") or 0, 0))
            except ValueError:
                return "0"
        return name

    expr = re.sub(r"\$\{?([A-Za-z_]\w*|\d+|#)\}?", ref, expr)
    try:
        tree = ast.parse(expr.strip() or "0", mode="eval")
    except SyntaxError:
        raise ParseError(f"{expr.strip()}: syntax error in expression")

    def ev(node) -> int:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and type(node.value) is int:
            return node.value
        if isinstance(node, ast.Name):
            try:
                return int(env.get(node.id, "0") or 0, 0)
            except ValueError:
                return 0
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd, ast.Not)):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else (int(not v) if isinstance(node.op, ast.Not) else v)
        if isinstance(node, ast.BinOp):
            a, b = ev(node.left), ev(node.right)
            op = node.op
            if isinstance(op, ast.Add):
                return a + b
            if isinstance(op, ast.Sub):
                return a - b
            if isinstance(op, ast.Mult):
                return a * b
            if isinstance(op, (ast.Div, ast.FloorDiv, ast.Mod)):
                if b == 0:
                    raise ParseError("division by 0 (error token is \"0\")")
                q = abs(a) // abs(b) * (1 if (a < 0) == (b < 0) else -1)
                return q if not isinstance(op, ast.Mod) else a - q * b
            if isinstance(op, ast.Pow) and 0 <= b <= 64 and abs(a) < 2**32:
                return a**b
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            a, b = ev(node.left), ev(node.comparators[0])
            o = node.ops[0]
            table = {ast.Lt: a < b, ast.LtE: a <= b, ast.Gt: a > b, ast.GtE: a >= b, ast.Eq: a == b, ast.NotEq: a != b}
            if type(o) in table:
                return int(table[type(o)])
        raise ParseError(f"{expr.strip()}: unsupported arithmetic expression")

    return ev(tree)


_RANGE_INT = re.compile(r"^(-?\d+)\.\.(-?\d+)(?:\.\.(-?\d+))?$")
_RANGE_CHAR = re.compile(r"^([A-Za-z])\.\.([A-Za-z])(?:\.\.(-?\d+))?$")


def _brace_items(inner: str) -> list[str] | None:
    """The expansion of a {...} body (range or comma list), or None if it isn't one."""
    m = _RANGE_INT.match(inner)
    if m:
        lo_s, hi_s, step_s = m.group(1), m.group(2), m.group(3)
        lo, hi = int(lo_s), int(hi_s)
        step = abs(int(step_s)) if step_s else 1
        step = step or 1
        rng = range(lo, hi + 1, step) if lo <= hi else range(lo, hi - 1, -step)
        pad = lo_s.lstrip("-").startswith("0") or hi_s.lstrip("-").startswith("0")
        width = max(len(lo_s.lstrip("-")), len(hi_s.lstrip("-"))) if pad else 0
        return [("-" if v < 0 else "") + str(abs(v)).zfill(width) for v in rng]
    m = _RANGE_CHAR.match(inner)
    if m:
        lo, hi = ord(m.group(1)), ord(m.group(2))
        step = abs(int(m.group(3))) if m.group(3) else 1
        step = step or 1
        rng = range(lo, hi + 1, step) if lo <= hi else range(lo, hi - 1, -step)
        return [chr(v) for v in rng]
    if "," in inner:
        items: list[str] = []
        cur: list[str] = []
        depth = 0
        for ch in inner:
            if ch == "{":
                depth += 1
                cur.append(ch)
            elif ch == "}":
                depth -= 1
                cur.append(ch)
            elif ch == "," and depth == 0:
                items.append("".join(cur))
                cur = []
            else:
                cur.append(ch)
        items.append("".join(cur))
        if len(items) > 1:
            return items
    return None


def _expand_braces(word: str) -> list[str]:
    """Expand the first unquoted {a,b}/{a..b} group in word, recursively (so pre{a,b}{1,2}suf works)."""
    i, n, depth, start = 0, len(word), 0, -1
    in_s = in_d = esc = False
    while i < n:
        c = word[i]
        if esc:
            esc = False
        elif c == "\\" and not in_s:
            esc = True
        elif in_s:
            if c == "'":
                in_s = False
        elif in_d:
            if c == '"':
                in_d = False
        elif c == "'":
            in_s = True
        elif c == '"':
            in_d = True
        elif c == "{" and not (i > 0 and word[i - 1] == "$"):
            if depth == 0:
                start = i
            depth += 1
        elif c == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                inner = word[start + 1 : i]
                items = _brace_items(inner)
                if items is not None:
                    prefix, suffix = word[:start], word[i + 1 :]
                    out: list[str] = []
                    for it in items:
                        out.extend(_expand_braces(prefix + it + suffix))
                    return out
                start = -1
        i += 1
    return [word]


def _raw_words(line: str) -> list[str]:
    """Split on unquoted whitespace, keeping quotes/escapes intact (brace expansion ignores them)."""
    words: list[str] = []
    buf: list[str] = []
    in_s = in_d = esc = False
    for c in line:
        if esc:
            buf.append(c)
            esc = False
        elif c == "\\" and not in_s:
            buf.append(c)
            esc = True
        elif in_s:
            buf.append(c)
            if c == "'":
                in_s = False
        elif in_d:
            buf.append(c)
            if c == '"':
                in_d = False
        elif c == "'":
            buf.append(c)
            in_s = True
        elif c == '"':
            buf.append(c)
            in_d = True
        elif c in " \t":
            if buf:
                words.append("".join(buf))
                buf = []
        else:
            buf.append(c)
    if buf:
        words.append("".join(buf))
    return words


def expand_braces_in_line(line: str) -> str:
    """Bash's brace expansion: {1..5}, {a..e}, {x,y,z} -- a purely textual pass, before $ and quotes resolve."""
    out: list[str] = []
    for w in _raw_words(line):
        out.extend(_expand_braces(w))
    return " ".join(out)


def tokenize(
    line: str,
    env: dict[str, str],
    status: int,
    subst: Callable[[str], str],
) -> list[Word | Op]:
    line = expand_braces_in_line(line)
    toks: list[Word | Op] = []
    buf: list[str] = []
    has = False
    glob = False
    quoted = False
    i, n = 0, len(line)

    def flush() -> None:
        nonlocal buf, has, glob, quoted
        if has:
            toks.append(Word("".join(buf), glob, quoted))
        buf, has, glob, quoted = [], False, False, False

    def add_split(t: str) -> None:
        """Unquoted expansion results are split on whitespace into separate words."""
        nonlocal has
        if re.match(r"[A-Za-z_][A-Za-z0-9_]*=", "".join(buf)):  # NAME=value: assignments never word-split
            buf.append(t)
            has = True
            return
        for seg in re.split(r"(\s+)", t):
            if not seg:
                continue
            if seg.isspace():
                flush()
            else:
                buf.append(seg)
                has = True

    def dollar(i: int) -> tuple[str, int]:
        if line.startswith("$(", i):
            depth, j = 1, i + 2
            while j < n and depth:
                depth += (line[j] == "(") - (line[j] == ")")
                j += 1
            if depth:
                raise ParseError("unexpected EOF while looking for matching `)'")
            inner = line[i + 2 : j - 1]
            if inner.startswith("(") and inner.endswith(")"):
                return str(arith(inner[1:-1], env)), j
            return subst(inner).rstrip("\n"), j
        if line.startswith("${", i):
            k = line.find("}", i)
            if k < 0:
                raise ParseError("bad substitution")
            return env.get(line[i + 2 : k], ""), k + 1
        if i + 1 < n and line[i + 1] == "?":
            return str(status), i + 2
        if i + 1 < n and (line[i + 1].isdigit() or line[i + 1] in "#@*"):
            return env.get(line[i + 1], ""), i + 2
        m = _VAR.match(line, i + 1)
        if m:
            return env.get(m.group(), ""), m.end()
        return "$", i + 1

    def backtick(i: int) -> tuple[str, int]:
        j = line.find("`", i + 1)
        if j < 0:
            raise ParseError("unexpected EOF while looking for matching ``'")
        return subst(line[i + 1 : j]).rstrip("\n"), j + 1

    while i < n:
        c = line[i]
        if c in " \t":
            flush()
            i += 1
        elif c == "#" and not has:
            break
        elif c == "'":
            j = line.find("'", i + 1)
            if j < 0:
                raise ParseError("unexpected EOF while looking for matching `''")
            buf.append(line[i + 1 : j])
            has = quoted = True
            i = j + 1
        elif c == '"':
            i += 1
            has = quoted = True
            while True:
                if i >= n:
                    raise ParseError('unexpected EOF while looking for matching `"\'')
                ch = line[i]
                if ch == '"':
                    i += 1
                    break
                if ch == "\\" and i + 1 < n and line[i + 1] in '$"\\`':
                    buf.append(line[i + 1])
                    i += 2
                elif ch == "$":
                    t, i = dollar(i)
                    buf.append(t)
                elif ch == "`":
                    t, i = backtick(i)
                    buf.append(t)
                else:
                    buf.append(ch)
                    i += 1
        elif c == "\\":
            if i + 1 < n:
                buf.append(line[i + 1])
                has = quoted = True
            i += 2
        elif c == "$":
            t, i = dollar(i)
            add_split(t)
        elif c == "`":
            t, i = backtick(i)
            add_split(t)
        elif c == "~" and not has and (i + 1 >= n or line[i + 1] in "/ \t;|&<>"):
            buf.append(env.get("HOME", "/"))
            has = True
            i += 1
        elif c in "*?[":
            glob = has = True
            buf.append(c)
            i += 1
        elif c in "|&;<>":
            if c == ">" and buf == ["2"] and has:
                buf, has = [], False
                if line.startswith(">&1", i):
                    toks.append(Op("2>&1"))
                    i += 3
                elif line.startswith(">>", i):
                    toks.append(Op("2>>"))
                    i += 2
                else:
                    toks.append(Op("2>"))
                    i += 1
                continue
            flush()
            two = line[i : i + 2]
            if two in ("||", "&&", ">>"):
                toks.append(Op(two))
                i += 2
            else:
                toks.append(Op(";" if c == "&" else c))
                i += 1
        else:
            buf.append(c)
            has = True
            i += 1
    flush()
    return toks


def parse(toks: list[Word | Op]) -> list[tuple[str, Pipeline]]:
    """Return [(connector_before, pipeline)] where connector is ';', '&&' or '||'."""
    items: list[tuple[str, Pipeline]] = []
    pipeline: Pipeline = []
    cur = Simple()
    connector = ";"
    i = 0

    def finish_simple(require: bool) -> None:
        nonlocal cur
        if not cur.argv and (cur.stdin or cur.stdout or cur.stderr):
            raise ParseError("syntax error: missing command")
        if not cur.argv:
            if require:
                raise ParseError("syntax error near unexpected token")
            return
        pipeline.append(cur)
        cur = Simple()

    while i < len(toks):
        t = toks[i]
        if isinstance(t, Word):
            cur.argv.append(t)
        elif t.value in ("<", ">", ">>", "2>", "2>>"):
            if i + 1 >= len(toks) or not isinstance(toks[i + 1], Word):
                raise ParseError("syntax error near unexpected token `newline'")
            target = toks[i + 1]
            assert isinstance(target, Word)
            if t.value == "<":
                cur.stdin = target
            elif t.value in (">", ">>"):
                cur.stdout = (target, t.value == ">>")
            else:
                cur.stderr = (target, t.value == "2>>")
            i += 1
        elif t.value == "2>&1":
            cur.stderr = "out"
        elif t.value == "|":
            finish_simple(True)
        else:  # ; && ||
            finish_simple(False)
            if pipeline:
                items.append((connector, list(pipeline)))
                pipeline.clear()
            elif t.value != ";":
                raise ParseError(f"syntax error near unexpected token `{t.value}'")
            connector = t.value
        i += 1
    finish_simple(bool(pipeline))
    if pipeline:
        items.append((connector, list(pipeline)))
    return items
