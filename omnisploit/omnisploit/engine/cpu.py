"""A tiny x86-64 stepper for the level binaries (never touches the host).

It understands exactly the instructions the levels use (push pop mov add sub lea cmp test xor
movzx, the jcc family, call -- direct or indirect through memory -- ret leave nop) and models
libc calls (printf, gets, scanf, strcmp, puts, system, __stack_chk_fail) as atomic PLT stubs.
printf supports the conversions a format-string bug actually needs: %s %x %p %d %c and the write
primitive %n/%hn, with positional %N$ arguments pulled from the argument registers and then the
caller's own stack. Memory is the binary image plus one stack window. Because it executes real
instructions against a real (overflowable, writable) stack, ``ret`` to a smashed address, ROP
into the middle of a function, a stack canary that actually has to be preserved, and a dispatch
table entry overwritten through a format string, all behave naturally -- and a debugger can stop
between any two instructions, or the instant a watched memory location changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .machine import CANONICAL_LIMIT, Crash, Level, p64, u64

M64 = 0xFFFF_FFFF_FFFF_FFFF
LIBC_RET = 0x7FFFF7C29D90  # main returns here (__libc_start_call_main)
MAX_STEPS = 20000
REG64 = ["rax", "rbx", "rcx", "rdx", "rsi", "rdi", "rbp", "rsp"] + [f"r{i}" for i in range(8, 16)]
REG32 = {"eax": "rax", "ebx": "rbx", "ecx": "rcx", "edx": "rdx", "esi": "rsi", "edi": "rdi", "ebp": "rbp", "esp": "rsp"}
# what libc leaves behind in the caller-saved registers
CLOBBER = {"rcx": 0x7FFFF7E9A5A0, "rdx": 0, "rsi": 0x4052A0, "rdi": 0x7FFFF7F9B7E0,
           "r8": 0, "r9": 0x7C, "r10": 0x7FFFF7DE5AE8, "r11": 0x246}


MEM = re.compile(r"(?:(byte|word|dword|qword) )?\[(r\w+)\s*(?:([-+])\s*(0x[0-9a-f]+|\d+))?\]")
FMT_SPEC = re.compile(rb"%(?:(\d+)\$)?([0-9#.*lhLqjzt]*)([sdiouxXpcn%])")
ARG_REGS = ("rsi", "rdx", "rcx", "r8", "r9")
MEM_SIZES = {"byte": 1, "word": 2, "dword": 4, "qword": 8}
JUMPS = {
    "jmp": lambda c: True, "je": lambda c: c.zf, "jz": lambda c: c.zf, "jne": lambda c: not c.zf, "jnz": lambda c: not c.zf,
    "jl": lambda c: c.sf, "jge": lambda c: not c.sf, "jg": lambda c: not c.zf and not c.sf, "jle": lambda c: c.zf or c.sf,
    "jb": lambda c: c.cf, "jae": lambda c: not c.cf,
}


class _MemFault(Exception):
    pass


@dataclass
class Stop:
    kind: str  # step | break | input | exit | fault


class Cpu:
    def __init__(self, level: Level, stdin: bytes | None, suid: bool = False) -> None:
        st = level.stack
        self.level = level
        below = 0x200  # room under the buffer for pushes and call frames
        self.base = st["buffer_addr"] - below  # lowest mapped stack address
        self.mem = bytearray(st["size"] + below)
        self.image = level.build_elf()
        self.plt = {a: n for n, a in level.imports.items()}
        main = level.function("main")
        self.regs: dict[str, int] = {r: 0 for r in REG64}
        self.regs.update(CLOBBER)
        self.regs.update(r12=0x7FFFFFFFE0F8, r13=main.addr, r15=0x7FFFF7FFD040,
                         rbp=1, rsp=st["saved_rbp"] + 8, rip=main.addr)
        self.write(self.regs["rsp"], p64(LIBC_RET))
        self.out = bytearray()
        self.depth = 0  # call depth relative to main
        self.solved = False
        self.exit_code: int | None = None
        self.signal: str | None = None
        self.interactive = stdin is None
        self.inbuf = bytearray(stdin or b"")
        self.gets_calls = 0
        self.watch_hit: tuple[int, int, bytes | None, bytes | None] | None = None
        self.suid = suid  # the VFS file's own setuid bit; some levels' system() checks this
        self.zf = self.sf = self.cf = False

    # ----------------------------------------------------------------- memory
    def read(self, addr: int, n: int) -> bytes | None:
        off = addr - self.base
        if 0 <= off and off + n <= len(self.mem):
            return bytes(self.mem[off : off + n])
        off = addr - self.level.base
        if 0 <= off and off + n <= len(self.image):
            return self.image[off : off + n]
        return None

    def write(self, addr: int, data: bytes) -> bool:
        off = addr - self.base
        if 0 <= off and off + len(data) <= len(self.mem):
            self.mem[off : off + len(data)] = data
            return True
        return False

    def cstring(self, addr: int) -> bytes | None:
        raw = b""
        while len(raw) < 4096:
            b = self.read(addr + len(raw), 1)
            if b is None:
                return None
            if b == b"\0":
                return raw
            raw += b
        return raw

    def take_output(self) -> bytes:
        data = bytes(self.out)
        self.out.clear()
        return data

    def give_input(self, line: bytes) -> None:
        self.inbuf += line + b"\n"

    # --------------------------------------------------------------- operands
    def _mem(self, op: str, other: str = "") -> tuple[int, int]:
        """(address, size) of a memory operand; size comes from the prefix or the register it pairs with."""
        m = MEM.fullmatch(op)
        if not m:
            raise ValueError(op)
        size = MEM_SIZES.get(m.group(1) or "", 4 if other in REG32 else 8)
        off = int(m.group(4), 0) * (-1 if m.group(3) == "-" else 1) if m.group(4) else 0
        return (self.regs[m.group(2)] + off) & M64, size

    def _flags(self, result: int, bits: int, a: int = 0, b: int = 0, sub: bool = False) -> None:
        mask = (1 << bits) - 1
        r = result & mask
        self.zf, self.sf = r == 0, bool(r >> (bits - 1))
        self.cf = (a & mask) < (b & mask) if sub else False

    def _bits(self, op: str) -> int:
        m = MEM.fullmatch(op)
        if m and m.group(1):
            return MEM_SIZES[m.group(1)] * 8
        return 32 if op in REG32 else 64

    def _val(self, op: str, other: str = "") -> int:
        if "[" in op:
            addr, size = self._mem(op, other)
            raw = self.read(addr, size)
            if raw is None:
                raise _MemFault()
            return int.from_bytes(raw, "little")
        if op in REG32:
            return self.regs[REG32[op]] & 0xFFFF_FFFF
        if op in self.regs:
            return self.regs[op]
        return int(op, 0) & M64

    def _set(self, op: str, v: int, other: str = "") -> None:
        if "[" in op:
            addr, size = self._mem(op, other)
            if not self.write(addr, (v & ((1 << (8 * size)) - 1)).to_bytes(size, "little")):
                raise _MemFault()
        elif op in REG32:
            self.regs[REG32[op]] = v & 0xFFFF_FFFF
        elif op in self.regs and op != "rip":
            self.regs[op] = v & M64
        else:
            raise ValueError(op)

    def _lea(self, insn, op: str) -> int:
        fn = self.level.function_containing(insn.addr)
        m = re.fullmatch(r"\[(\w+)\]", op)
        if m and fn:
            for decl in fn.locals:  # "var char *s @ rbp-0x40"
                d = re.match(r"var .*?(\w+) @ (r\w+)([-+])(0x[0-9a-f]+)", decl)
                if d and d.group(1) == m.group(1):
                    off = int(d.group(4), 16) * (-1 if d.group(3) == "-" else 1)
                    return (self.regs[d.group(2)] + off) & M64
        m = re.fullmatch(r"\[(r\w+)\s*([-+])\s*(0x[0-9a-f]+|\d+)\]", op)
        if m:
            return (self.regs[m.group(1)] + int(m.group(3), 0) * (-1 if m.group(2) == "-" else 1)) & M64
        if insn.comment:
            return int(insn.comment, 16)
        raise ValueError(op)

    # ---------------------------------------------------------------- control
    def _fault(self, signal: str = "SIGSEGV") -> Stop:
        self.signal = signal
        return Stop("fault")

    def _push(self, v: int) -> bool:
        sp = (self.regs["rsp"] - 8) & M64
        if not self.write(sp, p64(v)):
            return False
        self.regs["rsp"] = sp
        return True

    def _do_ret(self) -> Stop | None:
        raw = self.read(self.regs["rsp"], 8)
        if raw is None:
            return self._fault()
        target = u64(raw)
        if target >= CANONICAL_LIMIT:
            return self._fault()  # #GP is raised at the ret itself; nothing is popped
        self.regs["rsp"] = (self.regs["rsp"] + 8) & M64
        self.regs["rip"] = target
        self.depth -= 1
        return None

    def executable(self, addr: int) -> bool:
        return addr == LIBC_RET or addr in self.plt or self.level.insn_at(addr) is not None

    def _exec_one(self) -> Stop | None:
        r = self.regs
        rip = r["rip"]
        if rip == LIBC_RET:
            self.exit_code = r["rax"] & 0xFF
            return Stop("exit")
        if rip in self.plt:
            return self._libc(self.plt[rip])
        insn = self.level.insn_at(rip)
        if insn is None:
            return self._fault()
        mn, _, rest = insn.asm.partition(" ")
        ops = [o.strip() for o in rest.split(",")] if rest else []
        nxt = rip + insn.length
        try:
            if mn == "push":
                if not self._push(self._val(ops[0])):
                    return self._fault()
            elif mn == "pop":
                raw = self.read(r["rsp"], 8)
                if raw is None:
                    return self._fault()
                r["rsp"] = (r["rsp"] + 8) & M64
                self._set(ops[0], u64(raw))
            elif mn == "mov":
                self._set(ops[0], self._val(ops[1], ops[0]), ops[1])
            elif mn in ("add", "sub"):
                a, b, bits = self._val(ops[0], ops[1]), self._val(ops[1], ops[0]), self._bits(ops[0])
                res = a + b if mn == "add" else a - b
                self._set(ops[0], res, ops[1])
                self._flags(res, bits, a, b, sub=mn == "sub")
            elif mn == "movzx":
                self._set(ops[0], self._val(ops[1], ops[0]), ops[1])
            elif mn == "cmp":
                a, b = self._val(ops[0], ops[1]), self._val(ops[1], ops[0])
                self._flags(a - b, self._bits(ops[0]), a, b, sub=True)
            elif mn == "test":
                self._flags(self._val(ops[0]) & self._val(ops[1]), self._bits(ops[0]))
            elif mn == "xor":
                res = self._val(ops[0], ops[1]) ^ self._val(ops[1], ops[0])
                self._set(ops[0], res, ops[1])
                self._flags(res, self._bits(ops[0]))
            elif mn in JUMPS:
                if JUMPS[mn](self):
                    r["rip"] = int(ops[0], 0)
                    return None
            elif mn == "lea":
                self._set(ops[0], self._lea(insn, ops[1]))
            elif mn == "nop":
                pass
            elif mn == "leave":
                raw = self.read(r["rbp"], 8)
                if raw is None:
                    return self._fault()
                r["rsp"] = (r["rbp"] + 8) & M64
                r["rbp"] = u64(raw)
            elif mn == "ret":
                return self._do_ret()
            elif mn == "call":
                if "[" in ops[0]:
                    # an indirect call through memory (a GOT-style dispatch table): whatever is
                    # stored there right now is where execution goes, tampered with or not
                    target = self._val(ops[0])
                else:
                    m = re.fullmatch(r"sym\.(imp\.)?(\w+)", ops[0])
                    if not m:
                        raise ValueError(ops[0])
                    target = self.level.imports[m.group(2)] if m.group(1) else self.level.function(m.group(2)).addr
                if not self._push(nxt):
                    return self._fault()
                r["rip"] = target
                self.depth += 1
                return None
            else:
                return self._fault("SIGILL")
        except _MemFault:
            return self._fault()
        except (ValueError, KeyError, IndexError):
            return self._fault("SIGILL")
        r["rip"] = nxt
        return None

    # ------------------------------------------------------------ printf
    def _printf_arg(self, index: int, call_rsp: int) -> int | None:
        """The value a variadic argument slot holds: the 5 integer argument registers, then the
        caller's stack right above the (already-pushed) return address, 8 bytes at a time."""
        if index < len(ARG_REGS):
            return self.regs[ARG_REGS[index]]
        stack_index = index - len(ARG_REGS)
        raw = self.read(call_rsp + 8 * (stack_index + 1), 8)
        return u64(raw) if raw is not None else None

    def _printf_format(self, fmt: bytes, call_rsp: int) -> bytes | None:
        """Render a printf format string against the (possibly nonexistent) arguments the call
        site actually passed. A format string under attacker control, with no matching arguments,
        is exactly the format-string vulnerability: whatever is left in the argument slots -- other
        registers, or memory further up the stack -- comes out, in place of real arguments.
        """
        out = bytearray()
        auto_idx = 0
        pos = 0
        for m in FMT_SPEC.finditer(fmt):
            out += fmt[pos : m.start()]
            pos = m.end()
            posnum, mods, conv = m.group(1), m.group(2).decode(), m.group(3).decode()
            if conv == "%":
                out += b"%"
                continue
            if posnum:
                idx = int(posnum) - 1
            else:
                idx = auto_idx
                auto_idx += 1
            length = "ll" if mods.endswith("ll") else (mods[-1] if mods and mods[-1] in "lhLqjzt" else "")
            width_flags = mods[: len(mods) - len(length)]
            zero_pad = width_flags.startswith("0")
            width_digits = width_flags.lstrip("0#.")
            width = int(width_digits) if width_digits.isdigit() else 0
            wide = length in ("l", "ll", "q", "z", "j")
            value = self._printf_arg(idx, call_rsp)
            if value is None:
                return None
            if conv == "s":
                text = self.cstring(value)
                if text is None:
                    return None
                out += text
                continue
            if conv == "c":
                out += bytes([value & 0xFF])
                continue
            if conv == "p":
                out += f"0x{value & M64:x}".encode()
                continue
            if conv == "n":
                # the real exploitation primitive: writes the number of bytes printed so far to
                # the address this argument holds. %hn writes 2 bytes, %n/%ln writes 4 or 8.
                nsize = 2 if length == "h" else (8 if wide else 4)
                if not self.write(value, (len(out) & ((1 << (8 * nsize)) - 1)).to_bytes(nsize, "little")):
                    return None
                continue
            mask = M64 if wide else 0xFFFFFFFF
            bits = 64 if wide else 32
            v = value & mask
            if conv in "di":
                text = str(v - (1 << bits) if v >> (bits - 1) else v)
            elif conv == "u":
                text = str(v)
            elif conv == "o":
                text = f"{v:o}"
            else:  # x, X
                text = f"{v:x}" if conv == "x" else f"{v:X}"
            if width:
                text = text.rjust(width, "0" if zero_pad else " ")
            out += text.encode()
        out += fmt[pos:]
        return bytes(out)

    def _libc(self, name: str) -> Stop | None:
        r = self.regs
        ret = 0
        if name == "gets":
            dst = r["rdi"]
            if self.inbuf:
                nl = self.inbuf.find(b"\n")
                line = bytes(self.inbuf[:nl] if nl >= 0 else self.inbuf)
                del self.inbuf[: nl + 1 if nl >= 0 else len(self.inbuf)]
            elif self.interactive:
                return Stop("input")
            elif self.gets_calls == 0:
                line = b""  # nothing was piped in: behaves like an empty line
            else:
                line = None  # EOF: gets() returns NULL and leaves the buffer alone
            self.gets_calls += 1
            if line is not None:
                room = self.base + len(self.mem) - dst
                if not 0 <= dst - self.base < len(self.mem):
                    return self._fault()
                self.write(dst, (line + b"\0")[:room])  # no bounds check, like the real thing
                ret = dst
        elif name == "printf":
            fmt = self.cstring(r["rdi"])
            if fmt is None:
                return self._fault()
            text = self._printf_format(fmt, r["rsp"])
            if text is None:
                return self._fault()
            self.out += text
            ret = len(text)
            if self.level.flag.encode() in self.out:
                self.solved = True  # the flag can also leak straight out of a vulnerable printf
        elif name == "scanf":
            fmt = self.cstring(r["rdi"])
            m = re.fullmatch(rb"%(\d*)s|%d", fmt or b"")
            if m is None:
                return self._fault("SIGILL" if fmt is not None else "SIGSEGV")
            while self.inbuf and chr(self.inbuf[0]).isspace():
                del self.inbuf[0]
            if not self.inbuf:
                if self.interactive:
                    return Stop("input")
                ret = 0xFFFFFFFF  # EOF
            elif fmt == b"%d":
                end = 0
                while end < len(self.inbuf) and not chr(self.inbuf[end]).isspace():
                    end += 1
                try:
                    value = int(bytes(self.inbuf[:end]))
                except ValueError:
                    value = None  # not a number: scanf stops and leaves the input alone
                if value is None:
                    ret = 0
                else:
                    del self.inbuf[:end]
                    if not self.write(r["rsi"], (value & 0xFFFFFFFF).to_bytes(4, "little")):
                        return self._fault()
                    ret = 1
            else:
                width = int(m.group(1)) if m.group(1) else 4096
                end = 0
                while end < len(self.inbuf) and end < width and not chr(self.inbuf[end]).isspace():
                    end += 1
                token = bytes(self.inbuf[:end])
                del self.inbuf[:end]
                if not self.write(r["rsi"], token + b"\0"):
                    return self._fault()
                ret = 1
        elif name == "strcmp":
            a, b = self.cstring(r["rdi"]), self.cstring(r["rsi"])
            if a is None or b is None:
                return self._fault()
            ret = ((a > b) - (a < b)) & 0xFFFFFFFF
        elif name == "puts":
            text = self.cstring(r["rdi"])
            if text is None:
                return self._fault()
            self.out += text + b"\n"
            ret = len(text) + 1
        elif name == "system":
            cmd = self.cstring(r["rdi"])
            if cmd is None:
                return self._fault()
            if cmd == b"cat " + self.level.flag_path.encode() and (not self.level.requires_suid or self.suid):
                self.out += self.level.flag.encode() + b"\n"
                self.solved = True
        elif name == "__stack_chk_fail":
            return self._fault("SIGABRT")  # the canary didn't match: real glibc aborts, never returns
        r.update(CLOBBER)
        r["rax"] = ret
        return self._do_ret()

    # ---------------------------------------------------------------- running
    def step(self) -> Stop:
        return self._exec_one() or Stop("step")

    def run(self, breakpoints=frozenset(), depth_lt: int | None = None, watches=()) -> Stop:
        """Run until a breakpoint address is reached (before executing it), a watched memory
        location's value changes (after the instruction that changed it), input is needed, the
        process exits or faults, or the call depth drops below ``depth_lt``.

        ``watches`` is an iterable of (addr, size) pairs; a hit is reported via ``self.watch_hit``
        as (addr, size, old_bytes, new_bytes).
        """
        first = True  # never re-trigger a breakpoint on the instruction we are resuming from
        watch_vals = [(addr, size, self.read(addr, size)) for addr, size in watches]
        for _ in range(MAX_STEPS):
            if not first and self.regs["rip"] in breakpoints:
                return Stop("break")
            first = False
            stop = self._exec_one()
            if stop:
                return stop
            for i, (addr, size, old) in enumerate(watch_vals):
                new = self.read(addr, size)
                if new != old:
                    self.watch_hit = (addr, size, old, new)
                    return Stop("watch")
                watch_vals[i] = (addr, size, new)
            if depth_lt is not None and self.depth < depth_lt:
                # a step-over/finish returned: if we landed somewhere unmapped, the CPU faults first
                if not self.executable(self.regs["rip"]):
                    return self._fault()
                return Stop("step")
        self.exit_code = 0  # runaway: give up quietly
        return Stop("exit")

    def crash(self) -> Crash:
        rip = self.regs["rip"]
        fn = self.level.function_containing(rip)
        return Crash(self.signal or "SIGSEGV", rip, self.regs["rsp"], self.regs["rbp"], dict(self.regs),
                     self.base, bytes(self.mem), fn.name if fn else None)
