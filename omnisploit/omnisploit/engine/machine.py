"""The abstract machine: level data model plus the vulnerable-program simulation.

A "binary" in omnisploit is data (``levels/*.toml``). The :class:`Process` class
interprets just enough of its behaviour (stack layout, mitigations, return
dispatch) to make exploit development feel real, without ever executing
anything on the host.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

LEVEL_DIR = Path(__file__).resolve().parent.parent / "levels"
CANONICAL_LIMIT = 0x0000_8000_0000_0000


def u64(b: bytes | bytearray) -> int:
    return int.from_bytes(bytes(b[:8]).ljust(8, b"\0"), "little")


def p64(v: int) -> bytes:
    return (v & 0xFFFF_FFFF_FFFF_FFFF).to_bytes(8, "little")


# ---------------------------------------------------------------- data model
@dataclass
class Insn:
    addr: int
    raw: str  # hex bytes
    text: str  # "mov rbp, rsp" or "lea rdi, str.x ; 0x402004"

    @property
    def length(self) -> int:
        return len(self.raw) // 2

    @property
    def asm(self) -> str:
        return self.text.split(" ; ", 1)[0]

    @property
    def comment(self) -> str:
        parts = self.text.split(" ; ", 1)
        return parts[1] if len(parts) > 1 else ""


@dataclass
class Function:
    name: str
    addr: int
    insns: list[Insn]
    locals: list[str] = field(default_factory=list)

    @property
    def end(self) -> int:
        last = self.insns[-1]
        return last.addr + last.length

    @property
    def size(self) -> int:
        return self.end - self.addr

    @property
    def ret_addr(self) -> int:
        return self.insns[-1].addr


@dataclass
class Level:
    id: str
    title: str
    flag: str
    flag_path: str
    prompt_text: str
    binary_path: str
    base: int
    build_id: str
    mitigations: dict[str, object]
    stack: dict[str, int]
    imports: dict[str, int]
    strings: dict[int, str]
    functions: list[Function]
    kind: str = ""  # stack_overflow, crackme, ...
    readme: str = ""  # README.txt dropped next to the binary (empty: the world supplies one)
    requires_suid: bool = False  # win()'s system() call only succeeds if the binary itself is SUID
    remote_only: bool = False  # not installed on the player's own box; placed by a Host's level_id instead

    # -- lookups
    def function(self, name: str) -> Function:
        for f in self.functions:
            if f.name == name:
                return f
        raise KeyError(name)

    def function_containing(self, addr: int) -> Function | None:
        for f in self.functions:
            if f.addr <= addr < f.end:
                return f
        return None

    @property
    def symbols(self) -> dict[str, int]:
        syms = {f.name: f.addr for f in self.functions}
        syms.update({f"imp.{k}": v for k, v in self.imports.items()})
        return syms

    def symbol_at(self, addr: int) -> tuple[str, int] | None:
        f = self.function_containing(addr)
        if f:
            return f.name, addr - f.addr
        for name, a in self.imports.items():
            if a == addr:
                return f"{name}@plt", 0
        return None

    def insn_at(self, addr: int) -> Insn | None:
        for f in self.functions:
            for i in f.insns:
                if i.addr == addr:
                    return i
        return None

    # -- file image (so xxd/strings/r2 agree with each other)
    def build_elf(self) -> bytes:
        img = bytearray(0x2100)
        hdr = bytearray(64)
        hdr[0:4] = b"\x7fELF"
        hdr[4:7] = bytes([2, 1, 1])
        hdr[16:18] = (2).to_bytes(2, "little")
        hdr[18:20] = (0x3E).to_bytes(2, "little")
        hdr[20:24] = (1).to_bytes(4, "little")
        hdr[24:32] = p64(self.functions[0].addr - 0x136)
        hdr[32:40] = p64(64)
        hdr[52:54] = (64).to_bytes(2, "little")
        hdr[54:56] = (56).to_bytes(2, "little")
        img[0:64] = hdr
        for f in self.functions:
            for i in f.insns:
                off = i.addr - self.base
                img[off : off + i.length] = bytes.fromhex(i.raw)
        for addr, text in self.strings.items():
            off = addr - self.base
            img[off : off + len(text) + 1] = text.encode() + b"\0"
        img[0x20C0 : 0x20C0 + 30] = b"GCC: (Ubuntu 12.3.0) 12.3.0\0\0\0"
        return bytes(img)


def _load_function(raw: dict) -> Function:
    insns = []
    for line in raw["insns"]:
        addr_s, raw_hex, text = line.split(" ", 2)
        insns.append(Insn(int(addr_s, 16), raw_hex, text))
    return Function(raw["name"], raw["addr"], insns, raw.get("locals", []))


def load_level(path: Path) -> Level:
    with open(path, "rb") as fh:
        d = tomllib.load(fh)
    b = d["binary"]
    return Level(
        id=d["id"],
        title=d["title"],
        flag=d["flag"],
        flag_path=d["flag_path"],
        prompt_text=d["prompt_text"],
        binary_path=b["path"],
        base=b["base"],
        build_id=b["build_id"],
        mitigations=d["mitigations"],
        stack=d["stack"],
        imports=d["imports"],
        strings={s["addr"]: s["text"] for s in d["strings"]},
        functions=[_load_function(f) for f in d["functions"]],
        kind=d.get("vuln", ""),
        readme=d.get("readme", ""),
        requires_suid=d.get("requires_suid", False),
        remote_only=d.get("remote_only", False),
    )


def load_all_levels() -> dict[str, Level]:
    levels = {}
    for p in sorted(LEVEL_DIR.glob("*.toml")):
        lv = load_level(p)
        levels[lv.id] = lv
    return levels


# --------------------------------------------------------------- simulation
@dataclass
class Crash:
    signal: str
    rip: int
    rsp: int
    rbp: int
    regs: dict[str, int]
    stack_base: int
    stack: bytes
    func: str | None  # function name gdb would print ("in vuln ()"), None -> "??"


@dataclass
class RunResult:
    stdout: bytes
    exit_code: int | None  # None when killed by a signal
    crash: Crash | None = None
    solved: bool = False

    @property
    def returncode(self) -> int:
        """Python/pwntools convention: negative signal number on a crash."""
        if self.crash:
            return -11
        return self.exit_code or 0


class Process:
    """One run of a level binary: prints a prompt, then consumes one line."""

    def __init__(self, level: Level, pid: int = 4242, suid: bool = False) -> None:
        self.level = level
        self.pid = pid
        self.suid = suid  # the VFS file's own setuid bit, as seen from the shell that launched it

    @property
    def banner(self) -> bytes:
        return self.level.prompt_text.encode()

    def read_memory(self, crash: Crash | None, addr: int, n: int) -> bytes | None:
        lv = self.level
        if crash and crash.stack_base <= addr and addr + n <= crash.stack_base + len(crash.stack):
            off = addr - crash.stack_base
            return crash.stack[off : off + n]
        img = lv.build_elf()
        off = addr - lv.base
        if 0 <= off and off + n <= len(img):
            return img[off : off + n]
        return None

    def start(self, stdin: bytes | None = None):
        """A fresh CPU for this level; ``stdin=None`` makes ``gets`` ask for input interactively."""
        from .cpu import Cpu  # local import: cpu builds on this module's data model

        return Cpu(self.level, stdin, suid=self.suid)

    def feed(self, data: bytes) -> RunResult:
        """Run to completion with ``data`` as stdin."""
        cpu = self.start(data)
        stop = cpu.run()
        out = bytes(cpu.out)
        if out.startswith(self.banner):
            out = out[len(self.banner) :]  # callers print the prompt themselves
        if stop.kind == "fault":
            return RunResult(out, None, cpu.crash(), cpu.solved)
        return RunResult(out, cpu.exit_code or 0, None, cpu.solved)
