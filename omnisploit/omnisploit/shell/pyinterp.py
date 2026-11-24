"""A sandboxed Python-subset interpreter for the simulated shell.

Scripts are parsed with ``ast.parse`` (parsing never runs code) and then *walked* by
this module; ``exec``/``eval``/``compile`` are never used on player code. Only a
whitelist of builtins, modules, types and methods is reachable, dunder names are
refused, ``str.format`` is not offered (f-strings and ``%`` are), and step/size
limits stop runaway scripts. Files are the simulated VFS, never the host's.

Not supported: classes, generators/yield, async, decorators, ``global`` across
nested functions beyond the module level, ``match``, ``with`` on anything but files.
"""

from __future__ import annotations

import ast
import builtins
import codeop
import math
import re
import operator
import struct as _struct

from ..engine.vfs import VFSError
from .commands import command
from .shell import Result, Session, Shell, ok

STEP_LIMIT = 400_000
SIZE_LIMIT = 1_000_000
OUTPUT_LIMIT = 1_000_000
DEPTH_LIMIT = 40


class _Flow(BaseException):
    """Control flow and sandbox signals; BaseException so `except Exception` cannot swallow them."""


class _Break(_Flow):
    pass


class _Continue(_Flow):
    pass


class _Return(_Flow):
    def __init__(self, value) -> None:
        self.value = value


class _Exit(_Flow):
    def __init__(self, code) -> None:
        self.code = code


class _Limit(_Flow):
    pass


class _HelpPrompt(_Flow):
    """``help()`` with no arguments: :class:`PythonSession` switches to a one-topic-per-line sub-prompt."""


class Unsupported(Exception):
    pass


class _Sentinel:
    """The bare `exit`/`quit`/`help` names: a friendly one-line hint when referenced, a real call when called."""

    def __init__(self, text: str, action) -> None:
        self._text, self._action = text, action

    def __repr__(self) -> str:
        return self._text

    def __call__(self, *args, **kwargs):
        return self._action(*args, **kwargs)


EXCEPTIONS = {n: getattr(builtins, n)
              for n in ("Exception", "ValueError", "TypeError", "IndexError", "KeyError", "ZeroDivisionError",
                        "AttributeError", "NameError", "OverflowError", "StopIteration", "AssertionError",
                        "RuntimeError", "OSError", "FileNotFoundError", "PermissionError", "ArithmeticError",
                        "LookupError", "UnicodeDecodeError", "UnicodeEncodeError")}

ALLOWED_METHODS: dict[type, frozenset[str]] = {
    str: frozenset("join split rsplit strip lstrip rstrip upper lower replace startswith endswith find rfind index count "
                   "encode isdigit isalpha isalnum isspace isupper islower zfill ljust rjust center title capitalize "
                   "splitlines partition rpartition removeprefix removesuffix".split()),
    bytes: frozenset("decode hex join split rsplit strip lstrip rstrip replace startswith endswith find rfind index count "
                     "ljust rjust center upper lower isdigit isalpha partition splitlines zfill".split()),
    bytearray: frozenset("decode hex append extend pop find index count replace startswith endswith join".split()),
    list: frozenset("append extend pop insert remove index count sort reverse copy clear".split()),
    dict: frozenset("get keys values items pop update setdefault copy clear".split()),
    set: frozenset("add remove discard update union intersection difference copy clear issubset".split()),
    tuple: frozenset("count index".split()),
    int: frozenset("bit_length to_bytes".split()),
    float: frozenset("is_integer".split()),
}
WIDTH_RE = re.compile(r"\d{7,}")
SIZED_METHODS = {"zfill", "ljust", "rjust", "center"}
TYPE_ATTRS = {int: {"from_bytes"}, bytes: {"fromhex"}, dict: {"fromkeys"}}


class Scope:
    def __init__(self, parent: "Scope | None" = None) -> None:
        self.vars: dict[str, object] = {}
        self.parent = parent
        self.globals: set[str] = set()

    def root(self) -> "Scope":
        s = self
        while s.parent is not None:
            s = s.parent
        return s

    def lookup(self, name: str):
        s: Scope | None = self
        while s is not None:
            if name in s.vars:
                return s.vars[name]
            s = s.parent
        raise KeyError(name)

    def assign(self, name: str, value) -> None:
        (self.root() if name in self.globals else self).vars[name] = value


class Module:
    def __init__(self, name: str, attrs: dict[str, object]) -> None:
        self.name, self.attrs = name, attrs


class SimFile:
    def __init__(self, interp: "Interp", path: str, mode: str) -> None:
        self.interp, self.path, self.mode = interp, path, mode
        self.binary = "b" in mode
        self.writing = any(c in mode for c in "wax")
        self.buf = bytearray()
        self.closed = False
        sh = interp.shell
        if "r" in mode:
            data = sh.vfs.read(path, sh.cwd)
            self.data = data
            self.pos = 0
        else:
            self.data, self.pos = b"", 0
            sh.vfs.write(path, b"" if "w" in mode or "x" in mode else sh.vfs.read(path, sh.cwd) if sh.vfs.exists(path, sh.cwd) else b"", sh.cwd)

    def _wrap(self, raw: bytes):
        return raw if self.binary else raw.decode("utf-8", errors="replace")

    def read(self, n: int = -1):
        end = len(self.data) if n is None or n < 0 else min(len(self.data), self.pos + n)
        chunk, self.pos = self.data[self.pos : end], end
        return self._wrap(chunk)

    def readline(self):
        i = self.data.find(b"\n", self.pos)
        end = len(self.data) if i < 0 else i + 1
        chunk, self.pos = self.data[self.pos : end], end
        return self._wrap(chunk)

    def readlines(self):
        out = []
        while self.pos < len(self.data):
            out.append(self.readline())
        return out

    def __iter__(self):
        return iter(self.readlines())

    def write(self, text):
        if not self.writing:
            raise OSError("not writable")
        raw = text if isinstance(text, (bytes, bytearray)) else str(text).encode()
        self.interp.shell.vfs.write(self.path, bytes(raw), self.interp.shell.cwd, append=True)
        return len(raw)

    def close(self):
        self.closed = True


