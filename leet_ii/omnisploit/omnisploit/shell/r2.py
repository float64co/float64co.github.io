"""A simulated radare2 (static analysis only; the debugger is `gdb`).

Reads the same ELF image that ``xxd`` and ``strings`` see, so every address, byte
and string agrees across tools. Supported: seeking, disassembly, hexdumps, strings,
imports, symbols, binary info, analysis (aa/aaa/af), function list, cross references,
a tiny expression calculator, ``;`` command chaining and ``~grep`` filters.
"""

from __future__ import annotations

import re

from ..engine.machine import Insn, Level
from ..engine.vfs import VFSError
from .commands import command
from .shell import Result, Session, Shell, fail, ok

SIGS = {
    "printf": "int printf(const char *format)",
    "gets": "char *gets(char *s)",
    "system": "int system(const char *command)",
    "scanf": "int scanf(const char *format)",
    "strcmp": "int strcmp(const char *s1, const char *s2)",
    "puts": "int puts(const char *s)",
}
AA_LINES = """\
[x] Analyze all flags starting with sym. and entry0 (aa)
[x] Analyze function calls (aac)
[x] Analyze len bytes of instructions for references (aar)
[x] Check for objc references
[x] Check for vtables
[x] Type matching analysis for all functions (aaft)
[x] Propagate noreturn information
[x] Use -AA or aaaa to perform additional experimental analysis.
"""
HELP = """\
Usage: [.][times][cmd][~grep][@[@iter]addr!size][|>pipe] ; ...
| s [addr]          seek (s+N, s-N; no argument prints the offset)
| aa, aaa, af       analyze everything / analyze the function at the seek
| afl               list functions (after analysis)
| pdf [@ addr]      disassemble the function at the seek
| pd [N] [@ addr]   disassemble N instructions
| px [N] [@ addr]   hexdump N bytes   | p8 [N]   plain hex   | psz   C string
| iz, izz           strings        | ii imports   | is symbols
| iI, i, ie         binary info    | ie entry points
| axt addr          cross references to addr (after analysis)
| ?v expr, ? expr   evaluate (flags like sym.vuln and entry0 work; + and -)
| q                 quit
Add ~text to any command to keep only matching lines.
"""
NAMES = ["aaaa", "aaa", "aa", "afl", "af", "axt", "pdf", "pd", "px", "p8", "psz", "izz", "iz", "ii", "iI", "ie",
         "is", "i", "s+", "s-", "s", "?v", "?", "q", "e", "w", "V", "!"]


class R2Error(Exception):
    pass


def _sanitize(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.]", "_", text.rstrip())


