"""Tiny syntax highlighters (Python and shell). Pure functions, no curses.

``highlight(lines, kind)`` returns, for every line, a list of ``(start, end, style)``
spans with style in: kw, str, com, num, def, bi, var.
"""

from __future__ import annotations

import keyword
import re

Span = tuple[int, int, str]

PY_KEYWORDS = set(keyword.kwlist) | {"self", "cls"}
PY_BUILTINS = {
    "print", "len", "range", "int", "str", "bytes", "bytearray", "list", "dict", "set", "tuple", "open",
    "isinstance", "enumerate", "zip", "map", "filter", "sum", "min", "max", "sorted", "reversed", "abs",
    "bool", "float", "type", "hex", "ord", "chr", "input", "super", "any", "all", "bin", "oct", "repr",
    "format", "iter", "next", "getattr", "setattr", "hasattr", "Exception", "ValueError", "KeyError",
}
SH_KEYWORDS = {"if", "then", "elif", "else", "fi", "for", "in", "do", "done", "while", "until", "break", "continue"}

_PY_STRING = re.compile(r"(?i:(?:rb|br|rf|fr|r|b|f|u))?(\"\"\"|'''|\"|')")
_NUMBER = re.compile(r"0[xX][0-9a-fA-F_]+|0[bB][01_]+|\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_VAR = re.compile(r"\$(?:\{[^}]*\}|\(\(|[A-Za-z_][A-Za-z0-9_]*|[0-9#@*?$])")


def kind_for(name: str, first_line: str = "") -> str | None:
    if name.endswith(".py") or first_line.startswith("#!") and "python" in first_line:
        return "python"
    if name.endswith((".sh", ".bash")) or first_line.startswith("#!") and ("sh" in first_line):
        return "sh"
    return None


def _scan_string(line: str, i: int, quote: str) -> int:
    """Index just past the closing quote on this line, or len(line) if it does not close."""
    j = i
    while j < len(line):
        if line[j] == "\\":
            j += 2
            continue
        if line.startswith(quote, j):
            return j + len(quote)
        j += 1
    return len(line)


def _closes(line: str, quote: str, start: int = 0) -> int:
    j = start
    while j < len(line):
        if line[j] == "\\":
            j += 2
            continue
        if line.startswith(quote, j):
            return j + len(quote)
        j += 1
    return -1


def _python(lines: list[str]) -> list[list[Span]]:
    out: list[list[Span]] = []
    triple: str | None = None
    for line in lines:
        spans: list[Span] = []
        i, n = 0, len(line)
        if triple:
            end = _closes(line, triple)
            if end < 0:
                out.append([(0, n, "str")] if n else [])
                continue
            spans.append((0, end, "str"))
            i, triple = end, None
        prev_word = ""
        while i < n:
            c = line[i]
            if c == "#":
                spans.append((i, n, "com"))
                break
            m = _PY_STRING.match(line, i)
            if m:
                quote = m.group(1)
                if len(quote) == 3:
                    end = _closes(line, quote, m.end())
                    if end < 0:
                        spans.append((i, n, "str"))
                        triple = quote
                        break
                else:
                    end = _scan_string(line, m.end(), quote)
                spans.append((i, end, "str"))
                i = end
                continue
            m = _NUMBER.match(line, i) if c.isdigit() else None
            if m:
                spans.append((i, m.end(), "num"))
                i = m.end()
                continue
            m = _IDENT.match(line, i)
            if m:
                word = m.group()
                if prev_word in ("def", "class") and word not in PY_KEYWORDS:
                    spans.append((i, m.end(), "def"))
                elif word in PY_KEYWORDS:
                    spans.append((i, m.end(), "kw"))
                elif word in PY_BUILTINS:
                    spans.append((i, m.end(), "bi"))
                prev_word = word
                i = m.end()
                continue
            if c == "@" and line[:i].strip() == "":
                m = re.compile(r"@[\w.]+").match(line, i)
                if m:
                    spans.append((i, m.end(), "def"))
                    i = m.end()
                    continue
            if not c.isspace():
                prev_word = ""
            i += 1
        out.append(spans)
    return out


def _shell(lines: list[str]) -> list[list[Span]]:
    out: list[list[Span]] = []
    for line in lines:
        spans: list[Span] = []
        i, n = 0, len(line)
        at_word_start = True
        while i < n:
            c = line[i]
            if c == "#" and at_word_start:
                spans.append((i, n, "com"))
                break
            if c in "'\"":
                end = _scan_string(line, i + 1, c)
                spans.append((i, end, "str"))
                i = end
                at_word_start = False
                continue
            m = _VAR.match(line, i) if c == "$" else None
            if m:
                spans.append((i, m.end(), "var"))
                i = m.end()
                at_word_start = False
                continue
            m = _IDENT.match(line, i)
            if m:
                if m.group() in SH_KEYWORDS and at_word_start:
                    spans.append((i, m.end(), "kw"))
                i = m.end()
                at_word_start = False
                continue
            at_word_start = c in " \t;|&("
            i += 1
        out.append(spans)
    return out


def highlight(lines: list[str], kind: str | None) -> list[list[Span]]:
    if kind == "python":
        return _python(lines)
    if kind == "sh":
        return _shell(lines)
    return [[] for _ in lines]
