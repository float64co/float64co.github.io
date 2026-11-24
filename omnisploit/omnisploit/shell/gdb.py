"""A simulated gdb (Intel syntax, no debug info) for the level binaries.

The debugger never executes anything: it drives :class:`engine.machine.Process`.
``run`` reads one line for the program (or ``< file``), and a crash leaves the
process stopped and inspectable. Execution is done by :mod:`engine.cpu`, a small
instruction stepper, so breakpoints, stepi/nexti and finish work between any two
instructions. ``next``/``step`` behave like gdb on a binary without line info.
"""

from __future__ import annotations

import re

from dataclasses import dataclass

from ..engine.cpu import LIBC_RET, REG32, Cpu, Stop
from ..engine.machine import Level
from ..engine.vfs import VFSError
from .commands import command
from .shell import Result, Session, Shell, fail, ok

PROMPT = "(gdb) "
REG_ORDER = ["rax", "rbx", "rcx", "rdx", "rsi", "rdi", "rbp", "rsp", "r8", "r9", "r10", "r11",
             "r12", "r13", "r14", "r15", "rip"]
SEGS = {"cs": 0x33, "ss": 0x2B, "ds": 0, "es": 0, "fs": 0, "gs": 0, "fs_base": 0x7FFFF7D8A740, "gs_base": 0}
EFLAGS = 0x246
REG_ALIAS = {"pc": "rip", "sp": "rsp", "fp": "rbp"}
POINTER_REGS = {"rsp", "rbp"}
SIZES = {"b": 1, "h": 2, "w": 4, "g": 8}
PER_LINE = {1: 8, 2: 8, 4: 4, 8: 2}
BANNER = """\
GNU gdb (Ubuntu 12.1-0ubuntu1~22.04.2) 12.1
Copyright (C) 2022 Free Software Foundation, Inc.
License GPLv3+: GNU GPL version 3 or later <http://gnu.org/licenses/gpl.html>
This is free software: you are free to change and redistribute it.
There is NO WARRANTY, to the extent permitted by law.
Type "show copying" and "show warranty" for details.
This GDB was configured as "x86_64-linux-gnu".
Type "show configuration" for configuration details.
For bug reporting instructions, please see:
<https://www.gnu.org/software/gdb/bugs/>.
Find the GDB manual and other documentation resources online at:
    <http://www.gnu.org/software/gdb/documentation/>.

For help, type "help".
Type "apropos word" to search for commands related to "word"...
"""
HELP = """\
List of commands (a subset of gdb is simulated):

run, r -- Start the program; it reads one line from you ("run < FILE" reads FILE)
start -- Run to the start of main
continue, c -- Resume (a crashed program just dies)
stepi, si / nexti, ni -- Execute one instruction (ni steps over calls)
next, step -- Run until the current function returns (no line info here)
finish -- Run until the current function returns
break, tbreak LOCATION -- FUNC, *ADDR or *$rip; delete, disable, enable [N...]
watch ADDR -- Stop (after the fact) when the 8 bytes at ADDR change; shares numbering with break
info breakpoints -- List breakpoints and watchpoints, and their hit counts
kill, k -- Kill the program being debugged
backtrace, bt, where -- Print the call stack
info registers [REG...] -- Show registers of the crashed program
info functions [REGEX] -- List the functions
info symbol ADDR -- Say which symbol an address belongs to
x/NFU ADDR -- Examine memory (F: x d u o t c a s i, U: b h w g)
print[/F] EXPR, p -- Evaluate an expression: $rip, $rsp, win, 0x401196+8, ...
disassemble[/r] [FUNC|ADDR] -- Disassemble a function (Intel syntax)
set, show -- Accepted and ignored
quit, q -- Leave gdb

A watchpoint checks after each instruction, not through real hardware, and always watches a
plain 8-byte range at a fixed address (no expressions, no types, no rwatch/awatch distinction).
"""


class GdbError(Exception):
    pass


def _s64(v: int) -> int:
    v &= 0xFFFF_FFFF_FFFF_FFFF
    return v - (1 << 64) if v >> 63 else v


def _flags(v: int) -> str:
    names = [(0, "CF"), (2, "PF"), (4, "AF"), (6, "ZF"), (7, "SF"), (8, "TF"), (9, "IF"), (10, "DF"), (11, "OF")]
    return "[ " + " ".join(n for bit, n in names if v >> bit & 1) + " ]"


@dataclass
class Breakpoint:
    num: int
    addr: int
    temp: bool = False
    enabled: bool = True
    hits: int = 0
    kind: str = "breakpoint"  # "breakpoint" or "watch"
    size: int = 8  # bytes watched (watch only)
    expr: str = ""  # the text the player typed (watch only; shown in place of an address)