class Stream:
    """sys.stdout / sys.stderr / sys.stdin."""

    def __init__(self, interp: "Interp", kind: str) -> None:
        self.interp, self.kind = interp, kind

    def write(self, text):
        self.interp.emit(str(text), self.kind == "err")
        return len(str(text))

    def flush(self):
        return None

    def read(self, n: int = -1):
        data, self.interp.stdin = self.interp.stdin, b""
        return data.decode(errors="replace")

    def readline(self):
        return self.interp.read_line() or ""

    def readlines(self):
        out = []
        while (ln := self.interp.read_line()) is not None:
            out.append(ln)
        return out

    def __iter__(self):
        return iter(self.readlines())


class Func:
    def __init__(self, interp: "Interp", name: str, args: ast.arguments, body, scope: Scope, is_lambda: bool = False) -> None:
        self.interp, self.name, self.args, self.body, self.scope, self.is_lambda = interp, name, args, body, scope, is_lambda
        self.defaults = [interp.eval(d, scope) for d in args.defaults]
        self.kw_defaults = {a.arg: (interp.eval(d, scope) if d is not None else None) for a, d in zip(args.kwonlyargs, args.kw_defaults)}
        first = body[0] if (not is_lambda and body) else None  # a lambda's body is a bare expression, not a statement list
        self.__doc__ = (
            first.value.value
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str)
            else None
        )

    def __call__(self, *args, **kwargs):
        it = self.interp
        it.depth += 1
        if it.depth > DEPTH_LIMIT:
            it.depth -= 1
            raise RecursionError("maximum recursion depth exceeded")
        try:
            sc = Scope(self.scope)
            a = self.args
            params = [p.arg for p in a.posonlyargs + a.args]
            vals = list(args)
            for i, p in enumerate(params):
                if i < len(vals):
                    if p in kwargs:
                        raise TypeError(f"{self.name}() got multiple values for argument '{p}'")
                    sc.vars[p] = vals[i]
                elif p in kwargs:
                    sc.vars[p] = kwargs.pop(p)
                else:
                    di = i - (len(params) - len(self.defaults))
                    if di < 0:
                        raise TypeError(f"{self.name}() missing required positional argument: '{p}'")
                    sc.vars[p] = self.defaults[di]
            extra = vals[len(params):]
            if a.vararg:
                sc.vars[a.vararg.arg] = tuple(extra)
            elif extra:
                raise TypeError(f"{self.name}() takes {len(params)} positional arguments but {len(vals)} were given")
            for k in a.kwonlyargs:
                if k.arg in kwargs:
                    sc.vars[k.arg] = kwargs.pop(k.arg)
                elif self.kw_defaults.get(k.arg) is not None:
                    sc.vars[k.arg] = self.kw_defaults[k.arg]
                else:
                    raise TypeError(f"{self.name}() missing required keyword-only argument: '{k.arg}'")
            if a.kwarg:
                sc.vars[a.kwarg.arg] = dict(kwargs)
            elif kwargs:
                raise TypeError(f"{self.name}() got an unexpected keyword argument '{next(iter(kwargs))}'")
            if self.is_lambda:
                return it.eval(self.body, sc)
            try:
                it.run_block(self.body, sc)
            except _Return as r:
                return r.value
            return None
        finally:
            it.depth -= 1

    def __repr__(self) -> str:
        return f"<function {self.name}>"

    @property
    def __name__(self) -> str:
        return self.name


BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow, ast.LShift: operator.lshift,
    ast.RShift: operator.rshift, ast.BitOr: operator.or_, ast.BitXor: operator.xor, ast.BitAnd: operator.and_,
}
CMPOPS = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt,
    ast.GtE: operator.ge, ast.Is: operator.is_, ast.IsNot: operator.is_not,
    ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b,
}


