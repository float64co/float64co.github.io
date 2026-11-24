"""The pwntools subset for the sandboxed interpreter: `from pwn import *`.

Packing, the cyclic pattern, hex helpers, ``context`` settings, ``log``, ``process`` (a tube over
the simulated CPU, never the host), ``ELF`` (read-only: symbols, imports, mitigations, byte search)
and a small ``ROP`` chain builder limited to the (pop reg; ret) / (leave; ret) / (ret) tail gadgets
already present in these binaries' authored instruction streams -- there is no byte-level x86
disassembler here, so gadgets starting mid-instruction are not discovered, and multi-argument
``rop.call(name, args)`` is refused rather than faked.
"""

from __future__ import annotations

from ..engine.cyclic import cyclic as _cyclic, cyclic_find as _cyclic_find
from ..engine.vfs import VFSError
from .pwn import hexdump_text

ARCH_BITS = {"i386": 32, "arm": 32, "amd64": 64, "aarch64": 64}


class Context:
    """`context.arch = "amd64"` and friends; only these attributes exist."""

    ALLOWED = ("arch", "bits", "endian", "os", "log_level", "word_size")

    def __init__(self) -> None:
        self.values = {"arch": "i386", "bits": 32, "endian": "little", "os": "linux", "log_level": "info"}

    def get(self, name: str):
        if name == "word_size":
            return self.values["bits"]
        if name in self.values:
            return self.values[name]
        raise AttributeError(f"'context' has no attribute '{name}'")

    def set(self, name: str, value) -> None:
        if name not in self.ALLOWED or name == "word_size":
            raise AttributeError(f"cannot set context.{name}")
        if name == "arch":
            if value not in ARCH_BITS:
                raise AttributeError(f"unsupported arch {value!r} (choose from {', '.join(ARCH_BITS)})")
            self.values["bits"] = ARCH_BITS[value]
        elif name == "bits" and value not in (8, 16, 32, 64):
            raise AttributeError("bits must be 8, 16, 32 or 64")
        elif name == "endian" and value not in ("little", "big"):
            raise AttributeError("endian must be 'little' or 'big'")
        self.values[name] = value


class Elf:
    """A read-only view of an ELF file's layout and symbols. Never executes anything.

    Only the level target binaries (``./motd``, ``./vault``, ...) carry real symbol and function
    data in this simulation; any other ELF opens fine but has empty ``symbols``/``functions`` and
    an address of 0, since nothing here has disassembled it.
    """

    ATTRS = frozenset({
        "path", "address", "entry", "symbols", "sym", "plt", "functions",
        "canary", "nx", "pie", "relro", "arch", "bits",
    })
    METHODS = frozenset({"read", "search"})

    def __init__(self, interp, path: str) -> None:
        sh = interp.shell
        try:
            node = sh.vfs.get(path, sh.cwd)
        except VFSError:
            raise FileNotFoundError(f"[Errno 2] No such file or directory: '{path}'")
        if node.is_dir or not node.data.startswith(b"\x7fELF"):
            raise OSError(f"'{path}' is not an ELF file")
        if not sh.vfs.can_read(node):
            raise PermissionError(f"[Errno 13] Permission denied: '{path}'")
        self.path = path
        level = sh.world.level_for(node)
        self._level = level
        self._image = level.build_elf() if level else bytes(node.data)
        if level is not None:
            self.address = level.base
            self.entry = level.functions[0].addr  # this simulation's one "entry point" convention
            self.plt = dict(level.imports)
            self.symbols = {f.name: f.addr for f in level.functions}
            self.symbols.update(self.plt)
            self.sym = self.symbols
            self.functions = {f.name: {"address": f.addr, "size": f.size} for f in level.functions}
            m = level.mitigations
            self.canary, self.nx, self.pie = bool(m.get("canary")), bool(m.get("nx")), bool(m.get("pie"))
            self.relro = str(m.get("relro", "no"))
        else:
            self.address = self.entry = 0
            self.plt, self.symbols, self.sym, self.functions = {}, {}, {}, {}
            self.canary = self.nx = self.pie = False
            self.relro = "no"
        self.arch, self.bits = "amd64", 64

    def read(self, addr: int, n: int) -> bytes:
        off = addr - self.address
        if not (0 <= off and off + n <= len(self._image)):
            raise ValueError(f"address 0x{addr:x} is not mapped in {self.path}")
        return self._image[off : off + n]

    def search(self, needle, writable: bool = False) -> list:
        if isinstance(needle, str):
            needle = needle.encode()
        elif not isinstance(needle, (bytes, bytearray)):
            raise TypeError("search() expects bytes or str")
        needle, data, out, start = bytes(needle), bytes(self._image), [], 0
        while True:
            i = data.find(needle, start)
            if i < 0:
                return out
            out.append(self.address + i)
            start = i + 1

    def attr(self, name: str):
        if name in self.ATTRS or name in self.METHODS:
            return getattr(self, name)
        raise AttributeError(f"'ELF' object has no attribute '{name}'")