class GdbSession(Session):
    prompt = PROMPT

    def __init__(self, shell: Shell, level: Level | None, path: str, quiet: bool, suid: bool = False) -> None:
        self.shell = shell
        self.level = level
        self.path = path  # absolute path of the loaded file ("" when none)
        self.quiet = quiet
        self.suid = suid  # the VFS file's own setuid bit, as of when gdb loaded it
        self.image = level.build_elf() if level else b""
        self.cpu: Cpu | None = None  # the live (stopped) process, if any
        self.pid = 0
        self.resume_fn = None  # re-issues the interrupted command once the program gets its input
        self.awaiting = False  # the program is blocked in gets()
        self.breakpoints: list[Breakpoint] = []
        self.bp_count = 0
        self.history: list[int] = []
        self.next_addr = 0  # where a bare `x` continues
        self.fmt, self.size = "x", 4  # sticky `x` defaults
        self.startup: Result = Result()

    @property
    def alive(self) -> bool:
        return self.cpu is not None

    def _pc(self) -> int:
        assert self.cpu is not None
        return self.cpu.regs["rip"]

    # --------------------------------------------------------------- plumbing
    def banner(self) -> Result:
        return self.startup

    def handle(self, line: str) -> Result:
        if self.awaiting:
            self.awaiting = False
            self.prompt = PROMPT
            assert self.cpu is not None and self.resume_fn is not None
            self.cpu.give_input(line.encode(errors="replace"))
            return self.resume_fn()
        line = line.strip()
        if not line:
            return Result()
        m = re.match(r"([A-Za-z][A-Za-z_-]*)(/\S*)?\s*(.*)$", line)
        if not m:
            return fail(f'Undefined command: "{line.split()[0]}".  Try "help".')
        word, mod, rest = m.group(1), (m.group(2) or "")[1:], m.group(3)
        fn = self._lookup(word)
        if fn is None:
            return fail(f'Undefined command: "{word}".  Try "help".')
        try:
            return fn(mod, rest)
        except GdbError as e:
            return fail(str(e))

    def _lookup(self, word: str):
        table = {
            "run": self.c_run, "r": self.c_run, "start": self.c_start,
            "continue": self.c_continue, "c": self.c_continue, "cont": self.c_continue, "fg": self.c_continue,
            "next": self.c_next, "n": self.c_next, "step": self.c_next, "s": self.c_next,
            "stepi": self.c_stepi, "si": self.c_stepi, "nexti": self.c_nexti, "ni": self.c_nexti,
            "finish": self.c_finish, "fin": self.c_finish,
            "kill": self.c_kill, "k": self.c_kill,
            "backtrace": self.c_bt, "bt": self.c_bt, "where": self.c_bt,
            "x": self.c_x,
            "print": self.c_print, "p": self.c_print, "inspect": self.c_print, "output": self.c_print,
            "info": self.c_info, "i": self.c_info,
            "disassemble": self.c_disas, "disas": self.c_disas,
            "quit": self.c_quit, "q": self.c_quit, "exit": self.c_quit,
            "help": self.c_help, "h": self.c_help,
            "set": self.c_set, "show": self.c_ignore,
            "break": self.c_break, "b": self.c_break, "br": self.c_break,
            "tbreak": self.c_tbreak, "tb": self.c_tbreak,
            "delete": self.c_delete, "d": self.c_delete,
            "disable": lambda m, r: self.c_enable(r, False), "dis": lambda m, r: self.c_enable(r, False),
            "enable": lambda m, r: self.c_enable(r, True), "en": lambda m, r: self.c_enable(r, True),
            "watch": self.c_watch, "rwatch": self.c_watch, "awatch": self.c_watch,
        }
        return table.get(word)

    def _need_level(self) -> Level:
        if self.level is None:
            raise GdbError('No symbol table is loaded.  Use the "file" command.')
        return self.level

    # ---------------------------------------------------------------- running
    def _where(self, addr: int) -> str:
        lv = self._need_level()
        if addr == LIBC_RET:
            return "__libc_start_call_main"
        s = lv.symbol_at(addr)
        return s[0] if s else "??"

    def _frame_line(self, addr: int) -> str:
        return f"0x{addr:016x} in {self._where(addr)} ()"

    def _bp_addrs(self) -> frozenset[int]:
        return frozenset(b.addr for b in self.breakpoints if b.enabled and b.kind == "breakpoint")

    def _watch_specs(self):
        return [(b.addr, b.size) for b in self.breakpoints if b.enabled and b.kind == "watch"]

    def c_run(self, mod: str, rest: str, temp_main: bool = False) -> Result:
        lv = self._need_level()
        self.cpu = None
        stdin = None
        m = re.search(r"<\s*(\S+)", rest)
        prefix = b""
        if temp_main:
            prefix += self._add_bp(self._after_prologue(lv.function("main")), temp=True).encode()
        prefix += f"Starting program: {self.path} \n".encode()
        if m:
            try:
                stdin = self.shell.vfs.read(m.group(1), self.shell.cwd)
            except VFSError as e:
                return Result(out=prefix, err=f"{e}\n".encode())
        proc = self.shell.world.new_process(lv, suid=self.suid)
        self.pid = proc.pid
        self.cpu = proc.start(stdin)
        return self._go(lambda: self._cont(), prefix)

    def c_start(self, mod: str, rest: str) -> Result:
        return self.c_run(mod, rest, temp_main=True)

    def _go(self, fn, prefix: bytes = b"") -> Result:
        """Run one execution command; ``fn`` returns a Stop. Remembers ``fn`` in case input is needed."""
        self.resume_fn = lambda: self._go(fn)
        assert self.cpu is not None
        return self._describe(fn(), prefix)

    def _cont(self, depth_lt: int | None = None) -> Stop:
        assert self.cpu is not None
        return self.cpu.run(self._bp_addrs(), depth_lt, self._watch_specs())

    def _describe(self, stop: Stop, prefix: bytes = b"") -> Result:
        cpu = self.cpu
        assert cpu is not None
        w = self.shell.world
        lv = self._need_level()
        text = prefix + cpu.take_output()
        if cpu.solved:
            w.events.add(f"solved:{lv.id}")
        if stop.kind == "input":
            self.awaiting = True
            head, nl, tail = text.rpartition(b"\n")
            self.prompt = tail.decode(errors="replace")
            return Result(out=head + nl)
        if stop.kind == "exit":
            self.cpu = None
            code = cpu.exit_code or 0
            tail = "exited normally" if code == 0 else f"exited with code {code:02o}"
            return Result(out=text + f"[Inferior 1 (process {self.pid}) {tail}]\n".encode())
        if stop.kind == "fault":
            w.events.add(f"crash:{lv.id}")
            if cpu.signal == "SIGABRT":
                w.events.add(f"abort:{lv.id}")
            what = {"SIGILL": "Illegal instruction", "SIGABRT": "Aborted"}.get(cpu.signal or "", "Segmentation fault")
            text += f"\nProgram received signal {cpu.signal}, {what}.\n{self._frame_line(self._pc())}\n".encode()
            return Result(out=text)
        if stop.kind == "watch":
            addr, size, old, new = cpu.watch_hit
            wp = next((b for b in self.breakpoints if b.kind == "watch" and b.addr == addr and b.size == size), None)
            oldv = f"0x{int.from_bytes(old, 'little'):x}" if old is not None else "<unreadable>"
            newv = f"0x{int.from_bytes(new, 'little'):x}" if new is not None else "<unreadable>"
            label = f"Hardware watchpoint {wp.num}: {wp.expr}" if wp else "Hardware watchpoint"
            if wp:
                wp.hits += 1
            return Result(out=text + f"\n{label}\n\nOld value = {oldv}\nNew value = {newv}\n{self._frame_line(self._pc())}\n".encode())
        hit = [b for b in self.breakpoints if b.enabled and b.kind == "breakpoint" and b.addr == self._pc()]
        if hit and stop.kind in ("break", "step"):
            b = hit[0]
            b.hits += 1
            w.events.add("gdb:breakpoint-hit")
            label = "Temporary breakpoint" if b.temp else "Breakpoint"
            if b.temp:
                self.breakpoints.remove(b)
            return Result(out=text + f"\n{label} {b.num}, {self._frame_line(self._pc())}\n".encode())
        return Result(out=text + f"{self._frame_line(self._pc())}\n".encode())

    def _live(self) -> Cpu:
        if self.cpu is None:
            raise GdbError("The program is not being run.")
        return self.cpu

    def _terminated(self) -> Result | None:
        """A process stopped on a signal dies when it is resumed."""
        cpu = self._live()
        if cpu.signal is None:
            return None
        sig = cpu.signal
        self.cpu = None
        what = {"SIGILL": "Illegal instruction", "SIGABRT": "Aborted"}.get(sig, "Segmentation fault")
        return Result(out=f"\nProgram terminated with signal {sig}, {what}.\nThe program no longer exists.\n".encode())

    def c_continue(self, mod: str, rest: str) -> Result:
        self._live()
        return self._terminated() or self._go(lambda: self._cont(), b"Continuing.\n")

    def c_stepi(self, mod: str, rest: str) -> Result:
        self._live()
        return self._terminated() or self._go(lambda: self._live().step())

    def c_nexti(self, mod: str, rest: str) -> Result:
        self._live()
        if (r := self._terminated()):
            return r
        depth = self.cpu.depth  # type: ignore[union-attr]

        def over() -> Stop:
            cpu = self._live()
            stop = cpu.step()
            if stop.kind == "step" and cpu.depth > depth:
                stop = self._cont(depth + 1)  # stepped into a call: run until it returns
            return stop

        return self._go(over)

    def _until_return(self, header: str) -> Result:
        cpu = self._live()
        if (r := self._terminated()):
            return r
        depth = cpu.depth
        return self._go(lambda: self._cont(depth), header.encode())

    def c_finish(self, mod: str, rest: str) -> Result:
        cpu = self._live()
        return self._until_return(f"Run till exit from #0  {self._frame_line(cpu.regs['rip'])}\n")

    def c_next(self, mod: str, rest: str) -> Result:
        cpu = self._live()
        name = self._where(cpu.regs["rip"])
        return self._until_return(f"Single stepping until exit from function {name},\nwhich has no line number information.\n")

    def c_kill(self, mod: str, rest: str) -> Result:
        self._live()
        self.cpu = None
        return ok(f"[Inferior 1 (process {self.pid}) killed]\n")

    # ------------------------------------------------------------ breakpoints
    def _after_prologue(self, fn) -> int:
        """gdb skips `push rbp; mov rbp,rsp` when breaking on a function without line info."""
        i = fn.insns
        if len(i) > 2 and i[0].asm == "push rbp" and i[1].asm == "mov rbp, rsp":
            return i[2].addr
        return fn.addr

    def _resolve(self, spec: str) -> int:
        lv = self._need_level()
        spec = spec.strip()
        if not spec:
            if self.cpu is None:
                raise GdbError("No default breakpoint address now.")
            return self._pc()
        if spec.startswith("*"):
            return self._eval(spec[1:])[0]
        for f in lv.functions:
            if f.name == spec:
                return self._after_prologue(f)
        if spec in lv.imports:
            return lv.imports[spec]
        raise GdbError(f'Function "{spec}" not defined.')

    def _add_bp(self, addr: int, temp: bool = False) -> str:
        self.bp_count += 1
        self.breakpoints.append(Breakpoint(self.bp_count, addr, temp))
        return f"{'Temporary breakpoint' if temp else 'Breakpoint'} {self.bp_count} at 0x{addr:x}\n"

    def c_break(self, mod: str, rest: str, temp: bool = False) -> Result:
        return ok(self._add_bp(self._resolve(rest), temp))

    def c_tbreak(self, mod: str, rest: str) -> Result:
        return self.c_break(mod, rest, temp=True)

    def _select(self, rest: str) -> list[Breakpoint]:
        if not rest.strip():
            return list(self.breakpoints)
        out = []
        for tok in rest.split():
            if not tok.isdigit():
                raise GdbError(f"Bad breakpoint argument: '{tok}'")
            found = [b for b in self.breakpoints if b.num == int(tok)]
            if not found:
                raise GdbError(f"No breakpoint number {tok}.")
            out += found
        return out

    def c_delete(self, mod: str, rest: str) -> Result:
        for b in self._select(rest.replace("breakpoints", "")):
            self.breakpoints.remove(b)
        return Result()

    def c_enable(self, rest: str, on: bool) -> Result:
        for b in self._select(rest.replace("breakpoints", "")):
            b.enabled = on
        return Result()

    def _info_breakpoints(self) -> Result:
        if not self.breakpoints:
            return ok("No breakpoints or watchpoints.\n")
        lines = ["Num     Type           Disp Enb Address            What"]
        for b in self.breakpoints:
            if b.kind == "watch":
                what = f"{'hw watchpoint':<14} {'keep':<4} {'y' if b.enabled else 'n':<3}" + " " * 19 + b.expr
            else:
                what = f"{'breakpoint':<14} {'del' if b.temp else 'keep':<4} {'y' if b.enabled else 'n':<3} 0x{b.addr:016x} {self._sym(b.addr)}"
            lines.append(f"{b.num:<7} {what}")
            if b.hits:
                kind_word = "watchpoint" if b.kind == "watch" else "breakpoint"
                lines.append(f"\t{kind_word} already hit {b.hits} time{'s' if b.hits != 1 else ''}")
        return ok("\n".join(lines) + "\n")

    def c_watch(self, mod: str, rest: str) -> Result:
        if not rest.strip():
            raise GdbError("Argument required (expression to compute).")
        addr = self._eval(rest.strip().lstrip("*"))[0]
        self.bp_count += 1
        self.breakpoints.append(Breakpoint(self.bp_count, addr, kind="watch", size=8, expr=rest.strip()))
        return ok(f"Hardware watchpoint {self.bp_count}: {rest.strip()}\n")

    def c_set(self, mod: str, rest: str) -> Result:
        if rest.lstrip().startswith(("$", "var ", "variable ")):
            raise GdbError("Assigning to registers or memory is not simulated.")
        return Result()

    def c_quit(self, mod: str, rest: str) -> Result:
        self.done = True
        return Result()

    def c_help(self, mod: str, rest: str) -> Result:
        return ok(HELP)

    def c_ignore(self, mod: str, rest: str) -> Result:
        return Result()

    # ----------------------------------------------------------------- memory
    def _peek(self, addr: int, n: int) -> bytes | None:
        if self.cpu is not None:
            return self.cpu.read(addr, n)
        lv = self._need_level()
        off = addr - lv.base
        return self.image[off : off + n] if 0 <= off and off + n <= len(self.image) else None

    def _read(self, addr: int, n: int) -> bytes:
        data = self._peek(addr, n)
        if data is not None:
            return data
        bad = next((addr + i for i in range(n) if self._peek(addr + i, 1) is None), addr)  # first unreadable byte
        raise GdbError(f"Cannot access memory at address 0x{bad:x}")

    def _sym(self, addr: int) -> str:
        """`<vuln+68>` style label, or '' when no symbol covers the address."""
        if self.level is None:
            return ""
        s = self.level.symbol_at(addr)
        if not s:
            return ""
        return f"<{s[0]}+{s[1]}>" if s[1] else f"<{s[0]}>"

    def _labeled(self, addr: int) -> str:
        s = self._sym(addr)
        return f"0x{addr:x} {s}" if s else f"0x{addr:x}"

    # ------------------------------------------------------------- registers
    def _regs(self) -> dict[str, int]:
        if self.cpu is None:
            raise GdbError("The program has no registers now.")
        return self.cpu.regs

    def c_info(self, mod: str, rest: str) -> Result:
        sub, _, arg = rest.partition(" ")
        arg = arg.strip()
        if sub in ("registers", "reg", "r", "regs", "register"):
            return self._info_registers(arg.split())
        if sub in ("functions", "function", "fu", "func"):
            return self._info_functions(arg)
        if sub == "symbol":
            return self._info_symbol(arg)
        if sub in ("breakpoints", "break", "b", "watchpoints", "br"):
            return self._info_breakpoints()
        if sub in ("sharedlibrary", "shared", "dll"):
            return ok("No shared libraries loaded at this time.\n")
        if sub in ("frame", "f", "locals", "args", "line", "source"):
            raise GdbError("No symbol table is loaded.  Use the \"file\" command." if self.level is None else "No frame selected." if not self.alive else "Not supported in this simulation.")
        raise GdbError(f'Undefined info command: "{rest}".  Try "help info".')

    def _info_registers(self, names: list[str]) -> Result:
        regs = self._regs()
        self.shell.world.events.add("gdb:inspected")
        wanted = [n.lstrip("$") for n in names] or REG_ORDER + ["eflags"] + list(SEGS)
        lines = []
        for n in wanted:
            n = REG_ALIAS.get(n, n)
            if n in regs:
                v = regs[n]
                nat = self._labeled(v) if n == "rip" else f"0x{v:x}" if n in POINTER_REGS else str(_s64(v))
                lines.append(f"{n:<15}{'0x%x' % v:<19}{nat}")
            elif n == "eflags":
                lines.append(f"{n:<15}{'0x%x' % EFLAGS:<19}{_flags(EFLAGS)}")
            elif n in SEGS:
                v = SEGS[n]
                lines.append(f"{n:<15}{'0x%x' % v:<19}{v}")
            else:
                raise GdbError(f"Invalid register `{n}'")
        return ok("\n".join(lines) + "\n")

    def _info_functions(self, pattern: str) -> Result:
        lv = self._need_level()
        syms = [(a, f"{n}@plt") for n, a in lv.imports.items()] + [(f.addr, f.name) for f in lv.functions]
        if pattern:
            try:
                rx = re.compile(pattern)
            except re.error:
                raise GdbError(f"Invalid regexp: {pattern}")
            syms = [s for s in syms if rx.search(s[1])]
            head = f'All functions matching regular expression "{pattern}":\n'
        else:
            head = "All defined functions:\n"
        if not syms:
            return ok(head)
        return ok(head + "\nNon-debugging symbols:\n" + "".join(f"0x{a:016x}  {n}\n" for a, n in sorted(syms)))

    def _info_symbol(self, arg: str) -> Result:
        if not arg:
            raise GdbError("Argument required (address).")
        addr, _ = self._eval(arg)
        lv = self._need_level()
        s = lv.symbol_at(addr)
        if not s:
            return ok(f"No symbol matches {arg}.\n")
        name, off = s
        sect = ".plt" if name.endswith("@plt") else ".text"
        return ok(f"{name}{' + %d' % off if off else ''} in section {sect}\n")

    def _unwind(self, rip: int, rsp: int, rbp: int) -> tuple[int, int, int] | None:
        """Caller's (pc, rsp, rbp) from the live stack, as gdb's CFI unwinder would find it."""
        lv = self._need_level()

        def rd(a: int) -> int | None:
            raw = self._peek(a, 8)
            return int.from_bytes(raw, "little") if raw else None

        if rip in lv.imports.values():
            kind = "top"
        else:
            fn = lv.function_containing(rip)
            if fn is None or len(fn.insns) < 3 or fn.insns[0].asm != "push rbp" or fn.insns[1].asm != "mov rbp, rsp":
                return None
            idx = next((i for i, x in enumerate(fn.insns) if x.addr == rip), -1)
            if idx < 0:
                return None
            kind = "top" if idx == 0 or fn.insns[idx].asm == "ret" else "pushed" if idx == 1 else "frame"
        if kind == "top":
            ret = rd(rsp)
            return None if ret is None else (ret, rsp + 8, rbp)
        if kind == "pushed":
            ret, saved = rd(rsp + 8), rd(rsp)
            return None if ret is None or saved is None else (ret, rsp + 16, saved)
        ret, saved = rd(rbp + 8), rd(rbp)
        return None if ret is None or saved is None else (ret, rbp + 16, saved)

    def c_bt(self, mod: str, rest: str) -> Result:
        if self.cpu is None:
            raise GdbError("No stack.")
        r = self.cpu.regs
        rip, rsp, rbp = r["rip"], r["rsp"], r["rbp"]
        lines = []
        for i in range(16):
            lines.append(f"#{i:<3}{self._frame_line(rip)}")
            if self._where(rip) in ("main", "??"):
                break
            nxt = self._unwind(rip, rsp, rbp)
            if nxt is None:
                break
            rip, rsp, rbp = nxt
        return ok("\n".join(lines) + "\n")

    # ------------------------------------------------------------ expressions
    def _eval(self, text: str) -> tuple[int, str]:
        """Returns (value, kind); kind is int | code | ptr | func."""
        toks: list[str] = []
        pos = 0
        pat = re.compile(r"\s*(\$\w+|0[xX][0-9a-fA-F]+|\d+|[A-Za-z_][\w.@]*|[-+*()])")
        while text[pos:].strip():
            m = pat.match(text, pos)
            if not m:
                raise GdbError(f"Invalid character '{text[pos:].strip()[0]}' in expression.")
            toks.append(m.group(1))
            pos = m.end()
        if not toks:
            raise GdbError("Argument required (expression to compute).")
        i = 0

        def peek():
            return toks[i] if i < len(toks) else None

        def atom() -> tuple[int, str]:
            nonlocal i
            t = peek()
            if t is None:
                raise GdbError("A syntax error in expression, near `'.")
            i += 1
            if t == "(":
                v = expr()
                if peek() != ")":
                    raise GdbError("A syntax error in expression, near `'.")
                i += 1
                return v
            if t == "-":
                v, _ = atom()
                return -v, "int"
            if t[0] == "$":
                return self._dollar(t[1:])
            if t[0].isdigit():
                return int(t, 0), "int"
            if t[0].isalpha() or t[0] in "_":
                lv = self.level
                if lv is not None:
                    if t in {f.name for f in lv.functions}:
                        return lv.function(t).addr, "func"
                    if t in lv.imports:
                        return lv.imports[t], "func"
                raise GdbError(f'No symbol "{t}" in current context.' if lv else 'No symbol table is loaded.  Use the "file" command.')
            raise GdbError(f"A syntax error in expression, near `{t}'.")

        def term() -> tuple[int, str]:
            nonlocal i
            v, k = atom()
            while peek() == "*":
                i += 1
                r, _ = atom()
                v, k = v * r, "int"
            return v, k

        def expr() -> tuple[int, str]:
            nonlocal i
            v, k = term()
            while peek() in ("+", "-"):
                op = toks[i]
                i += 1
                r, rk = term()
                if op == "+":
                    k = k if k != "int" else rk if rk != "int" else "int"
                    v += r
                else:
                    k = k if rk == "int" else "int"
                    v -= r
                if k == "func":
                    k = "fptr"  # function + offset is a pointer, not the function itself
            return v, k

        v, k = expr()
        if i != len(toks):
            raise GdbError(f"A syntax error in expression, near `{' '.join(toks[i:])}'.")
        return v & 0xFFFF_FFFF_FFFF_FFFF, k

    def _dollar(self, name: str) -> tuple[int, str]:
        if name.isdigit():
            n = int(name)
            if not 1 <= n <= len(self.history):
                raise GdbError(f"History has not yet reached ${n}.")
            return self.history[n - 1], "int"
        name = REG_ALIAS.get(name, name)
        if name in REG32:  # $eax and friends: low half of the 64-bit register
            if self.cpu is None:
                raise GdbError("No registers.")
            return self.cpu.regs[REG32[name]] & 0xFFFF_FFFF, "int"
        if name in REG_ORDER:
            regs = self.cpu.regs if self.cpu else None
            if regs is None:
                raise GdbError("No registers.")
            kind = "code" if name == "rip" else "ptr" if name in POINTER_REGS else "int"
            return regs[name], kind
        if name == "eflags" and self.cpu:
            return EFLAGS, "int"
        raise GdbError(f"Convenience variable ${name} is void.")

    def c_print(self, mod: str, rest: str) -> Result:
        v, kind = self._eval(rest)
        fmt = (mod[-1:] if mod else "")
        n = len(self.history) + 1
        if fmt in ("x", "o", "t", "d", "u", "c", "a"):
            text = self._fmt_value(v, fmt, 8, pad=False)
        elif kind == "code":
            text = f"(void (*)()) {self._labeled(v)}"
        elif kind == "ptr":
            text = f"(void *) 0x{v:x}"
        elif kind == "func":
            text = f"{{<text variable, no debug info>}} {self._labeled(v)}"
        elif kind == "fptr":
            text = f"(<text variable, no debug info> *) {self._labeled(v)}"
        else:
            text = str(_s64(v))
        self.history.append(v)
        return ok(f"${n} = {text}\n")

    # --------------------------------------------------------------- examine
    def _fmt_value(self, v: int, fmt: str, size: int, pad: bool = True) -> str:
        v &= (1 << (8 * size)) - 1
        signed = v - (1 << (8 * size)) if v >> (8 * size - 1) else v
        if fmt == "x":
            return f"0x{v:0{2 * size}x}" if pad else f"0x{v:x}"
        if fmt == "d":
            return str(signed)
        if fmt == "u":
            return str(v)
        if fmt == "o":
            return f"0{v:o}" if v else "0"
        if fmt == "t":
            return f"{v:0{8 * size}b}" if pad else f"{v:b}"
        if fmt == "c":
            esc = {10: "\\n", 0: "\\000", 9: "\\t", 39: "\\'", 92: "\\\\"}
            c = v & 0xFF
            shown = chr(c) if 32 <= c < 127 and c not in (39, 92) else esc.get(c, f"\\{c:03o}")
            return f"{signed} '{shown}'"
        if fmt == "a":
            return self._labeled(v)
        raise GdbError(f"Undefined output format \"{fmt}\".")

    def c_x(self, mod: str, rest: str) -> Result:
        count, fmt, size = 1, self.fmt, self.size
        m = re.fullmatch(r"(\d*)([a-z]*)", mod)
        if not m:
            raise GdbError(f'Invalid number "{mod}".')
        if m.group(1):
            count = int(m.group(1))
        for ch in m.group(2):
            if ch in SIZES:
                size = SIZES[ch]
            elif ch in "xduotcasi":
                fmt = ch
            else:
                raise GdbError(f'Undefined output format "{ch}".')
        if fmt == "a":
            size = 8
        elif fmt == "c" and not any(ch in SIZES for ch in m.group(2)):
            size = 1
        if self.cpu is not None:
            self.shell.world.events.add("gdb:inspected")
        addr = self._eval(rest)[0] if rest.strip() else self.next_addr
        if not rest.strip() and not self.next_addr:
            raise GdbError("Argument required (starting display address).")
        self.fmt, self.size = fmt, (size if fmt not in "si" else self.size)
        lines: list[str] = []
        if fmt == "s":
            for _ in range(count):
                raw = b""
                while len(raw) < 200:
                    b = self._read(addr + len(raw), 1)
                    if b == b"\0":
                        break
                    raw += b
                text = "".join(chr(c) if 32 <= c < 127 and c not in (34, 92) else {10: "\\n", 9: "\\t", 34: '\\"', 92: "\\\\"}.get(c, f"\\{c:03o}") for c in raw)
                lines.append(f"{self._labeled(addr)}:\t\"{text}\"")
                addr += len(raw) + 1
        elif fmt == "i":
            lv = self._need_level()
            for _ in range(count):
                insn = lv.insn_at(addr)
                if insn is None:
                    self._read(addr, 1)  # raises the proper memory error when unmapped
                    raise GdbError(f"Cannot disassemble at 0x{addr:x} in this simulation.")
                here = self.cpu is not None and addr == self._pc()
                lines.append(f"{'=>' if here else '  '} {self._labeled(addr)}:\t{self._insn_text(insn)}")
                addr += insn.length
        else:
            per = PER_LINE[size]
            for start in range(0, count, per):
                n = min(per, count - start)
                base = addr + start * size
                cells = [self._fmt_value(int.from_bytes(self._read(base + j * size, size), "little"), fmt, size) for j in range(n)]
                lines.append(f"{self._labeled(base)}:\t" + "\t".join(cells))
            addr += count * size
        self.next_addr = addr
        return ok("\n".join(lines) + "\n")

    # ---------------------------------------------------------- disassembly
    def _insn_text(self, insn) -> str:
        lv = self._need_level()
        mn, _, ops = insn.asm.partition(" ")
        ops = ops.replace(", ", ",")
        ops = re.sub(r"(?<![0-9A-Za-z_.])(\d+)(?![0-9A-Za-z_.])", lambda mm: hex(int(mm.group(1))), ops)  # gdb prints 0x0, [rbp-0x8]
        comment = ""
        fn = lv.function_containing(insn.addr)
        if fn:
            for decl in fn.locals:  # "var char *s @ rbp-0x40"
                m = re.match(r"var .*?(\w+) @ (rbp[-+]0x[0-9a-f]+)", decl)
                if m:
                    ops = ops.replace(f"[{m.group(1)}]", f"[{m.group(2)}]")
        m = re.search(r"sym\.imp\.(\w+)", ops)
        if m and m.group(1) in lv.imports:
            ops = f"0x{lv.imports[m.group(1)]:x} <{m.group(1)}@plt>"
        else:
            m = re.search(r"sym\.(\w+)", ops)
            if m:
                try:
                    ops = f"0x{lv.function(m.group(1)).addr:x} <{m.group(1)}>"
                except KeyError:
                    pass
        ops = re.sub(r"\b(byte|word|dword|qword) \[", lambda mm: mm.group(1).upper() + " PTR [", ops)
        if mn.startswith("j") and re.fullmatch(r"0x[0-9a-f]+", ops):
            label = self._sym(int(ops, 16))
            ops = f"{ops} {label}" if label else ops
        m = re.search(r"\bstr\.\S+", ops)
        if m and insn.comment:
            target = int(insn.comment, 16)
            disp = target - (insn.addr + insn.length)
            ops = ops.replace(m.group(0), f"[rip{'+' if disp >= 0 else '-'}0x{abs(disp):x}]")
            comment = f"        # {insn.comment}"
        return (f"{mn:<6} {ops}" if ops else mn) + comment

    def c_disas(self, mod: str, rest: str) -> Result:
        lv = self._need_level()
        if rest.strip():
            addr = self._eval(rest)[0]
        elif self.cpu:
            addr = self._pc()
        else:
            raise GdbError("No frame selected.")
        fn = lv.function_containing(addr)
        if fn is None:
            raise GdbError("No function contains specified address." if rest.strip() else "No function contains program counter for selected frame.")
        raw = "r" in mod
        lines = [f"Dump of assembler code for function {fn.name}:"]
        for insn in fn.insns:
            here = self.cpu is not None and insn.addr == self._pc()
            hexb = " ".join(insn.raw[i : i + 2] for i in range(0, len(insn.raw), 2)) + "\t" if raw else ""
            lines.append(f"{'=>' if here else '  '} 0x{insn.addr:016x} <+{insn.addr - fn.addr}>:\t{hexb}{self._insn_text(insn)}")
        lines.append("End of assembler dump.")
        return ok("\n".join(lines) + "\n")


