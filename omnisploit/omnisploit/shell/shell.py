"""The fake bash: parses a line, runs pipelines against the VFS, collects output."""

from __future__ import annotations

import fnmatch
import posixpath
import re
from dataclasses import dataclass, field
from typing import Callable

from ..engine.vfs import Node, VFSError
from ..engine.world import World
from .parser import ParseError, Op, Pipeline, Simple, Word, parse, tokenize
from .script import (
    Case, Cmd, For, Func, Group, If, Incomplete, Pipe, Redirected, ScriptSyntaxError, While,
    parse_script, unparse,
)

ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
LOOP_LIMIT = 20000
OUTPUT_LIMIT = 2_000_000
FUNCTION_DEPTH_LIMIT = 100
DEFAULT_PS1 = r"\u@\h:\w\$ "
POSITIONAL_KEYS = ["0", "#", "@", "*"] + [str(i) for i in range(1, 10)]


class ScriptError(Exception):
    """A script hit a safety limit (runaway loop, runaway output)."""


class _LoopControl(Exception):
    def __init__(self, n: int = 1) -> None:
        self.n = n


class _Return(Exception):
    def __init__(self, n: int = 0) -> None:
        self.n = n


class _Break(_LoopControl):
    pass


class _Continue(_LoopControl):
    pass


@dataclass
class Result:
    out: bytes = b""
    err: bytes = b""
    status: int = 0
    action: tuple | None = None  # ("edit", abs_path, display) | ("clear",) | ("quit",)
    session: "Session | None" = None  # an interactive sub-program (gdb, r2, ...)


def ok(text: str = "", status: int = 0) -> Result:
    return Result(out=text.encode(errors="replace"), status=status)


def fail(text: str, status: int = 1) -> Result:
    return Result(err=(text + "\n").encode(errors="replace"), status=status)


class Session:
    """Base class for interactive programs that take over the prompt."""

    prompt = "> "
    done = False
    secret = False  # True while asking for a password: the UI does not echo the answer

    def banner(self) -> Result:
        return Result()

    def interrupt(self) -> None:
        """Ctrl-C. Most sessions just end; the default does nothing."""

    def eof_line(self) -> str:
        """The command line Ctrl-D submits when nothing is typed. Most sessions want plain "exit"."""
        return "exit"

    def handle(self, line: str) -> Result:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass
class Job:
    """A background job. Most finish immediately ("Done"); a listener stays "Running" until something connects."""

    num: int
    text: str
    state: str = "Done"
    listener: object | None = None  # a network Listener for `nc -l ... &`
    pid: int = 0


@dataclass
class Output:
    chunks: list[tuple[bytes, bool]] = field(default_factory=list)  # (data, is_stderr)
    actions: list[tuple] = field(default_factory=list)


ProgramHandler = Callable[["Shell", object, list[str], bytes], Result]


class LevelSession(Session):
    """Interactive run of a level binary: the binary's prompt, then one line."""

    def __init__(self, shell: "Shell", proc) -> None:
        self.shell = shell
        self.proc = proc
        self.prompt = proc.banner.decode(errors="replace")

    def handle(self, line: str) -> Result:
        self.done = True
        return _finish_run(self.shell, self.proc, line.encode(errors="replace") + b"\n", b"")


SIGNAL_INFO = {  # (bash's description, exit status 128+signum, text the program itself would print first)
    "SIGSEGV": ("Segmentation fault", 139, ""),
    "SIGILL": ("Illegal instruction", 132, ""),
    "SIGABRT": ("Aborted", 134, "*** stack smashing detected ***: terminated\n"),
}


def _finish_run(shell: "Shell", proc, data: bytes, prefix: bytes) -> Result:
    res = proc.feed(data)
    out = prefix + res.stdout
    if res.solved:
        shell.world.events.add(f"solved:{proc.level.id}")
    if res.crash:
        shell.world.events.add(f"crash:{proc.level.id}")
        if res.crash.signal == "SIGABRT":
            shell.world.events.add(f"abort:{proc.level.id}")
        what, status, detail = SIGNAL_INFO.get(res.crash.signal, SIGNAL_INFO["SIGSEGV"])
        return Result(out=out, err=f"{detail}{what} (core dumped)\n".encode(), status=status)
    return Result(out=out, status=res.exit_code or 0)