class R2Session(Session):
    def __init__(self, shell: Shell, level: Level | None, data: bytes, path: str) -> None:
        self.shell = shell
        self.level = level
        self.path = path
        self.image = level.build_elf() if level else data
        self.base = level.base if level else 0
        self.entry = (level.functions[0].addr - 0x136) if level else 0
        self.seek = self.entry
        self.known: set[int] = set()  # function addresses analysis has discovered
        self.startup = Result()
        self.flags: dict[str, int] = {}
        if level:
            self.flags["entry0"] = self.entry
            for f in level.functions:
                self.flags[self._fname(f.name)] = f.addr
                if f.name == "main":
                    self.flags["sym.main"] = f.addr
            for n, a in level.imports.items():
                self.flags[f"sym.imp.{n}"] = a
            for a, t in level.strings.items():
                self.flags[f"str.{_sanitize(t)}"] = a
        self._names = {a: n for n, a in self.flags.items() if n != "sym.main"}
        self._update_prompt()

    # --------------------------------------------------------------- plumbing
    def _update_prompt(self) -> None:
        self.prompt = f"[0x{self.seek:08x}]> "

    def banner(self) -> Result:
        return self.startup

    @staticmethod
    def _fname(name: str) -> str:
        return name if name == "main" else f"sym.{name}"

    def handle(self, line: str) -> Result:
        out: list[str] = []
        err: list[str] = []
        for part in line.split(";"):
            part = part.strip()
            if not part:
                continue
            try:
                out.append(self._run_one(part))
            except R2Error as e:
                err.append(str(e) + "\n")
            if self.done:
                break
        self._update_prompt()
        return Result(out="".join(out).encode(errors="replace"), err="".join(err).encode(errors="replace"))

    def _run_one(self, text: str) -> str:
        grep = None
        if "~" in text:
            text, _, grep = text.partition("~")
            text = text.strip()
        tmp = None
        if "@" in text:
            text, _, at = text.partition("@")
            tmp = self._eval(at.strip())
            text = text.strip()
        name = next((n for n in NAMES if text.startswith(n)), None)
        if name is None:
            raise R2Error(f"Invalid command '{text}' (?)")
        arg = text[len(name) :].strip()
        fn = getattr(self, "c_" + {"?v": "qv", "?": "q_help", "s+": "sp", "s-": "sm", "!": "shell"}.get(name, name))
        saved = self.seek
        if tmp is not None:
            self.seek = tmp
        try:
            res = fn(arg)
        finally:
            if tmp is not None and name not in ("s", "s+", "s-"):
                self.seek = saved
        if grep:
            res = "".join(ln + "\n" for ln in res.splitlines() if grep in ln)
        return res

    # ------------------------------------------------------------ expressions
    def _eval(self, expr: str) -> int:
        expr = expr.replace(" ", "")
        if not expr:
            raise R2Error("Invalid number")
        total, sign = 0, 1
        for tok in re.split(r"([+-])", expr):
            if tok == "+":
                sign = 1
            elif tok == "-":
                sign = -1
            elif tok:
                if tok == "$$":
                    v = self.seek
                elif tok in self.flags:
                    v = self.flags[tok]
                else:
                    try:
                        v = int(tok, 0)
                    except ValueError:
                        raise R2Error(f"Invalid address ({expr})")
                total += sign * v
                sign = 1
        return total & 0xFFFF_FFFF_FFFF_FFFF

    def _label(self, addr: int) -> str:
        return self._names.get(addr, "")

    def _bytes(self, addr: int, n: int) -> bytes:
        off = addr - self.base
        out = bytearray()
        for i in range(n):
            o = off + i
            out.append(self.image[o] if 0 <= o < len(self.image) else 0xFF)
        return bytes(out)

    def _fn_at(self, addr: int):
        return self.level.function_containing(addr) if self.level else None

    def _need_level(self) -> Level:
        if self.level is None:
            raise R2Error("This command needs an analyzable ELF target.")
        return self.level

    # ------------------------------------------------------------------- seek
    def c_s(self, arg: str) -> str:
        if not arg:
            return f"0x{self.seek:x}\n"
        self.seek = self._eval(arg)
        return ""

    def c_sp(self, arg: str) -> str:
        self.seek = (self.seek + (self._eval(arg) if arg else 1)) & 0xFFFF_FFFF_FFFF_FFFF
        return ""

    def c_sm(self, arg: str) -> str:
        self.seek = (self.seek - (self._eval(arg) if arg else 1)) & 0xFFFF_FFFF_FFFF_FFFF
        return ""

    # --------------------------------------------------------------- analysis
    def _event(self, name: str) -> None:
        self.shell.world.events.add(name)

    def c_aa(self, arg: str) -> str:
        self._event("r2:analyzed")
        if self.level:
            self.known |= {f.addr for f in self.level.functions}
        return AA_LINES.splitlines(True)[0]

    def c_aaa(self, arg: str) -> str:
        self.c_aa(arg)
        return AA_LINES

    def c_aaaa(self, arg: str) -> str:
        return self.c_aaa(arg)

    def c_af(self, arg: str) -> str:
        fn = self._fn_at(self.seek)
        if fn is None:
            raise R2Error(f"Cannot analyze at 0x{self.seek:08x}")
        self.known.add(fn.addr)
        self._event("r2:analyzed")
        return ""

    def c_afl(self, arg: str) -> str:
        lv = self.level
        if lv is None:
            return ""
        rows = [(a, 6, f"sym.imp.{n}") for n, a in lv.imports.items()]
        if self.known:
            rows.append((self.entry, 37, "entry0"))
        rows += [(f.addr, f.size, self._fname(f.name)) for f in lv.functions if f.addr in self.known]
        return "".join(f"0x{a:08x}    1 {size:<11}{name}\n" for a, size, name in sorted(rows))

    def _xrefs_to(self, target: int) -> list[tuple[str, Insn, str]]:
        lv = self._need_level()
        found = []
        for f in lv.functions:
            if f.addr not in self.known:
                continue
            for insn in f.insns:
                m = re.fullmatch(r"call sym\.(imp\.)?(\w+)", insn.asm)
                if m:
                    t = lv.imports.get(m.group(2)) if m.group(1) else (lv.function(m.group(2)).addr if any(g.name == m.group(2) for g in lv.functions) else None)
                    if t == target:
                        found.append((self._fname(f.name), insn, "CALL:--x"))
                elif insn.comment.startswith("0x") and int(insn.comment, 16) == target:
                    found.append((self._fname(f.name), insn, "DATA:r--"))
        return found

    def c_axt(self, arg: str) -> str:
        target = self._eval(arg) if arg else self.seek
        return "".join(f"{n} 0x{i.addr:x} [{kind}] {i.asm}\n" for n, i, kind in self._xrefs_to(target))

    # ----------------------------------------------------------- disassembly
    def _fmt_insn(self, insn: Insn, prefix: str) -> str:
        raw = insn.raw if len(insn.raw) <= 12 else insn.raw[:12] + "."
        text = insn.asm
        comment = insn.comment
        m = re.fullmatch(r"call sym\.imp\.(\w+)", insn.asm)
        if m and m.group(1) in SIGS:
            comment = SIGS[m.group(1)]
        if comment:
            text = f"{insn.asm:<27}" + ("" if len(insn.asm) < 27 else " ") + f"; {comment}"
        return f"{prefix}0x{insn.addr:08x}      {raw:<14} {text}\n"

    def _header(self, fn, prefix: str) -> str:
        out = ""
        for src, insn, kind in self._xrefs_to(fn.addr):
            if kind.startswith("CALL"):
                out += f"            ; CALL XREF from {src} @ 0x{insn.addr:x}\n"
        out += f"{prefix}{fn.size}: {self._fname(fn.name)} ();\n"
        for decl in fn.locals:
            out += f"│           ; {decl}\n"
        return out

    def c_pdf(self, arg: str) -> str:
        fn = self._fn_at(self.seek)
        if fn is None or fn.addr not in self.known:
            raise R2Error(f"p: Cannot find function at 0x{self.seek:08x}")
        self._event("r2:disassembled")
        out = self._header(fn, "┌ ")
        last = len(fn.insns) - 1
        for i, insn in enumerate(fn.insns):
            out += self._fmt_insn(insn, ("└" if i == last else "│") + "           ")
        return out

    def c_pd(self, arg: str) -> str:
        n = int(arg, 0) if arg else 16
        lv = self.level
        addr, out = self.seek, ""
        fn0 = self._fn_at(addr)
        if fn0 and fn0.addr in self.known and fn0.addr == addr:
            out += self._header(fn0, "┌ ")
        for _ in range(n):
            insn = lv.insn_at(addr) if lv else None
            if insn is None:
                b = self._bytes(addr, 1)
                out += f"            0x{addr:08x}      {b.hex():<14} invalid\n"
                addr += 1
                continue
            fn = self._fn_at(addr)
            inside = fn is not None and fn.addr in self.known
            out += self._fmt_insn(insn, ("│" if inside else " ") + "           ")
            addr += insn.length
        return out

    # ---------------------------------------------------------------- dumping
    def c_px(self, arg: str) -> str:
        n = int(arg, 0) if arg else 64
        lines = ["- offset -   0 1  2 3  4 5  6 7  8 9  A B  C D  E F  0123456789ABCDEF"]
        for off in range(0, n, 16):
            chunk = self._bytes(self.seek + off, min(16, n - off))
            groups = " ".join(chunk[i : i + 2].hex() for i in range(0, len(chunk), 2))
            asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append(f"0x{self.seek + off:08x}  {groups:<39}  {asc}")
        return "\n".join(lines) + "\n"

    def c_p8(self, arg: str) -> str:
        return self._bytes(self.seek, int(arg, 0) if arg else 32).hex() + "\n"

    def c_psz(self, arg: str) -> str:
        raw = b""
        while len(raw) < 1024:
            b = self._bytes(self.seek + len(raw), 1)
            if b == b"\0" or b == b"\xff" and self.seek + len(raw) - self.base >= len(self.image):
                break
            raw += b
        return raw.decode("latin-1") + "\n"

    # -------------------------------------------------------------------- info
    def _strings(self, minlen: int) -> list[tuple[int, str]]:
        if self.level:
            return sorted(self.level.strings.items())
        return [(m.start(), m.group().decode()) for m in re.finditer(rb"[\x20-\x7e]{%d,}" % minlen, self.image)]

    def c_iz(self, arg: str) -> str:
        rows = self._strings(5 if not self.level else 1)
        out = ["[Strings]", "nth paddr      vaddr      len size section type  string", "―――――――――――――――――――――――――――――――――――――――――――――――――――――――"]
        for i, (addr, text) in enumerate(rows):
            shown = text.replace("\n", "\\n")
            out.append(f"{i:<3} 0x{addr - self.base:08x} 0x{addr:08x} {len(text):<3} {len(text) + 1:<4} .rodata ascii {shown}")
        return "\n".join(out) + "\n"

    c_izz = c_iz

    def c_ii(self, arg: str) -> str:
        lv = self._need_level()
        out = ["[Imports]", "nth vaddr      bind   type   lib name", "―――――――――――――――――――――――――――――――――――――"]
        for i, (n, a) in enumerate(lv.imports.items(), 1):
            out.append(f"{i:<3} 0x{a:08x} GLOBAL FUNC       {n}")
        return "\n".join(out) + "\n"

    def c_is(self, arg: str) -> str:
        lv = self._need_level()
        out = ["[Symbols]", "nth paddr      vaddr      bind   type size lib name", "―――――――――――――――――――――――――――――――――――――――――――――――――――――"]
        syms = [(a, 16, "FUNC", f"imp.{n}") for n, a in lv.imports.items()] + [(f.addr, f.size, "FUNC", f.name) for f in lv.functions]
        for i, (a, size, kind, name) in enumerate(sorted(syms)):
            out.append(f"{i:<3} 0x{a - self.base:08x} 0x{a:08x} GLOBAL {kind} {size:<4}     {name}")
        return "\n".join(out) + "\n"

    def c_iI(self, arg: str) -> str:
        lv = self.level
        if lv is None:
            return f"arch     unknown\nbintype  raw\nbinsz    {len(self.image)}\n"
        m = lv.mitigations
        rows = [
            ("arch", "x86"), ("baddr", f"0x{lv.base:x}"), ("binsz", str(len(self.image))), ("bintype", "elf"), ("bits", "64"),
            ("canary", str(bool(m.get("canary"))).lower()), ("class", "ELF64"), ("crypto", "false"), ("endian", "little"),
            ("havecode", "true"), ("intrp", "/lib64/ld-linux-x86-64.so.2"), ("laddr", "0x0"), ("lang", "c"),
            ("linenum", "false"), ("lsyms", "false"), ("machine", "AMD x86-64 architecture"),
            ("nx", str(bool(m.get("nx"))).lower()), ("os", "linux"), ("pic", str(bool(m.get("pie"))).lower()),
            ("relocs", "false"), ("relro", str(m.get("relro", "no"))), ("rpath", "NONE"), ("sanitize", "false"),
            ("static", "false"), ("stripped", "false"), ("subsys", "linux"), ("va", "true"),
        ]
        return "".join(f"{k:<8} {v}\n" for k, v in rows)

    def c_i(self, arg: str) -> str:
        lv = self.level
        fmt = "elf64" if lv else "raw"
        return (f"file     {self.path}\nsize     0x{len(self.image):x}\nmode     r-x\nformat   {fmt}\n"
                f"arch     {'x86' if lv else 'unknown'}\nbits     {64 if lv else 8}\n")

    def c_ie(self, arg: str) -> str:
        self._need_level()
        e = self.entry
        return f"[Entrypoints]\nvaddr=0x{e:08x} paddr=0x{e - self.base:08x} haddr=0x00000018 hvaddr=0x{self.base + 0x18:08x} type=program\n\n1 entrypoints\n"

    # -------------------------------------------------------------- misc/meta
    def c_qv(self, arg: str) -> str:
        return f"0x{self._eval(arg):x}\n"

    def c_q_help(self, arg: str) -> str:
        if not arg:
            return HELP
        v = self._eval(arg)
        return (f"int32   {v if v < 2**31 else v - 2**32 if v < 2**32 else v}\nhex     0x{v:x}\noctal   0{v:o}\n"
                f"unit    {v}\nbinary  0b{v:b}\n")

    def c_q(self, arg: str) -> str:
        self.done = True
        return ""

    def c_e(self, arg: str) -> str:
        return ""  # evaluation variables are accepted and ignored

    def c_w(self, arg: str) -> str:
        raise R2Error("Cannot write: the file is opened read-only in this simulation.")

    def c_V(self, arg: str) -> str:
        raise R2Error("Visual mode is not available in this simulation.")

    def c_shell(self, arg: str) -> str:
        raise R2Error("Shell escapes are not available; quit r2 with q.")


@command("r2", "radare2")
def r2(sh: Shell, args: list[str], stdin: bytes) -> Result:
    if stdin or not sh.tty:
        return fail("r2: cannot start an interactive session here (no terminal)")
    if any(a.startswith("-") and "d" in a[1:] and not a.startswith("--") for a in args):
        return fail("r2: debugger mode (-d) is not simulated; use gdb to run and debug a program")
    analyze = any(a.startswith("-") and "A" in a[1:] for a in args)
    files = [a for a in args if not a.startswith("-")]
    if not files:
        return fail("r2: no file given (usage: r2 [-A] FILE)")
    name = files[0]
    try:
        node = sh.vfs.get(name, sh.cwd)
    except VFSError:
        return fail(f"Cannot open {name}")
    if node.is_dir or not sh.vfs.can_read(node):
        return fail(f"Cannot open {name}")
    level = sh.world.level_for(node)
    data = node.data if isinstance(node.data, bytes) else bytes(node.data)
    s = R2Session(sh, level, data, name)
    if analyze and level:
        s.startup = ok(s.c_aaa(""))
    return Result(session=s)