KEYWORD_HELP = {
    "if": "if CONDITION:\n    ...\nelif CONDITION:\n    ...\nelse:\n    ...\n\nRuns the first branch whose condition is true.",
    "for": "for TARGET in ITERABLE:\n    ...\nelse:\n    ...\n\nRuns the body once per item; else runs unless 'break' was used.",
    "while": "while CONDITION:\n    ...\nelse:\n    ...\n\nRepeats the body while CONDITION is true.",
    "break": "Exit the innermost enclosing for/while loop immediately.",
    "continue": "Skip to the next iteration of the innermost enclosing for/while loop.",
    "pass": "Does nothing; a placeholder where a statement is required.",
    "def": "def NAME(ARGS):\n    ...\n\nDefines a function (defaults, *args and **kwargs work). See 'lambda' for a one-expression form.",
    "lambda": "lambda ARGS: EXPR\n\nA one-expression anonymous function.",
    "return": "return [EXPR]\n\nExit the current function, optionally with a value.",
    "global": "global NAME\n\nInside a function, makes assignments to NAME affect the module-level variable instead of a local one.",
    "import": "import MODULE  or  from MODULE import NAME[, ...]\n\nOnly 'sys', 'math', 'struct' and 'pwn' exist here (try \"modules\").",
    "try": "try:\n    ...\nexcept EXC as e:\n    ...\nelse:\n    ...\nfinally:\n    ...\n\nCatches exceptions raised in the try block.",
    "except": "See 'try'.",
    "raise": "raise EXC  or  raise EXC(msg)\n\nRaises an exception; with no argument, re-raises the one being handled.",
    "assert": "assert CONDITION[, MESSAGE]\n\nRaises AssertionError(MESSAGE) if CONDITION is false.",
    "with": "with EXPR as NAME:\n    ...\n\nIn this simulation EXPR must be an open() file; it is closed automatically.",
    "del": "del NAME  or  del SEQUENCE[INDEX]\n\nRemoves a variable, or an item from a list/dict.",
    "and": "A and B: B if A is true, else A (short-circuits).",
    "or": "A or B: A if A is true, else B (short-circuits).",
    "not": "not A: the boolean opposite of A.",
    "in": "A in B: whether A is a member of B (a string, bytes, list, tuple, dict or set).",
    "is": "A is B: whether A and B are the same object (use == to compare values instead).",
    "true": "The boolean true value, written True.",
    "false": "The boolean false value, written False.",
    "none": "The absence of a value, written None.",
    "elif": "See 'if'.",
    "else": "See 'if', 'for' and 'while'.",
    "finally": "See 'try'.",
    "from": "See 'import'.",
    "as": "See 'try', 'with' and 'import'.",
    "class": "Not supported in this simulation: there are no user-defined classes, only the builtin types.",
    "yield": "Not supported in this simulation: generator functions do not exist.",
    "async": "Not supported in this simulation.",
    "await": "Not supported in this simulation.",
    "match": "Not supported in this simulation: there is no match/case statement.",
}
MODULE_HELP = {
    "sys": "sys -- argv, exit(code), stdin, stdout, stderr, version, platform, maxsize.",
    "math": "math -- sqrt, floor, ceil, log, log2, log10, sin, cos, tan, gcd, factorial, isqrt, pi, e, inf.",
    "struct": "struct -- pack(fmt, *values), unpack(fmt, data), calcsize(fmt).",
    "pwn": "pwn -- p8/p16/p32/p64 and u8..u64, cyclic, cyclic_find, enhex, unhex, hexdump, flat, context, log,\n"
           "       process(path) (a tube over the simulated program), ELF(path) (read-only: symbols, mitigations),\n"
           "       and ROP(elf) (a small chain builder limited to this simulation's own gadgets). See 'man python3'.",
}
HELP_BANNER = (
    "Welcome to Python 3.10's help utility!\n\n"
    "Enter the name of a keyword, module or builtin to get help on it (try \"keywords\" or \"modules\"\n"
    "for a list of what this simulation actually has). Enter a blank line or \"quit\" to leave.\n\n"
)