def run_level_program(shell: "Shell", node, argv: list[str], stdin: bytes) -> Result:
    """Program handler for ``level:<id>`` nodes (the vulnerable target binaries)."""
    level = shell.world.level_for(node)
    if level is None:
        return fail(f"bash: {argv[0]}: cannot execute binary file: Exec format error", 126)
    proc = shell.world.new_process(level, suid=bool(node.mode & 0o4000))
    if stdin or not shell.tty:
        # piped / redirected input: the prompt is part of the program's stdout
        return _finish_run(shell, proc, stdin, proc.banner)
    return Result(session=LevelSession(shell, proc))


class Shell:
    def __init__(self, world: World) -> None:
        self.world = world
        self.vfs = world.vfs
        self.cwd = world.home
        self.env = {
            "HOME": world.home,
            "USER": world.user,
            "LOGNAME": world.user,
            "SHELL": "/bin/bash",
            "TERM": "xterm-256color",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOSTNAME": world.hostname,
            "PWD": self.cwd,
            "LANG": "en_US.UTF-8",
            "PS1": DEFAULT_PS1,
        }
        self.oldpwd = self.cwd
        self.status = 0
        self.width = 80
        self.tty = False  # is the current stage's stdout the terminal?
        self.session: Session | None = None
        self.program_handlers: dict[str, ProgramHandler] = {"level": run_level_program}
        self.aliases: dict[str, str] = {}
        self.history: list[str] = []
        self.jobs: list[Job] = []  # background jobs; finished ones are reported once by `jobs`
        self.bg_job: Job | None = None  # the job being started right now (lets nc -l turn into a listener)
        self.positional: list[str] = []
        self.functions: dict[str, list] = {}
        self._locals: list[dict[str, str | None]] = []  # one frame per active function call
        self._func_depth = 0
        self._stdin_buf: bytearray | None = None  # stdin of the enclosing compound command
        self._job_no = 0
        self._pending: list[str] = []  # lines of an unfinished compound command
        self._loop_depth = 0
        self._iter = 0
        self._out_total = 0
        self._sinks: list[Output] = [Output()]

    # ------------------------------------------------------------ prompt
    def prompt(self) -> str:
        if self.session:
            return self.session.prompt
        if self._pending:
            return "> "
        return self._expand_ps1(self.env.get("PS1", DEFAULT_PS1))

    def _expand_ps1(self, ps1: str) -> str:
        import time

        home = self.env.get("HOME", "/")
        path = self.cwd
        if path == home:
            path = "~"
        elif path.startswith(home + "/"):
            path = "~" + path[len(home) :]
        base = "~" if path == "~" else posixpath.basename(self.cwd) or "/"
        table = {
            "u": self.vfs.user,
            "h": self.world.hostname.split(".")[0],
            "H": self.world.hostname,
            "w": path,
            "W": base,
            "$": "#" if self.vfs.user == "root" else "$",
            "t": time.strftime("%H:%M:%S"),
            "d": time.strftime("%a %b %d"),
            "\\": "\\",
        }
        ps1 = re.sub(r"\\\[.*?\\\]", "", ps1)  # \[ ... \] wraps non-printing sequences (colors): drop
        out, i = [], 0
        while i < len(ps1):
            if ps1[i] == "\\" and i + 1 < len(ps1):
                out.append(table.get(ps1[i + 1], ""))  # unsupported escapes (\n, \[, \e...) are dropped
                i += 2
            else:
                out.append(ps1[i])
                i += 1
        return "".join(out)

    def abspath(self, path: str) -> str:
        return self.vfs.normalize(path, self.cwd)

    # --------------------------------------------------------- entry points
    def run_line(self, line: str) -> Output:
        """Run one line typed at the prompt; returns everything to display."""
        out = self._run_line(line)
        notes = self.world.net.take_notices(self.world.vantage)  # e.g. a listener got a connection
        if notes:
            out.chunks.append(("".join(n + "\n" for n in notes).encode(), False))
        return out

    def _run_line(self, line: str) -> Output:
        out = Output()
        self._sinks = [out]
        if self.session:
            res = self.session.handle(line)
            self.status = res.status
            self._emit(res)
            if self.session.done:
                self.session = None
            return out
        if not self._pending and not line.strip():
            return out
        text = "\n".join(self._pending + [line])
        try:
            items = parse_script(text)
        except Incomplete:
            self._pending.append(line)
            return out
        except ScriptSyntaxError as e:
            self._pending = []
            self._err(f"bash: {e}\n")
            self.status = 2
            return out
        self._pending = []
        self._iter = self._out_total = 0
        self._guarded(lambda: self._run_items(items))
        return out

    def secret_input(self) -> bool:
        return bool(self.session and self.session.secret)

    def eof_line(self) -> str:
        return self.session.eof_line() if self.session else "exit"

    def abort_input(self) -> None:
        """Ctrl-C: drop a half-typed compound command, or interrupt the interactive program."""
        self._pending = []
        if self.session is not None:
            self.session.interrupt()
            if self.session.done:
                self.session = None

    def _guarded(self, fn) -> None:
        try:
            fn()
        except ScriptError as e:
            self._err(f"bash: {e}\n")
            self.status = 1
        except (_LoopControl, _Return):
            self.status = 0

    def _run_text(self, text: str) -> None:
        if not text.strip():
            return
        try:
            items = parse_script(text)
        except Incomplete:
            self._err("bash: syntax error: unexpected end of file\n")
            self.status = 2
            return
        except ScriptSyntaxError as e:
            self._err(f"bash: {e}\n")
            self.status = 2
            return
        self._run_items(items)

    def run_script(self, text: str, args: list[str] | None = None, name: str = "bash") -> None:
        if args is None:
            self._run_text(text)
            return
        saved = {k: self.env.get(k) for k in POSITIONAL_KEYS}
        saved_pos = self.positional
        self.positional = list(args)
        self.env["0"] = name
        self._mirror_positional()
        try:
            self._run_text(text)
        finally:
            self.positional = saved_pos
            for k, v in saved.items():
                if v is None:
                    self.env.pop(k, None)
                else:
                    self.env[k] = v

    def _mirror_positional(self) -> None:
        for i in range(1, 10):
            if i <= len(self.positional):
                self.env[str(i)] = self.positional[i - 1]
            else:
                self.env.pop(str(i), None)
        self.env["#"] = str(len(self.positional))
        self.env["@"] = self.env["*"] = " ".join(self.positional)

    def run_captured(self, text: str, args: list[str] | None = None, name: str = "bash") -> Result:
        """Run script text with private sinks so the caller can pipe/redirect it."""
        sink = Output()
        self._sinks.append(sink)
        try:
            self.run_script(text, args if args is not None else [], name)
        finally:
            self._sinks.pop()
        res = Result(
            out=b"".join(d for d, e in sink.chunks if not e),
            err=b"".join(d for d, e in sink.chunks if e),
            status=self.status,
        )
        self._sinks[-1].actions.extend(sink.actions)
        return res

    # ------------------------------------------------------- compound commands
    def _run_items(self, items) -> int:
        status = self.status
        for connector, node in items:
            if connector == "&&" and status != 0:
                continue
            if connector == "||" and status == 0:
                continue
            status = self._run_node(node)
            self.status = status
        return status

    def _tick(self) -> None:
        self._iter += 1
        if self._iter > LOOP_LIMIT:
            raise ScriptError("loop iteration limit reached (possible infinite loop)")

    def _run_node(self, node) -> int:
        if isinstance(node, Cmd):
            return self._run_background(node.text) if node.background else self._run_cmd_text(node.text)
        if isinstance(node, If):
            for cond, body in node.branches:
                if self._run_items(cond) == 0:
                    return self._run_items(body)
            return self._run_items(node.otherwise) if node.otherwise else 0
        if isinstance(node, For):
            words = list(self.positional) if node.words is None else self._expand_words(node.words)
            return self._loop(((node.var, w) for w in words), None, node.body, False)
        if isinstance(node, While):
            return self._loop(None, node.cond, node.body, node.until)
        if isinstance(node, Func):
            self.functions[node.name] = node.body
            return 0
        if isinstance(node, Group):
            return self._run_items(node.body)
        if isinstance(node, Case):
            word = self._expand_one(node.word)
            for patterns, body in node.clauses:
                if any(fnmatch.fnmatchcase(word, self._expand_one(p)) for p in patterns):
                    return self._run_items(body)
            return 0
        if isinstance(node, Pipe):
            return self._run_pipe(node.nodes)
        if isinstance(node, Redirected):
            return self._run_redirected(node)
        raise AssertionError(node)

    # ------------------------------------------------- functions and compound I/O
    def _expand_one(self, raw: str) -> str:
        try:
            toks = tokenize(raw, self.env, self.status, self._capture)
        except ParseError as e:
            raise ScriptError(str(e))
        return "".join(t.text for t in toks if isinstance(t, Word))

    def define_function(self, name: str, body_text: str) -> bool:
        try:
            self.functions[name] = parse_script(body_text)
            return True
        except (Incomplete, ScriptSyntaxError):
            return False

    def function_source(self, name: str) -> str:
        body = unparse(self.functions[name])
        return f"{name} () \n{{ \n" + "\n".join("    " + l for l in body.split("\n")) + "\n}"

    def _call_function(self, name: str, args: list[str]) -> Result:
        if self._func_depth >= FUNCTION_DEPTH_LIMIT:
            return fail(f"bash: {name}: maximum function nesting level exceeded ({FUNCTION_DEPTH_LIMIT})")
        saved = {k: self.env.get(k) for k in POSITIONAL_KEYS if k != "0"}
        saved_pos = self.positional
        self.positional = list(args)
        self._mirror_positional()
        self._locals.append({})
        self._func_depth += 1
        sink = Output()
        self._sinks.append(sink)
        try:
            status = self._run_items(self.functions[name])
        except _Return as r:
            status = r.n
        finally:
            self._sinks.pop()
            self._func_depth -= 1
            for k, old in self._locals.pop().items():
                if old is None:
                    self.env.pop(k, None)
                else:
                    self.env[k] = old
            self.positional = saved_pos
            for k, v in saved.items():
                if v is None:
                    self.env.pop(k, None)
                else:
                    self.env[k] = v
        self._sinks[-1].actions.extend(sink.actions)
        return Result(
            out=b"".join(d for d, e in sink.chunks if not e),
            err=b"".join(d for d, e in sink.chunks if e),
            status=status,
        )

    def builtin_return(self, args: list[str]) -> Result:
        if self._func_depth == 0:
            return fail("bash: return: can only `return' from a function or sourced script")
        raise _Return(int(args[0]) & 255 if args and args[0].lstrip("-").isdigit() else self.status)

    def builtin_local(self, args: list[str]) -> Result:
        if not self._locals:
            return fail("bash: local: can only be used in a function")
        frame = self._locals[-1]
        for a in args:
            name, eq, value = a.partition("=")
            if name not in frame:
                frame[name] = self.env.get(name)
            if eq:
                self.env[name] = value
            elif name not in self.env:
                self.env[name] = ""
        return Result()

    def builtin_read(self, args: list[str]) -> Result:
        names = [a for a in args if not a.startswith("-")] or ["REPLY"]
        buf = self._stdin_buf
        if not buf:
            for n in names:
                self.env[n] = ""
            return Result(status=1)
        nl = buf.find(b"\n")
        line = bytes(buf if nl < 0 else buf[:nl]).decode(errors="replace")
        del buf[: len(buf) if nl < 0 else nl + 1]
        parts = line.split(None, len(names) - 1) if len(names) > 1 else [line.strip()]
        for i, n in enumerate(names):
            self.env[n] = parts[i] if i < len(parts) else ""
        return Result(status=0 if nl >= 0 else 1)

    def _with_stdin(self, data: bytes, fn):
        saved = self._stdin_buf
        self._stdin_buf = bytearray(data)
        try:
            return fn()
        finally:
            self._stdin_buf = saved

    def _run_pipe(self, nodes) -> int:
        data: bytes | None = None
        status = 0
        for i, node in enumerate(nodes):
            last = i == len(nodes) - 1
            sink = Output()
            if not last:
                self._sinks.append(sink)
            try:
                status = self._run_node(node) if data is None else self._with_stdin(data, lambda n=node: self._run_node(n))
            finally:
                if not last:
                    self._sinks.pop()
            if not last:
                data = b"".join(d for d, e in sink.chunks if not e)
                for d, e in sink.chunks:
                    if e:
                        self._sinks[-1].chunks.append((d, True))
                self._sinks[-1].actions.extend(sink.actions)
        return status

    def _run_redirected(self, node: Redirected) -> int:
        try:
            items = parse(tokenize("true " + node.redir, self.env, self.status, self._capture))
            simple = items[0][1][0]
        except (ParseError, IndexError) as e:
            self._err(f"bash: {e}\n")
            return 2
        stdin_data = None
        if simple.stdin is not None:
            try:
                stdin_data = self.vfs.read(simple.stdin.text, self.cwd)
            except VFSError as e:
                self._err(f"bash: {e}\n")
                return 1
        capture = simple.stdout is not None or simple.stderr is not None
        sink = Output()
        if capture:
            self._sinks.append(sink)
        try:
            if stdin_data is None:
                status = self._run_node(node.node)
            else:
                status = self._with_stdin(stdin_data, lambda: self._run_node(node.node))
        finally:
            if capture:
                self._sinks.pop()
        if capture:
            self._sinks[-1].actions.extend(sink.actions)
            if simple.stderr == "out":
                out, err = b"".join(d for d, _ in sink.chunks), b""
            else:
                out = b"".join(d for d, e in sink.chunks if not e)
                err = b"".join(d for d, e in sink.chunks if e)
                if isinstance(simple.stderr, tuple) and self._write_target(simple.stderr[0].text, err, simple.stderr[1]):
                    err = b""
            if simple.stdout is not None:
                self._write_target(simple.stdout[0].text, out, simple.stdout[1])
            else:
                self._out(out)
            if err:
                self._sinks[-1].chunks.append((err, True))
        return status

    def _loop(self, assignments, cond, body, until: bool) -> int:
        status = 0
        self._loop_depth += 1
        try:
            it = iter(assignments) if assignments is not None else None
            while True:
                self._tick()
                if it is not None:
                    try:
                        var, value = next(it)
                    except StopIteration:
                        break
                    self.env[var] = value
                else:
                    if (self._run_items(cond) == 0) == until:
                        break
                try:
                    status = self._run_items(body)
                except _Continue as e:
                    if e.n > 1:
                        raise _Continue(e.n - 1)
                except _Break as e:
                    if e.n > 1:
                        raise _Break(e.n - 1)
                    break
        finally:
            self._loop_depth -= 1
        return status

    def _expand_words(self, raw: str) -> list[str]:
        try:
            toks = tokenize(raw, self.env, self.status, self._capture)
        except ParseError as e:
            raise ScriptError(str(e))
        words = [t for t in toks if isinstance(t, Word)]
        return self._expand(words)

    def _run_background(self, text: str) -> int:
        """No real concurrency: run to completion, but look like a bash job.

        The exception is a listener (``nc -l ... &``): it registers itself on the job and stays
        "Running" until a connection arrives, which ``fg`` then attaches to.
        """
        self._job_no += 1
        job = Job(self._job_no, text, pid=2400 + self._job_no * 7)
        self._err(f"[{self._job_no}] {job.pid}\n")
        self.bg_job = job
        try:
            self._run_cmd_text(text)
        finally:
            self.bg_job = None
        job.state = "Running" if job.listener is not None else ("Done" if self.status == 0 else f"Exit {self.status}")
        self.jobs.append(job)
        return 0

    def _run_cmd_text(self, text: str) -> int:
        try:
            toks = self._expand_aliases(tokenize(text, self.env, self.status, self._capture))
            items = parse(toks)
        except ParseError as e:
            self._err(f"bash: {e}\n")
            self.status = 2
            return 2
        for connector, pipeline in items:
            if connector == "&&" and self.status != 0:
                continue
            if connector == "||" and self.status == 0:
                continue
            self.status = self._run_pipeline(pipeline)
        return self.status

    def _expand_aliases(self, toks: list, seen: frozenset[str] = frozenset()) -> list:
        if not self.aliases:
            return toks
        out: list = []
        cmd_pos = True
        for t in toks:
            if isinstance(t, Op):
                out.append(t)
                cmd_pos = t.value in ("|", "||", "&&", ";")
            elif cmd_pos and not t.quoted and t.text in self.aliases and t.text not in seen:
                try:
                    repl = tokenize(self.aliases[t.text], self.env, self.status, self._capture)
                except ParseError:
                    repl = [t]
                out.extend(self._expand_aliases(repl, seen | {t.text}))
                cmd_pos = False
            else:
                out.append(t)
                cmd_pos = False
        return out

    def loop_control(self, kind: str, args: list[str]) -> Result:
        if self._loop_depth == 0:
            return fail(f"bash: {kind}: only meaningful in a `for', `while', or `until' loop")
        n = int(args[0]) if args and args[0].isdigit() and int(args[0]) > 0 else 1
        raise (_Break if kind == "break" else _Continue)(n)

    def _capture(self, cmd: str) -> str:
        """$(...) support: run cmd with a private sink and return its stdout."""
        sink = Output()
        self._sinks.append(sink)
        saved = self.status
        try:
            self._run_text(cmd)
        finally:
            self._sinks.pop()
        self.status = saved
        # stderr and actions of the inner command still reach the user
        for data, is_err in sink.chunks:
            if is_err:
                self._sinks[-1].chunks.append((data, True))
        self._sinks[-1].actions.extend(sink.actions)
        return b"".join(d for d, e in sink.chunks if not e).decode(errors="replace")

    # ------------------------------------------------------------- plumbing
    def _out(self, data: bytes) -> None:
        if data:
            self._out_total += len(data)
            if self._out_total > OUTPUT_LIMIT:
                raise ScriptError("output limit reached")
            self._sinks[-1].chunks.append((data, False))

    def _err(self, text: str) -> None:
        if text:
            self._sinks[-1].chunks.append((text.encode(errors="replace"), True))

    def _emit(self, res: Result) -> None:
        self._out(res.out)
        if res.err:
            self._sinks[-1].chunks.append((res.err, True))
        if res.action:
            self._sinks[-1].actions.append(res.action)
        if res.session:
            self.session = res.session
            self._emit(res.session.banner())
            if self.session is not None and self.session.done:  # e.g. an ssh one-liner that finished
                self.session = None

    def _write_target(self, target: str, data: bytes, append: bool) -> bool:
        if target == "/dev/null":
            return True
        try:
            self.vfs.write(target, data, self.cwd, append=append)
            return True
        except VFSError as e:
            self._err(f"bash: {e}\n")
            return False

    def _run_pipeline(self, pipeline: Pipeline) -> int:
        data = b""
        status = 0
        last = len(pipeline) - 1
        if last:
            self.world.events.add("pipe")
        for idx, simple in enumerate(pipeline):
            stdin = data if idx else bytes(self._stdin_buf or b"")
            data = b""
            if simple.stdin is not None:
                try:
                    stdin = self.vfs.read(simple.stdin.text, self.cwd)
                except VFSError as e:
                    self._err(f"bash: {e}\n")
                    status = 1
                    continue
            argv = self._expand(simple.argv)
            if simple.stdout is not None or simple.stdin is not None:
                self.world.events.add("redirect")
            assigns: list[str] = []
            while argv and ASSIGN.match(argv[0]):
                assigns.append(argv.pop(0))
            self.tty = idx == last and simple.stdout is None and len(self._sinks) == 1
            if assigns and not argv:
                for a in assigns:
                    k, v = a.split("=", 1)
                    self.env[k] = v
                res = Result()
            else:
                saved = {a.split("=", 1)[0]: self.env.get(a.split("=", 1)[0]) for a in assigns}
                for a in assigns:
                    k, v = a.split("=", 1)
                    self.env[k] = v
                try:
                    res = self._run_simple(argv, stdin)
                finally:
                    for k, old in saved.items():
                        if old is None:
                            self.env.pop(k, None)
                        else:
                            self.env[k] = old
            status = res.status
            out, err = res.out, res.err
            if simple.stderr == "out":
                out, err = out + err, b""
            elif isinstance(simple.stderr, tuple):
                target, append = simple.stderr
                if self._write_target(target.text, err, append):
                    err = b""
            if err:
                self._sinks[-1].chunks.append((err, True))
            if res.action:
                self._sinks[-1].actions.append(res.action)
            if res.session:
                self.session = res.session
                self._emit(res.session.banner())
                if self.session is not None and self.session.done:
                    self.session = None
            if simple.stdout is not None:
                target, append = simple.stdout
                self._write_target(target.text, out, append)
            elif idx == last:
                self._out(out)
            else:
                data = out
        self.tty = False
        return status

    # ------------------------------------------------------------ expansion
    def _expand(self, words: list[Word]) -> list[str]:
        argv: list[str] = []
        for w in words:
            if w.glob:
                matches = self._glob(w.text)
                argv.extend(matches if matches else [w.text])
            else:
                argv.append(w.text)
        return argv

    def _glob(self, pattern: str) -> list[str]:
        head, _, base = pattern.rpartition("/")
        directory = (head + "/") if "/" in pattern else ""
        try:
            nodes = self.vfs.listdir(directory or ".", self.cwd)
        except VFSError:
            return []
        out = []
        for n in nodes:
            if n.name.startswith(".") and not base.startswith("."):
                continue
            if fnmatch.fnmatchcase(n.name, base):
                out.append(directory + n.name)
        return sorted(out)

    # ------------------------------------------------------------ execution
    def find_in_path(self, name: str) -> tuple[str, "Node"] | None:
        for d in self.env.get("PATH", "").split(":"):
            if not d:
                continue
            try:
                node = self.vfs.get(d.rstrip("/") + "/" + name, self.cwd)
            except VFSError:
                continue
            if not node.is_dir and self.vfs.can_exec(node):
                return d.rstrip("/") + "/" + name, node
        return None

    def _run_simple(self, argv: list[str], stdin: bytes) -> Result:
        from .commands import REGISTRY, SHELL_BUILTINS

        if not argv:
            return Result()
        name, args = argv[0], argv[1:]
        try:
            if name in self.functions:
                return self._call_function(name, args)
            if "/" in name:
                try:
                    node = self.vfs.get(name, self.cwd)
                except VFSError:
                    return fail(f"bash: {name}: No such file or directory", 127)
                return self._exec_node(name, node, argv, stdin)
            if name in SHELL_BUILTINS:
                return REGISTRY[name](self, args, stdin)
            found = self.find_in_path(name)
            if found is None:
                return fail(f"{name}: command not found", 127)
            return self._exec_node(name, found[1], argv, stdin)
        except VFSError as e:
            return fail(f"{name}: {e}")

    def _exec_node(self, name: str, node: "Node", argv: list[str], stdin: bytes) -> Result:
        from .commands import REGISTRY

        if node.is_dir:
            return fail(f"bash: {name}: Is a directory", 126)
        if not self.vfs.can_exec(node):
            return fail(f"bash: {name}: Permission denied", 126)
        prog = node.program
        if prog and prog.startswith("builtin:"):
            fn = REGISTRY.get(prog[8:])
            if fn:
                return fn(self, argv[1:], stdin)
        if prog:
            handler = self.program_handlers.get(prog.split(":", 1)[0])
            if handler:
                return handler(self, node, argv, stdin)
        elif node.data.startswith(b"#!"):
            first, _, body = node.data.decode(errors="replace").partition("\n")
            interp = first[2:].strip().split()
            if interp and interp[-1].rsplit("/", 1)[-1] in ("sh", "bash"):
                return self.run_captured(body, argv[1:], name)
            return fail(f"bash: {name}: {first[2:].strip()}: bad interpreter: No such file or directory", 126)
        return fail(f"bash: {name}: cannot execute binary file: Exec format error", 126)

    # ------------------------------------------------------------ completion
    def complete(self, text: str) -> tuple[int, list[str]]:
        """Return (start_index_of_word, candidate_replacements) for Tab."""
        from .commands import SHELL_BUILTINS

        idx = max(text.rfind(" "), text.rfind("\t")) + 1
        word = text[idx:]
        before = text[:idx].rstrip()
        first = before == "" or before.endswith(("|", ";", "&&", "||"))
        if first and "/" not in word:
            names = set(SHELL_BUILTINS)
            for d in self.env.get("PATH", "").split(":"):
                try:
                    names.update(n.name for n in self.vfs.listdir(d, self.cwd) if not n.is_dir)
                except VFSError:
                    pass
            return idx, sorted(n for n in names if n.startswith(word))
        if "/" in word:
            head = word[: word.rfind("/") + 1]
            base = word[len(head) :]
            directory = head
        else:
            head, base, directory = "", word, "."
        try:
            nodes = self.vfs.listdir(directory, self.cwd)
        except VFSError:
            return idx, []
        cands = []
        for n in nodes:
            if n.name.startswith(base) and (base.startswith(".") or not n.name.startswith(".")):
                cands.append(head + n.name + ("/" if n.is_dir else ""))
        return idx, sorted(cands)

    def display_path(self, path: str) -> str:
        return posixpath.normpath(path)