def _tail_gadgets(level) -> dict[tuple, int]:
    """(pop reg; ret) / (leave; ret) / (ret) tails: every ``ret`` already in the authored instruction
    stream, plus the one or two instructions immediately before it, within the same function."""
    found: dict[tuple, int] = {}
    if level is None:
        return found
    for fn in level.functions:
        insns = fn.insns
        for i, insn in enumerate(insns):
            if insn.asm != "ret":
                continue
            for length in (1, 2, 3):
                start = i - length + 1
                if start < 0:
                    continue
                seq = tuple(x.asm for x in insns[start : i + 1])
                found.setdefault(seq, insns[start].addr)
    return found


class Rop:
    """A minimal ROP chain builder.

    Gadgets are only the tail sequences :func:`_tail_gadgets` finds (no byte-level disassembly, so
    nothing starting mid-instruction). ``call(name)`` with no arguments just places that symbol's
    address, which is all a direct ret2win needs; a multi-argument call is refused rather than
    faked, since these binaries do not expose gadgets that load the calling-convention registers.
    """

    ATTRS = frozenset({"gadgets"})
    METHODS = frozenset({"raw", "call", "chain", "dump", "find_gadget"})

    def __init__(self, elf: "Elf") -> None:
        if not isinstance(elf, Elf):
            raise TypeError("ROP() expects an ELF object")
        self.elf = elf
        self._chain: list[int] = []
        self._gadgets = _tail_gadgets(elf._level)
        self.gadgets = {" ; ".join(seq): addr for seq, addr in self._gadgets.items()}

    def find_gadget(self, insns) -> int | None:
        if not isinstance(insns, (list, tuple)) or not all(isinstance(i, str) for i in insns):
            raise TypeError("find_gadget() expects a list of instruction strings, e.g. ['pop rbp', 'ret']")
        return self._gadgets.get(tuple(insns))

    def raw(self, value: int) -> None:
        if not isinstance(value, int):
            raise TypeError("raw() expects an address or integer value")
        self._chain.append(value & 0xFFFF_FFFF_FFFF_FFFF)

    def call(self, target, args=None) -> None:
        if args:
            raise NotImplementedError(
                "this simulation's binaries have no gadgets to load call arguments; "
                "use rop.call(target) with no arguments, or rop.raw(value) to place addresses by hand"
            )
        addr = self.elf.symbols.get(target) if isinstance(target, str) else target
        if addr is None:
            raise KeyError(f"no symbol named {target!r}")
        self.raw(addr)

    def chain(self) -> bytes:
        return b"".join(v.to_bytes(8, "little") for v in self._chain)

    def dump(self) -> str:
        return "\n".join(f"0x{i * 8:04x}: 0x{v:016x}" for i, v in enumerate(self._chain))

    def attr(self, name: str):
        if name in self.ATTRS or name in self.METHODS:
            return getattr(self, name)
        raise AttributeError(f"'ROP' object has no attribute '{name}'")