class Interp:
    def __init__(self, shell: Shell, argv: list[str], stdin: bytes, filename: str = "<stdin>") -> None:
        self.shell = shell
        self.stdin = stdin
        self.filename = filename
        self.out: list[str] = []
        self.err: list[str] = []
        self.out_size = 0
        self.steps = 0
        self.depth = 0
        self.interactive = False
        self.exited = False
        self.glob = Scope()
        self.sys = Module("sys", {
            "argv": list(argv), "stdout": Stream(self, "out"), "stderr": Stream(self, "err"), "stdin": Stream(self, "in"),
            "exit": self._exit, "version": "3.10.12", "platform": "linux", "maxsize": 2**63 - 1,
        })
        self.modules: dict[str, Module] = {
            "sys": self.sys,
            "math": Module("math", {k: getattr(math, k) for k in (
                "sqrt", "floor", "ceil", "pi", "e", "log", "log2", "log10", "sin", "cos", "tan", "gcd", "factorial", "inf", "isqrt")}),
            "struct": Module("struct", {"pack": self._pack, "unpack": _struct.unpack, "calcsize": _struct.calcsize}),
        }
        from .pwnmod import build_pwn  # local import: pwnmod needs Module from this file

        self.modules["pwn"] = build_pwn(self)
        self.builtins = self._make_builtins()

    # ------------------------------------------------------------ plumbing
    def emit(self, text: str, err: bool = False) -> None:
        self.out_size += len(text)
        if self.out_size > OUTPUT_LIMIT:
            raise _Limit("output limit reached")
        (self.err if err else self.out).append(text)

    def read_line(self) -> str | None:
        if not self.stdin:
            return None
        i = self.stdin.find(b"\n")
        end = len(self.stdin) if i < 0 else i + 1
        line, self.stdin = self.stdin[:end], self.stdin[end:]
        return line.decode(errors="replace")

    @staticmethod
    def _exit(code=0):
        raise _Exit(code)

    @staticmethod
    def _pack(fmt, *vals):
        return _struct.pack(fmt, *vals)

    def _make_builtins(self) -> dict[str, object]:
        def _print(*args, sep=" ", end="\n", file=None, flush=False):
            """Print values, separated by sep (default a space) and followed by end (default a newline)."""
            text = sep.join(str(a) for a in args) + end
            if isinstance(file, Stream):
                file.write(text)
            else:
                self.emit(text)

        def _input(prompt=""):
            """Print prompt, then read and return one line of standard input (without its newline)."""
            self.emit(str(prompt))
            line = self.read_line()
            if line is None:
                raise EOFError("EOF when reading a line")
            return line.rstrip("\n")

        def _open(path, mode="r", *a, **k):
            """Open a file in the simulated filesystem; mode is 'r', 'w', 'a', 'x', optionally with 'b' or '+'."""
            if not isinstance(path, str) or any(c not in "rwabx+t" for c in mode):
                raise ValueError(f"invalid mode: '{mode}'")
            try:
                return SimFile(self, path, mode)
            except VFSError as e:
                raise (FileNotFoundError if "No such" in str(e) else PermissionError if "ermission" in str(e) else OSError)(str(e))

        def _range(*a):
            """range(stop) or range(start, stop[, step]): a lazy sequence of integers."""
            r = range(*a)
            if len(r) > SIZE_LIMIT:
                raise MemoryError("range too large")
            return r

        def _repr(x):
            """Return a printable, unambiguous string representation of x."""
            return repr(x)

        def _help(*args):
            """help(): open the interactive help prompt. help(topic) or help(object): one-shot help on that."""
            if args:
                topic = args[0]
                self.emit((self._help_topic(topic) if isinstance(topic, str) else self._describe(topic)) + "\n")
                return None
            self.emit(HELP_BANNER)
            if self.interactive:
                raise _HelpPrompt()  # the live >>> prompt: PythonSession takes over one line at a time
            while True:  # -c / a script / piped stdin: the rest of stdin supplies the topics, synchronously
                topic = self.read_line()
                if topic is None or not topic.strip() or topic.strip().lower() in ("quit", "q"):
                    self.emit("\n")
                    return None
                self.emit(self._help_topic(topic.strip()) + "\n\n")

        def _dir(*args):
            """dir() lists the names in the current scope; dir(object) lists the attributes you can use on that object."""
            if len(args) != 1:
                raise TypeError(f"dir expected at most 1 argument, got {len(args)}")
            return self._dir_of(args[0])

        exit_sentinel = _Sentinel("Use exit() or Ctrl-D (i.e. EOF) to exit", self._exit)
        help_sentinel = _Sentinel("Type help() for interactive help, or help(object) for help about object.", _help)

        b: dict[str, object] = {
            "print": _print, "dir": _dir, "input": _input, "open": _open, "range": _range, "repr": _repr, "help": help_sentinel,
            "len": len, "int": int, "str": str, "bytes": bytes, "bytearray": bytearray, "list": list, "dict": dict,
            "set": set, "tuple": tuple, "bool": bool, "float": float, "abs": abs, "min": min, "max": max, "sum": sum,
            "sorted": sorted, "reversed": reversed, "enumerate": enumerate, "zip": zip, "map": map, "filter": filter,
            "any": any, "all": all, "hex": hex, "oct": oct, "bin": bin, "chr": chr, "ord": ord, "round": round,
            "divmod": divmod, "pow": self._safe_pow, "isinstance": isinstance, "format": self._format, "iter": iter, "next": next,
            "exit": exit_sentinel, "quit": exit_sentinel, "True": True, "False": False, "None": None,
        }
        for n, cls in EXCEPTIONS.items():
            b[n] = cls
        return b

    # -------------------------------------------------------------- dir()
    def _dir_of(self, obj) -> list[str]:
        """Only names the sandbox would actually let the player reach (never dunders)."""
        from .pwnmod import Context, Elf, Rop, Tube

        if isinstance(obj, Module):
            return sorted(n for n in obj.attrs if not n.startswith("_"))
        if isinstance(obj, (Context, Elf, Rop, Tube)):
            return sorted(n for n in dir(type(obj)) if not n.startswith("_"))
        if isinstance(obj, (SimFile, Stream)):
            return sorted(("read", "readline", "readlines", "write", "close", "flush"))
        if isinstance(obj, type):
            return sorted(TYPE_ATTRS.get(obj, ()))
        return sorted(ALLOWED_METHODS.get(type(obj), ()))

    # -------------------------------------------------------------- help()
    def _describe(self, obj) -> str:
        if isinstance(obj, Module):
            return f"Help on module {obj.name}:\n\n" + (MODULE_HELP.get(obj.name) or ("members: " + ", ".join(sorted(obj.attrs))))
        name = getattr(obj, "__name__", None) or type(obj).__name__
        doc = (getattr(obj, "__doc__", None) or "").strip()
        kind = "class" if isinstance(obj, type) else "function" if callable(obj) else "value"
        head = f"Help on {kind} {name}" + (":" if kind != "value" else f" (a {type(obj).__name__}):")
        return f"{head}\n\n{doc}" if doc else f"{head}\n\nNo documentation available."

    def _help_topic(self, topic: str) -> str:
        topic = topic.strip()
        if not topic:
            return ""
        if topic == "keywords":
            cased = {"true": "True", "false": "False", "none": "None"}
            names = sorted(cased.get(k, k) for k in KEYWORD_HELP)
            return "Keywords supported in this simulation:\n  " + " ".join(names)
        if topic == "modules":
            return "Modules available: " + ", ".join(sorted(self.modules))
        low = topic.lower()
        if low in KEYWORD_HELP:
            return f"Keyword: {topic}\n\n{KEYWORD_HELP[low]}"
        if topic in self.modules:
            return self._describe(self.modules[topic])
        for source in (self.glob.vars, self.builtins, *(m.attrs for m in self.modules.values())):
            if topic in source:
                return self._describe(source[topic])
        return f"no documentation found for '{topic}'"

    @staticmethod
    def _format(value, spec=""):
        if WIDTH_RE.search(str(spec)):
            raise MemoryError("format width too large")
        return format(value, spec)

    @staticmethod
    def _safe_pow(a, b, m=None):
        if m is None and isinstance(b, int) and b > 20000 and isinstance(a, int) and abs(a) > 1:
            raise OverflowError("exponent too large")
        return pow(a, b, m) if m is not None else pow(a, b)

    # ------------------------------------------------------------- running
    def run_source(self, source: str, filename: str, interactive: bool = False) -> int:
        """Run source; returns the exit status. Output accumulates in ``self.out`` / ``self.err``."""
        self.filename = filename
        self.interactive = interactive
        try:
            tree = ast.parse(source, filename, mode="single" if interactive else "exec")
        except SyntaxError as e:
            line = (e.text or "").rstrip("\n")
            self.emit(f'  File "{filename}", line {e.lineno}\n    {line.strip()}\n{type(e).__name__}: {e.msg}\n', True)
            return 1
        except (ValueError, RecursionError, MemoryError) as e:
            self.emit(f"SyntaxError: {e}\n", True)
            return 1
        try:
            self.run_block(tree.body, self.glob)
            return 0
        except _Exit as e:
            self.exited = True
            code = e.code
            if code is None or code == 0:
                return 0
            if isinstance(code, int):
                return code & 0xFF
            self.emit(f"{code}\n", True)
            return 1
        except _Limit as e:
            self.emit(f"\n{e}\n", True)
            return 1
        except _HelpPrompt:
            raise  # help(): let PythonSession switch to its sub-prompt instead of reporting an error
        except _Flow:
            self.emit("SyntaxError: 'break', 'continue' or 'return' outside a loop or function\n", True)
            return 1
        except Unsupported as e:
            self.emit(f"NotImplementedError: this Python subset does not support {e}\n", True)
            return 1
        except Exception as e:  # report as a Python traceback
            line = getattr(e, "_omni_line", None)
            where = f'  File "{filename}", line {line}, in <module>\n' if line else ""
            self.emit(f"Traceback (most recent call last):\n{where}{type(e).__name__}: {e}\n", True)
            return 1

    def run_block(self, body, scope: Scope) -> None:
        for stmt in body:
            self.exec_stmt(stmt, scope)

    def _tick(self) -> None:
        self.steps += 1
        if self.steps > STEP_LIMIT:
            raise _Limit("Execution limit reached (runaway loop?)")

    # ---------------------------------------------------------- statements
    def exec_stmt(self, node, sc: Scope) -> None:
        self._tick()
        try:
            self._exec(node, sc)
        except Exception as e:
            if not hasattr(e, "_omni_line"):
                try:
                    e._omni_line = node.lineno  # type: ignore[attr-defined]
                except AttributeError:
                    pass
            raise

    def _exec(self, node, sc: Scope) -> None:
        t = type(node)
        if t is ast.Expr:
            v = self.eval(node.value, sc)
            if self.interactive and sc is self.glob and v is not None:
                self.emit(repr(v) + "\n")
        elif t is ast.Assign:
            v = self.eval(node.value, sc)
            for tgt in node.targets:
                self.assign(tgt, v, sc)
        elif t is ast.AugAssign:
            cur = self.eval(self._as_load(node.target), sc)
            self.assign(node.target, self.binop(node.op, cur, self.eval(node.value, sc)), sc)
        elif t is ast.AnnAssign:
            if node.value is not None:
                self.assign(node.target, self.eval(node.value, sc), sc)
        elif t is ast.If:
            self.run_block(node.body if self.eval(node.test, sc) else node.orelse, sc)
        elif t is ast.For:
            self._for(node, sc)
        elif t is ast.While:
            while self.eval(node.test, sc):
                self._tick()
                try:
                    self.run_block(node.body, sc)
                except _Break:
                    return
                except _Continue:
                    continue
            self.run_block(node.orelse, sc)
        elif t is ast.Break:
            raise _Break()
        elif t is ast.Continue:
            raise _Continue()
        elif t is ast.Pass:
            pass
        elif t is ast.Return:
            raise _Return(self.eval(node.value, sc) if node.value else None)
        elif t is ast.FunctionDef:
            if node.decorator_list:
                raise Unsupported("decorators")
            sc.assign(node.name, Func(self, node.name, node.args, node.body, sc))
        elif t is ast.Global:
            sc.globals.update(node.names)
        elif t is ast.Import:
            for a in node.names:
                sc.assign((a.asname or a.name).split(".")[0], self._import(a.name))
        elif t is ast.ImportFrom:
            mod = self._import(node.module or "")
            for a in node.names:
                if a.name == "*":
                    for k, v in mod.attrs.items():
                        sc.assign(k, v)
                elif a.name in mod.attrs:
                    sc.assign(a.asname or a.name, mod.attrs[a.name])
                else:
                    raise ImportError(f"cannot import name '{a.name}' from '{mod.name}'")
        elif t is ast.Try:
            self._try(node, sc)
        elif t is ast.Raise:
            if node.exc is None:
                raise RuntimeError("No active exception to reraise")
            exc = self.eval(node.exc, sc)
            if isinstance(exc, type) and issubclass(exc, BaseException) and exc in EXCEPTIONS.values():
                exc = exc()
            if not (isinstance(exc, Exception) and type(exc) in EXCEPTIONS.values()):
                raise TypeError("exceptions must derive from BaseException")
            raise exc
        elif t is ast.Assert:
            if not self.eval(node.test, sc):
                raise AssertionError(self.eval(node.msg, sc) if node.msg else "")
        elif t is ast.Delete:
            for tgt in node.targets:
                if isinstance(tgt, ast.Subscript):
                    del self.eval(tgt.value, sc)[self.eval_index(tgt.slice, sc)]
                elif isinstance(tgt, ast.Name):
                    sc.vars.pop(tgt.id, None)
                else:
                    raise Unsupported("this del target")
        elif t is ast.With:
            self._with(node, sc)
        else:
            raise Unsupported(t.__name__)

    def _try(self, node: ast.Try, sc: Scope) -> None:
        try:
            try:
                self.run_block(node.body, sc)
            except Unsupported:
                raise
            except Exception as e:
                for h in node.handlers:
                    if h.type is None or self._matches(e, self.eval(h.type, sc)):
                        if h.name:
                            sc.assign(h.name, e)
                        self.run_block(h.body, sc)
                        break
                else:
                    raise
            else:
                self.run_block(node.orelse, sc)
        finally:
            self.run_block(node.finalbody, sc)

    @staticmethod
    def _matches(e: Exception, spec) -> bool:
        specs = spec if isinstance(spec, tuple) else (spec,)
        return any(isinstance(s, type) and s in EXCEPTIONS.values() and isinstance(e, s) for s in specs)

    def _with(self, node: ast.With, sc: Scope) -> None:
        files = []
        try:
            for item in node.items:
                f = self.eval(item.context_expr, sc)
                if not isinstance(f, SimFile):
                    raise Unsupported("`with` on anything but files")
                files.append(f)
                if item.optional_vars is not None:
                    self.assign(item.optional_vars, f, sc)
            self.run_block(node.body, sc)
        finally:
            for f in files:
                f.close()

    def _for(self, node: ast.For, sc: Scope) -> None:
        for item in self.eval(node.iter, sc):
            self._tick()
            self.assign(node.target, item, sc)
            try:
                self.run_block(node.body, sc)
            except _Break:
                return
            except _Continue:
                continue
        self.run_block(node.orelse, sc)

    def _import(self, name: str) -> Module:
        if name not in self.modules:
            raise ModuleNotFoundError(f"No module named '{name}'")
        return self.modules[name]

    @staticmethod
    def _as_load(target):
        if isinstance(target, ast.Name):
            return ast.Name(id=target.id, ctx=ast.Load())
        if isinstance(target, ast.Subscript):
            return ast.Subscript(value=target.value, slice=target.slice, ctx=ast.Load())
        raise Unsupported("this augmented-assignment target")

    def assign(self, target, value, sc: Scope) -> None:
        t = type(target)
        if t is ast.Name:
            sc.assign(target.id, value)
        elif t in (ast.Tuple, ast.List):
            vals = list(value)
            star = [i for i, e in enumerate(target.elts) if isinstance(e, ast.Starred)]
            if star:
                i = star[0]
                after = len(target.elts) - i - 1
                if len(vals) < len(target.elts) - 1:
                    raise ValueError("not enough values to unpack")
                for e, v in zip(target.elts[:i], vals[:i]):
                    self.assign(e, v, sc)
                self.assign(target.elts[i].value, vals[i : len(vals) - after], sc)
                for e, v in zip(target.elts[i + 1 :], vals[len(vals) - after :]):
                    self.assign(e, v, sc)
                return
            if len(vals) != len(target.elts):
                raise ValueError(f"{'not enough' if len(vals) < len(target.elts) else 'too many'} values to unpack (expected {len(target.elts)})")
            for e, v in zip(target.elts, vals):
                self.assign(e, v, sc)
        elif t is ast.Subscript:
            obj = self.eval(target.value, sc)
            if not isinstance(obj, (list, dict, bytearray)):
                raise TypeError(f"'{type(obj).__name__}' object does not support item assignment")
            obj[self.eval_index(target.slice, sc)] = value
        elif t is ast.Attribute:
            from .pwnmod import Context

            obj = self.eval(target.value, sc)
            if not isinstance(obj, Context) or target.attr.startswith("_"):
                raise Unsupported("assignment to attributes")
            obj.set(target.attr, value)
        else:
            raise Unsupported("this assignment target")

    # --------------------------------------------------------- expressions
    def eval_index(self, node, sc: Scope):
        if isinstance(node, ast.Slice):
            return slice(*(self.eval(p, sc) if p is not None else None for p in (node.lower, node.upper, node.step)))
        return self.eval(node, sc)

    def binop(self, op, a, b):
        fn = BINOPS.get(type(op))
        if fn is None:
            raise Unsupported(type(op).__name__)
        if isinstance(op, ast.Mult):
            for seq, n in ((a, b), (b, a)):
                if isinstance(seq, (str, bytes, bytearray, list, tuple)) and isinstance(n, int) and len(seq) * max(n, 0) > SIZE_LIMIT:
                    raise MemoryError("result too large")
        elif isinstance(op, ast.Mod) and isinstance(a, (str, bytes)) and WIDTH_RE.search(a if isinstance(a, str) else a.decode("latin-1")):
            raise MemoryError("format width too large")
        elif isinstance(op, ast.LShift) and isinstance(b, int) and b > 8192:
            raise OverflowError("shift too large")
        elif isinstance(op, ast.Pow):
            return self._safe_pow(a, b)
        return fn(a, b)

    def eval(self, node, sc: Scope):
        self._tick()
        t = type(node)
        if t is ast.Constant:
            return node.value
        if t is ast.Name:
            try:
                return sc.lookup(node.id)
            except KeyError:
                if node.id in self.builtins:
                    return self.builtins[node.id]
                raise NameError(f"name '{node.id}' is not defined")
        if t is ast.BinOp:
            return self.binop(node.op, self.eval(node.left, sc), self.eval(node.right, sc))
        if t is ast.UnaryOp:
            v = self.eval(node.operand, sc)
            if isinstance(node.op, ast.Not):
                return not v
            return {ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Invert: operator.invert}[type(node.op)](v)
        if t is ast.BoolOp:
            v = None
            for e in node.values:
                v = self.eval(e, sc)
                if isinstance(node.op, ast.And) and not v:
                    return v
                if isinstance(node.op, ast.Or) and v:
                    return v
            return v
        if t is ast.Compare:
            left = self.eval(node.left, sc)
            for op, comp in zip(node.ops, node.comparators):
                right = self.eval(comp, sc)
                if not CMPOPS[type(op)](left, right):
                    return False
                left = right
            return True
        if t is ast.IfExp:
            return self.eval(node.body if self.eval(node.test, sc) else node.orelse, sc)
        if t is ast.Call:
            return self._call(node, sc)
        if t is ast.Attribute:
            return self.getattr(self.eval(node.value, sc), node.attr)
        if t is ast.Subscript:
            return self.eval(node.value, sc)[self.eval_index(node.slice, sc)]
        if t is ast.Tuple:
            return tuple(self._elts(node.elts, sc))
        if t is ast.List:
            return list(self._elts(node.elts, sc))
        if t is ast.Set:
            return set(self._elts(node.elts, sc))
        if t is ast.Dict:
            d = {}
            for k, v in zip(node.keys, node.values):
                if k is None:
                    d.update(self.eval(v, sc))
                else:
                    d[self.eval(k, sc)] = self.eval(v, sc)
            return d
        if t is ast.JoinedStr:
            return "".join(self.eval(v, sc) if not isinstance(v, ast.Constant) else v.value for v in node.values)
        if t is ast.FormattedValue:
            v = self.eval(node.value, sc)
            if node.conversion == ord("r"):
                v = repr(v)
            elif node.conversion == ord("s"):
                v = str(v)
            elif node.conversion == ord("a"):
                v = ascii(v)
            spec = self.eval(node.format_spec, sc) if node.format_spec else ""
            return self._format(v, spec)
        if t is ast.Lambda:
            return Func(self, "<lambda>", node.args, node.body, sc, is_lambda=True)
        if t in (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp):
            return self._comp(node, sc)
        if t is ast.Starred:
            raise Unsupported("a bare starred expression")
        raise Unsupported(t.__name__)

    def _elts(self, elts, sc: Scope):
        for e in elts:
            if isinstance(e, ast.Starred):
                yield from self.eval(e.value, sc)
            else:
                yield self.eval(e, sc)

    def _comp(self, node, sc: Scope):
        inner = Scope(sc)
        out: list = []
        pairs: list = []

        def gen(i: int) -> None:
            if i == len(node.generators):
                if isinstance(node, ast.DictComp):
                    pairs.append((self.eval(node.key, inner), self.eval(node.value, inner)))
                else:
                    out.append(self.eval(node.elt, inner))
                return
            g = node.generators[i]
            for item in self.eval(g.iter, inner):
                self._tick()
                self.assign(g.target, item, inner)
                if all(self.eval(c, inner) for c in g.ifs):
                    gen(i + 1)

        gen(0)
        if isinstance(node, ast.DictComp):
            return dict(pairs)
        if isinstance(node, ast.SetComp):
            return set(out)
        return out if isinstance(node, ast.ListComp) else iter(out)

    def _call(self, node: ast.Call, sc: Scope):
        fn = self.eval(node.func, sc)
        args = list(self._elts(node.args, sc))
        kwargs: dict = {}
        for k in node.keywords:
            if k.arg is None:
                kwargs.update(self.eval(k.value, sc))
            else:
                kwargs[k.arg] = self.eval(k.value, sc)
        if not callable(fn):
            raise TypeError(f"'{type(fn).__name__}' object is not callable")
        if fn is self.builtins["dir"] and not args and not kwargs:
            names = sc.vars
            return sorted(n for n in names if not n.startswith("_"))
        big = any(isinstance(a, int) and not isinstance(a, bool) and a > SIZE_LIMIT for a in args[:1])
        if big and (fn in (bytes, bytearray) or getattr(fn, "__name__", "") in SIZED_METHODS | {"to_bytes"}):
            raise MemoryError("size too large")
        if getattr(fn, "__name__", "") == "to_bytes" and args and isinstance(args[0], int) and args[0] > SIZE_LIMIT:
            raise MemoryError("size too large")
        return fn(*args, **kwargs)

    def getattr(self, obj, name: str):
        if name.startswith("_"):
            raise AttributeError(f"access to '{name}' is not allowed")
        if isinstance(obj, Module):
            if name in obj.attrs:
                return obj.attrs[name]
            raise AttributeError(f"module '{obj.name}' has no attribute '{name}'")
        from .pwnmod import Context, Elf, Rop, Tube

        if isinstance(obj, Context):
            return obj.get(name)
        if isinstance(obj, (Tube, Elf, Rop)):
            return obj.attr(name)
        if isinstance(obj, (SimFile, Stream)):
            if name in ("read", "readline", "readlines", "write", "close", "flush"):
                return getattr(obj, name)
            raise AttributeError(f"'{type(obj).__name__}' object has no attribute '{name}'")
        if isinstance(obj, type):
            if name in TYPE_ATTRS.get(obj, ()):
                return getattr(obj, name)
            raise AttributeError(f"type object '{obj.__name__}' has no attribute '{name}'")
        allowed = ALLOWED_METHODS.get(type(obj))
        if allowed is not None and name in allowed:
            return getattr(obj, name)
        raise AttributeError(f"'{type(obj).__name__}' object has no attribute '{name}'")