@command("gdb")
def gdb(sh: Shell, args: list[str], stdin: bytes) -> Result:
    if stdin or not sh.tty:
        return fail("gdb: cannot start an interactive session here (no terminal)")
    quiet = any(a in ("-q", "-quiet", "--quiet", "-silent", "-nx", "-n") for a in args)
    files = [a for a in args if not a.startswith("-")]
    head = "" if quiet else BANNER
    if not files:
        s = GdbSession(sh, None, "", quiet)
        s.startup = ok(head)
        return Result(session=s)
    name = files[0]
    try:
        node = sh.vfs.get(name, sh.cwd)
    except VFSError:
        s = GdbSession(sh, None, "", quiet)
        s.startup = Result(out=head.encode(), err=f"{name}: No such file or directory.\n".encode())
        return Result(session=s)
    path = sh.abspath(name)
    if node.is_dir or not sh.vfs.can_read(node):
        s = GdbSession(sh, None, "", quiet)
        s.startup = Result(out=head.encode(), err=f"{name}: {'Is a directory.' if node.is_dir else 'Permission denied.'}\n".encode())
        return Result(session=s)
    if not node.data.startswith(b"\x7fELF"):
        s = GdbSession(sh, None, "", quiet)
        s.startup = Result(out=head.encode(), err=f'"{path}": not in executable format: file format not recognized\n'.encode())
        return Result(session=s)
    level = sh.world.level_for(node)
    s = GdbSession(sh, level, path, quiet, suid=bool(node.mode & 0o4000))
    s.startup = ok(f"{head}Reading symbols from {name}...\n(No debugging symbols found in {name})\n")
    return Result(session=s)