class Tube:
    """A local process tube over the simulated CPU (recv*/send*, poll, close). Nothing runs on the host."""

    METHODS = frozenset("recv recvline recvuntil recvall recvrepeat send sendline sendafter sendlineafter poll wait close kill "
                        "can_recv interactive".split())
    ATTRS = frozenset({"returncode", "pid"})
    MAX_OPEN = 8

    def __init__(self, interp, argv0: str) -> None:
        from .pyinterp import Unsupported  # noqa: F401  (imported lazily: pyinterp builds this module)
        from ..engine.vfs import VFSError

        sh = interp.shell
        self.interp, self.name = interp, argv0
        if getattr(interp, "open_tubes", 0) >= self.MAX_OPEN:
            raise OSError("too many open processes")
        try:
            node = sh.vfs.get(argv0, sh.cwd)
        except VFSError:
            raise FileNotFoundError(f"[Errno 2] No such file or directory: '{argv0}'")
        level = sh.world.level_for(node)
        if node.is_dir or level is None:
            raise OSError(f"[Errno 8] Exec format error: '{argv0}'")
        if not sh.vfs.can_exec(node):
            raise PermissionError(f"[Errno 13] Permission denied: '{argv0}'")
        interp.open_tubes = getattr(interp, "open_tubes", 0) + 1
        sh.world.events.add("py:process")
        proc = sh.world.new_process(level, suid=bool(node.mode & 0o4000))
        self.level, self.pid = level, proc.pid
        self.cpu = proc.start(None)  # interactive: gets() waits for what we send
        self.buf = bytearray()
        self.pending = b""
        self.alive = True
        self.returncode: int | None = None
        self._log(f"[+] Starting local process '{argv0}': pid {self.pid}")
        self._pump()

    # ------------------------------------------------------------- plumbing
    def _log(self, text: str) -> None:
        if self.interp.pwn_context.values["log_level"] in ("debug", "info"):
            self.interp.emit(text + "\n", True)

    def _pump(self) -> None:
        cpu, world = self.cpu, self.interp.shell.world
        if not self.alive:
            return
        stop = cpu.run()
        self.buf += cpu.take_output()
        if cpu.solved:
            world.events.add(f"solved:{self.level.id}")
        if stop.kind == "exit":
            self.returncode = cpu.exit_code or 0
        elif stop.kind == "fault":
            world.events.add(f"crash:{self.level.id}")
            if cpu.signal == "SIGABRT":
                world.events.add(f"abort:{self.level.id}")
            self.returncode = -{"SIGILL": 4, "SIGABRT": 6}.get(cpu.signal, 11)
        else:
            return  # waiting for input
        self.alive = False
        self.interp.open_tubes -= 1
        self._log(f"[*] Process '{self.name}' stopped with exit code {self.returncode} (pid {self.pid})")

    @staticmethod
    def _bytes(data) -> bytes:
        if isinstance(data, str):
            return data.encode()
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
        raise TypeError(f"data must be bytes or str, not {type(data).__name__}")

    # ---------------------------------------------------------------- sending
    def send(self, data) -> None:
        if not self.alive:
            raise EOFError("Process has stopped; cannot send")
        self.pending += self._bytes(data)
        *lines, self.pending = self.pending.split(b"\n")  # gets() only sees complete lines
        for ln in lines:
            self.cpu.give_input(ln)
        if lines:
            self._pump()

    def sendline(self, data=b"") -> None:
        self.send(self._bytes(data) + b"\n")

    def sendafter(self, delim, data) -> None:
        self.recvuntil(delim)
        self.send(data)

    def sendlineafter(self, delim, data=b"") -> None:
        self.recvuntil(delim)
        self.sendline(data)

    # -------------------------------------------------------------- receiving
    def recv(self, numb: int = 4096, timeout=None) -> bytes:
        if not self.buf and not self.alive:
            raise EOFError("Process has stopped")
        out = bytes(self.buf[:numb])
        del self.buf[:numb]
        return out

    def recvuntil(self, delims, drop: bool = False, timeout=None) -> bytes:
        d = self._bytes(delims)
        i = self.buf.find(d)
        if i < 0:
            if not self.alive:
                self.buf.clear()
                raise EOFError("Process has stopped before the delimiter appeared")
            return b""  # would block: behaves like a timeout and leaves the data in place
        out = bytes(self.buf[: i + len(d)])
        del self.buf[: i + len(d)]
        return out[: -len(d)] if drop else out

    def recvline(self, keepends: bool = True, timeout=None) -> bytes:
        return self.recvuntil(b"\n", drop=not keepends)

    def recvall(self, timeout=None) -> bytes:
        out, self.buf = bytes(self.buf), bytearray()
        return out

    recvrepeat = recvall

    def can_recv(self, timeout=0) -> bool:
        return bool(self.buf)

    # ---------------------------------------------------------------- control
    def poll(self, block: bool = False):
        return self.returncode

    wait = poll

    def close(self) -> None:
        if self.alive:
            self.alive = False
            self.interp.open_tubes -= 1
            self._log(f"[*] Stopped process '{self.name}' (pid {self.pid})")

    kill = close

    def interactive(self, *a, **k):
        from .pyinterp import Unsupported

        raise Unsupported("interactive(); run ./motd or gdb in the shell instead")

    def attr(self, name: str):
        if name in self.ATTRS:
            return getattr(self, name)
        if name in self.METHODS:
            return getattr(self, name)
        raise AttributeError(f"'process' object has no attribute '{name}'")