# ---------------------------------------------------------------- the command
class PythonSession(Session):
    """The interactive prompt: statements are compiled-for-completeness only, then walked."""

    def __init__(self, shell: Shell) -> None:
        self.shell = shell
        self.interp = Interp(shell, [""], b"")
        self.buf: list[str] = []
        self.prompt = ">>> "
        self.pending_help = False  # help() with no args: the next lines are topics, not Python

    def banner(self) -> Result:
        return ok('Python 3.10.12 (main, Nov  6 2024, 20:22:13) [GCC 11.4.0] on linux\n'
                  'Type "help", "copyright", "credits" or "license" for more information.\n')

    def eof_line(self) -> str:  # type: ignore[override]
        return "exit()"  # Ctrl-D: actually call exit(), not just name the builtin

    def handle(self, line: str) -> Result:
        if self.pending_help:
            return self._help_line(line)
        if not self.buf and not line.strip():
            return Result()
        self.buf.append(line)
        source = "\n".join(self.buf)
        try:
            # compile_command only *compiles* (never runs); it tells us if the input is complete
            incomplete = codeop.compile_command(source, "<stdin>", "single") is None
        except (SyntaxError, ValueError, OverflowError):
            incomplete = False
        if incomplete and line.strip():
            self.prompt = "... "
            return Result()
        if incomplete and not line.strip():
            source += "\n"
        self.buf = []
        self.prompt = ">>> "
        it = self.interp
        it.out, it.err, it.out_size, it.steps = [], [], 0, 0
        try:
            it.run_source(source + "\n", "<stdin>", interactive=True)
        except _HelpPrompt:
            self.pending_help = True
            self.prompt = "help> "
        else:
            if it.exited:
                self.done = True
        return Result(out="".join(it.out).encode(errors="replace"), err="".join(it.err).encode(errors="replace"))

    def _help_line(self, line: str) -> Result:
        topic = line.strip()
        if not topic or topic.lower() in ("quit", "q"):
            self.pending_help = False
            self.prompt = ">>> "
            return Result(out=b"\n")
        return Result(out=(self.interp._help_topic(topic) + "\n\n").encode(errors="replace"))


