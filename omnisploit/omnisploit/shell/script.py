"""Structure parser for shell scripts: if/elif/else, for, while/until, lists.

This works on *raw text*. Simple commands are kept as strings and tokenized only
when executed, so `$i` inside a loop body expands freshly on every iteration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class Incomplete(Exception):
    """Input ended inside a quote or an unfinished compound command."""


class ScriptSyntaxError(Exception):
    pass


@dataclass
class Cmd:
    text: str
    background: bool = False


@dataclass
class If:
    branches: list[tuple["List", "List"]]
    otherwise: "List | None"


@dataclass
class For:
    var: str
    words: str | None  # raw text of the word list; None -> positional parameters
    body: "List"


@dataclass
class While:
    cond: "List"
    body: "List"
    until: bool = False


@dataclass
class Func:
    name: str
    body: "List"


@dataclass
class Group:
    body: "List"


@dataclass
class Case:
    word: str  # raw text
    clauses: list[tuple[list[str], "List"]]  # (raw patterns, body)


@dataclass
class Pipe:
    nodes: list["Node"]  # at least one stage is a compound command


@dataclass
class Redirected:
    node: "Node"
    redir: str  # raw redirection text, e.g. "< file" or "> out 2>&1"


Node = Cmd | If | For | While | Func | Group | Case | Pipe | Redirected
COMPOUND_START = {"if", "for", "while", "until", "case", "{"}
REDIR_WORD = re.compile(r"(?:\d?>>?|<)")
List = list[tuple[str, Node]]  # (connector_before, node); connector in ; && ||

RESERVED = {
    "if", "then", "elif", "else", "fi", "for", "in", "do", "done", "while", "until",
    "case", "esac", "function", "{", "}",
}
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")
FUNC_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*$")


# ------------------------------------------------------------------ lexer
def _skip_quote(text: str, i: int) -> int:
    q = text[i]
    j = i + 1
    while j < len(text):
        if q == '"' and text[j] == "\\":
            j += 2
            continue
        if q == '"' and text.startswith("$(", j):
            j = _skip_paren(text, j + 1)
            continue
        if text[j] == q:
            return j + 1
        j += 1
    raise Incomplete


def _skip_paren(text: str, i: int) -> int:
    """text[i] == '('; return the index just past the matching ')'."""
    depth, j = 0, i
    while j < len(text):
        c = text[j]
        if c in "'\"":
            j = _skip_quote(text, j)
            continue
        if c == "\\":
            j += 2
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    raise Incomplete


def lex(text: str) -> list[tuple[str, str]]:
    toks: list[tuple[str, str]] = []
    cur: list[str] = []
    i, n = 0, len(text)

    def flush() -> None:
        if cur:
            toks.append(("w", "".join(cur)))
            cur.clear()

    while i < n:
        c = text[i]
        if c in " \t":
            flush()
            i += 1
        elif c == "\n":
            flush()
            toks.append(("op", "nl"))
            i += 1
        elif c == "#" and not cur:
            while i < n and text[i] != "\n":
                i += 1
        elif c in "'\"":
            j = _skip_quote(text, i)
            cur.append(text[i:j])
            i = j
        elif c == "\\":
            if i + 1 < n and text[i + 1] == "\n":
                i += 2
            else:
                cur.append(text[i : i + 2])
                i += 2
        elif c == "$" and text.startswith("$(", i):
            j = _skip_paren(text, i + 1)
            cur.append(text[i:j])
            i = j
        elif c == "`":
            j = text.find("`", i + 1)
            if j < 0:
                raise Incomplete
            cur.append(text[i : j + 1])
            i = j + 1
        elif c == "&" and cur and cur[-1].endswith(">"):
            cur.append(c)  # 2>&1
            i += 1
        elif c in ";&|":
            flush()
            two = text[i : i + 2]
            if two in ("&&", "||"):
                toks.append(("op", two))
                i += 2
            elif two == ";;":
                toks.append(("op", ";;"))
                i += 2
            else:
                toks.append(("op", c))
                i += 1
        else:
            cur.append(c)
            i += 1
    flush()
    return toks


# ----------------------------------------------------------------- parser
class _Parser:
    def __init__(self, toks: list[tuple[str, str]]) -> None:
        self.toks = toks
        self.pos = 0

    def peek(self) -> tuple[str, str] | None:
        return self.toks[self.pos] if self.pos < len(self.toks) else None

    def skip_seps(self) -> None:
        while (t := self.peek()) and t[0] == "op" and t[1] in ("nl", ";"):
            self.pos += 1

    def expect_word(self, word: str) -> None:
        t = self.peek()
        if t is None:
            raise Incomplete
        if t != ("w", word):
            raise ScriptSyntaxError(f"syntax error near unexpected token `{t[1]}'")
        self.pos += 1

    def parse_list(self, stop: frozenset[str]) -> List:
        items: List = []
        connector = ";"
        while True:
            self.skip_seps()
            t = self.peek()
            if t is None:
                break
            if t[0] == "w" and t[1] in stop:
                break
            if t == ("op", ";;"):
                break
            if t[0] == "op":
                raise ScriptSyntaxError(f"syntax error near unexpected token `{'newline' if t[1] == 'nl' else t[1]}'")
            node = self.parse_pipeline()
            items.append((connector, node))
            t = self.peek()
            if t and t[0] == "op" and t[1] in ("&&", "||"):
                connector = t[1]
                self.pos += 1
                while (n := self.peek()) and n == ("op", "nl"):
                    self.pos += 1
                if self.peek() is None:
                    raise Incomplete
            else:
                connector = ";"
        return items

    def parse_pipeline(self) -> Node:
        nodes = [self.parse_command()]
        while self.peek() == ("op", "|"):
            self.pos += 1
            while self.peek() == ("op", "nl"):
                self.pos += 1
            if self.peek() is None:
                raise Incomplete
            nodes.append(self.parse_command())
        return nodes[0] if len(nodes) == 1 else Pipe(nodes)

    def parse_command(self) -> Node:
        node = self.parse_node()
        if isinstance(node, (Cmd, Func)):
            return node
        redir = self.parse_redirs()
        return Redirected(node, redir) if redir else node

    def parse_redirs(self) -> str:
        parts: list[str] = []
        while (t := self.peek()) and t[0] == "w" and t[1][:1] in "<>2" and re.match(r"(?:\d?>>?|<)", t[1]):
            word = t[1]
            self.pos += 1
            if REDIR_WORD.fullmatch(word):
                n = self.peek()
                if n is None:
                    raise Incomplete
                if n[0] != "w":
                    raise ScriptSyntaxError("syntax error near unexpected token `newline'")
                parts.append(f"{word} {n[1]}")
                self.pos += 1
            else:
                parts.append(word)
        return " ".join(parts)

    def parse_node(self) -> Node:
        t = self.peek()
        assert t and t[0] == "w"
        word = t[1]
        if word == "if":
            return self.parse_if()
        if word == "for":
            return self.parse_for()
        if word in ("while", "until"):
            return self.parse_while()
        if word == "case":
            return self.parse_case()
        if word == "{":
            self.pos += 1
            body = self.parse_list(frozenset({"}"}))
            self.expect_word("}")
            return Group(body)
        if word == "function":
            return self.parse_function(keyword=True)
        if self._is_function_def(word):
            return self.parse_function(keyword=False)
        if word in RESERVED:
            raise ScriptSyntaxError(f"syntax error near unexpected token `{word}'")
        parts: list[str] = []
        background = False
        while (t := self.peek()) is not None:
            if t[0] == "w":
                parts.append(t[1])
            elif t[1] == "|":
                j = self.pos + 1
                while j < len(self.toks) and self.toks[j] == ("op", "nl"):  # newline allowed after a pipe
                    j += 1
                if j >= len(self.toks):
                    raise Incomplete
                if self.toks[j][0] == "w" and self.toks[j][1] in COMPOUND_START:
                    break  # `cmd | while ...`: parse_pipeline continues with the compound
                parts.append("|")
                self.pos = j
                continue
            elif t[1] == "&":
                background = True
                self.pos += 1
                break
            else:
                break
            self.pos += 1
        return Cmd(" ".join(parts), background)

    def _is_function_def(self, word: str) -> bool:
        if word.endswith("()") and FUNC_NAME.match(word[:-2]):
            return True
        nxt = self.toks[self.pos + 1] if self.pos + 1 < len(self.toks) else None
        return bool(FUNC_NAME.match(word)) and nxt == ("w", "()") and word not in RESERVED

    def parse_function(self, keyword: bool) -> Func:
        t = self.toks[self.pos]
        self.pos += 1
        if keyword:
            n = self.peek()
            if n is None:
                raise Incomplete
            if n[0] != "w" or not FUNC_NAME.match(n[1].removesuffix("()")):
                raise ScriptSyntaxError(f"`{n[1]}': not a valid identifier")
            name = n[1].removesuffix("()")
            self.pos += 1
            if n[1].endswith("()") is False and self.peek() == ("w", "()"):
                self.pos += 1
        else:
            name = t[1].removesuffix("()")
            if not t[1].endswith("()"):
                self.pos += 1  # the separate "()" word
        self.skip_seps()
        self.expect_word("{")
        body = self.parse_list(frozenset({"}"}))
        self.expect_word("}")
        return Func(name, body)

    def parse_case(self) -> Case:
        self.pos += 1
        t = self.peek()
        if t is None:
            raise Incomplete
        if t[0] != "w":
            raise ScriptSyntaxError(f"syntax error near unexpected token `{t[1]}'")
        word = t[1]
        self.pos += 1
        self.skip_seps()
        self.expect_word("in")
        clauses: list[tuple[list[str], List]] = []
        while True:
            self.skip_seps()
            t = self.peek()
            if t is None:
                raise Incomplete
            if t == ("w", "esac"):
                self.pos += 1
                return Case(word, clauses)
            if t == ("w", "("):
                self.pos += 1
            patterns: list[str] = []
            while True:
                t = self.peek()
                if t is None:
                    raise Incomplete
                self.pos += 1
                if t == ("op", "|"):
                    continue
                if t[0] != "w":
                    raise ScriptSyntaxError(f"syntax error near unexpected token `{t[1]}'")
                if t[1].endswith(")"):
                    if t[1] != ")":
                        patterns.append(t[1][:-1])
                    break
                patterns.append(t[1])
            if not patterns:
                raise ScriptSyntaxError("syntax error near unexpected token `)'")
            body = self.parse_list(frozenset({"esac"}))
            if self.peek() == ("op", ";;"):
                self.pos += 1
            clauses.append((patterns, body))

    def parse_if(self) -> If:
        self.pos += 1
        branches = []
        cond = self.parse_list(frozenset({"then"}))
        if not cond:
            raise ScriptSyntaxError("syntax error near unexpected token `then'")
        self.expect_word("then")
        body = self.parse_list(frozenset({"elif", "else", "fi"}))
        branches.append((cond, body))
        otherwise = None
        while True:
            t = self.peek()
            if t is None:
                raise Incomplete
            if t == ("w", "elif"):
                self.pos += 1
                cond = self.parse_list(frozenset({"then"}))
                self.expect_word("then")
                branches.append((cond, self.parse_list(frozenset({"elif", "else", "fi"}))))
            elif t == ("w", "else"):
                self.pos += 1
                otherwise = self.parse_list(frozenset({"fi"}))
            else:
                break
        self.expect_word("fi")
        return If(branches, otherwise)

    def parse_for(self) -> For:
        self.pos += 1
        t = self.peek()
        if t is None:
            raise Incomplete
        if t[0] != "w" or not NAME.match(t[1]):
            raise ScriptSyntaxError(f"`{t[1]}': not a valid identifier")
        var = t[1]
        self.pos += 1
        words: str | None = None
        if self.peek() == ("w", "in"):
            self.pos += 1
            parts = []
            while (t := self.peek()) and t[0] == "w":
                parts.append(t[1])
                self.pos += 1
            words = " ".join(parts)
        self.skip_seps()
        self.expect_word("do")
        body = self.parse_list(frozenset({"done"}))
        self.expect_word("done")
        return For(var, words, body)

    def parse_while(self) -> While:
        until = self.toks[self.pos][1] == "until"
        self.pos += 1
        cond = self.parse_list(frozenset({"do"}))
        if not cond:
            raise ScriptSyntaxError("syntax error near unexpected token `do'")
        self.expect_word("do")
        body = self.parse_list(frozenset({"done"}))
        self.expect_word("done")
        return While(cond, body, until)


def parse_script(text: str) -> List:
    """Parse script text. Raises Incomplete (needs more input) or ScriptSyntaxError."""
    p = _Parser(lex(text))
    items = p.parse_list(frozenset())
    t = p.peek()
    if t is not None:  # a stray reserved word such as `fi` or `done`
        raise ScriptSyntaxError(f"syntax error near unexpected token `{t[1]}'")
    return items


# --------------------------------------------------------------- unparse
def _indent(text: str, n: int = 1) -> str:
    pad = "    " * n
    return "\n".join(pad + l if l else l for l in text.split("\n"))


def unparse(items: List) -> str:
    """Turn a parsed list back into script text (used to save shell functions)."""
    lines: list[str] = []
    for connector, node in items:
        text = _unparse_node(node)
        if connector == ";" or not lines:
            lines.append(text)
        else:
            lines[-1] += f" {connector} {text}"
    return "\n".join(lines)


def _unparse_node(node: Node) -> str:
    if isinstance(node, Cmd):
        return node.text + (" &" if node.background else "")
    if isinstance(node, If):
        out = []
        for i, (cond, body) in enumerate(node.branches):
            out.append(f"{'if' if i == 0 else 'elif'} {unparse(cond)}; then")
            out.append(_indent(unparse(body)))
        if node.otherwise is not None:
            out.append("else")
            out.append(_indent(unparse(node.otherwise)))
        out.append("fi")
        return "\n".join(out)
    if isinstance(node, For):
        head = f"for {node.var} in {node.words}; do" if node.words is not None else f"for {node.var}; do"
        return f"{head}\n{_indent(unparse(node.body))}\ndone"
    if isinstance(node, While):
        return f"{'until' if node.until else 'while'} {unparse(node.cond)}; do\n{_indent(unparse(node.body))}\ndone"
    if isinstance(node, Func):
        return f"{node.name}() {{\n{_indent(unparse(node.body))}\n}}"
    if isinstance(node, Group):
        return f"{{\n{_indent(unparse(node.body))}\n}}"
    if isinstance(node, Pipe):
        return " | ".join(_unparse_node(n) for n in node.nodes)
    if isinstance(node, Redirected):
        return f"{_unparse_node(node.node)} {node.redir}"
    if isinstance(node, Case):
        out = [f"case {node.word} in"]
        for patterns, body in node.clauses:
            out.append(_indent("|".join(patterns) + ")"))
            out.append(_indent(unparse(body), 2))
            out.append(_indent(";;", 2))
        out.append("esac")
        return "\n".join(out)
    raise AssertionError(node)