def build_pwn(interp):
    from .pyinterp import Module  # local import: pyinterp builds this module in its constructor

    ctx = Context()
    interp.pwn_context = ctx

    def process(argv, *a, **kw):
        if kw.keys() - {"cwd", "stdin", "stdout", "stderr"} or a:
            raise TypeError("process() supports only the program path in this simulation")
        argv0 = argv[0] if isinstance(argv, (list, tuple)) and argv else argv
        if not isinstance(argv0, str):
            raise TypeError("process() needs a path (or a list starting with one)")
        return Tube(interp, argv0)

    process.__doc__ = Tube.__doc__

    def elf_ctor(path, *a, **kw):
        if a or kw:
            raise TypeError("ELF() supports only a path in this simulation")
        if not isinstance(path, str):
            raise TypeError("ELF() needs a path")
        return Elf(interp, path)

    elf_ctor.__doc__, elf_ctor.__name__ = Elf.__doc__, "ELF"

    def rop_ctor(elf):
        return Rop(elf)

    rop_ctor.__doc__, rop_ctor.__name__ = Rop.__doc__, "ROP"

    def pack(value: int, size: int, endian=None) -> bytes:
        if not isinstance(value, int):
            raise TypeError(f"pack expects an int, not {type(value).__name__}")
        if not -(1 << (8 * size - 1)) <= value < (1 << (8 * size)):
            raise OverflowError(f"{value} does not fit in {size} bytes")
        return (value & ((1 << (8 * size)) - 1)).to_bytes(size, endian or ctx.values["endian"])

    def unpack(data, size: int, endian=None) -> int:
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("unpack expects bytes")
        if len(data) != size:
            raise ValueError(f"unpack requires a buffer of {size} bytes, got {len(data)}")
        return int.from_bytes(bytes(data), endian or ctx.values["endian"])

    def cyclic(length: int, n: int = 4) -> bytes:
        if not isinstance(length, int) or length < 0:
            raise ValueError("cyclic length must be a non-negative int")
        return _cyclic(length)

    def cyclic_find(value, n: int = 4) -> int:
        return _cyclic_find(value)

    def enhex(data: bytes) -> str:
        return bytes(data).hex()

    def unhex(text) -> bytes:
        return bytes.fromhex(text.decode() if isinstance(text, bytes) else text)

    def hexdump(data) -> str:
        return hexdump_text(bytes(data))

    def flat(*items, word_size=None) -> bytes:
        size = (word_size or ctx.values["bits"]) // 8
        out = bytearray()

        def add(x) -> None:
            if isinstance(x, (list, tuple)):
                for y in x:
                    add(y)
            elif isinstance(x, int):
                out.extend(pack(x, size))
            elif isinstance(x, str):
                out.extend(x.encode())
            elif isinstance(x, (bytes, bytearray)):
                out.extend(x)
            else:
                raise TypeError(f"flat cannot encode {type(x).__name__}")

        add(items)
        return bytes(out)

    def _say(tag: str):
        def say(msg, *a):
            interp.emit(f"[{tag}] {msg % a if a else msg}\n")
        return say

    log = Module("log", {"info": _say("*"), "success": _say("+"), "warning": _say("!"), "warn": _say("!"),
                         "debug": lambda *a: None})

    attrs = {
        "p8": lambda v, **k: pack(v, 1, k.get("endian")), "p16": lambda v, **k: pack(v, 2, k.get("endian")),
        "p32": lambda v, **k: pack(v, 4, k.get("endian")), "p64": lambda v, **k: pack(v, 8, k.get("endian")),
        "u8": lambda d, **k: unpack(d, 1, k.get("endian")), "u16": lambda d, **k: unpack(d, 2, k.get("endian")),
        "u32": lambda d, **k: unpack(d, 4, k.get("endian")), "u64": lambda d, **k: unpack(d, 8, k.get("endian")),
        "cyclic": cyclic, "cyclic_find": cyclic_find, "enhex": enhex, "unhex": unhex, "hexdump": hexdump,
        "flat": flat, "context": ctx, "log": log, "process": process, "ELF": elf_ctor, "ROP": rop_ctor,
    }
    return Module("pwn", attrs)