def run_python(sh: Shell, args: list[str], stdin: bytes) -> Result:
    argv0, source, rest = "", None, []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-c" and i + 1 < len(args):
            source, argv0, rest = args[i + 1], "-c", args[i + 2 :]
            break
        if a in ("-u", "-B", "-q", "-I"):
            i += 1
            continue
        if a.startswith("-") and a != "-":
            return Result(err=f"Unknown option: {a}\nusage: python3 [option] ... [-c cmd | file | -] [arg] ...\n".encode(), status=2)
        argv0, rest = a, args[i + 1 :]
        break
    if source is None and argv0 in ("", "-"):
        if sh.tty and not stdin and argv0 == "":
            return Result(session=PythonSession(sh))
        source, argv0 = stdin.decode(errors="replace"), "-"
    elif source is None:
        try:
            source = sh.vfs.read(argv0, sh.cwd).decode(errors="replace")
        except VFSError as e:
            return Result(err=f"python3: can't open file '{argv0}': [Errno 2] No such file or directory\n".encode() if "No such" in str(e)
                          else f"python3: can't open file '{argv0}': [Errno 13] Permission denied\n".encode(), status=2)
    it = Interp(sh, [argv0] + rest, stdin if argv0 != "-" else b"", argv0)
    status = it.run_source(source, argv0 if argv0 != "-c" else "<string>")
    if status == 0:
        sh.world.events.add("py:ran")
    return Result(out="".join(it.out).encode(errors="replace"), err="".join(it.err).encode(errors="replace"), status=status)


@command("python3", "python")
def python3(sh, args, stdin):
    return run_python(sh, args, stdin)
