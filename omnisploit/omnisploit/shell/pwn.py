"""The pwntools command-line tools, simulated: cyclic, hex, unhex, phd (and checksec via `pwn`)."""

from __future__ import annotations

from ..engine.cyclic import cyclic as make_pattern, cyclic_find
from ..engine.vfs import VFSError
from .commands import REGISTRY, command, parse_flags
from .shell import Result, fail, ok


def _cyclic(sh, args, stdin) -> Result:
    flags, pos = parse_flags(args, "ln")
    if "l" in flags:
        raw = str(flags["l"])
        try:
            value: int | str = int(raw, 0)
        except ValueError:
            value = raw  # a literal substring such as "laaa"
        off = cyclic_find(value)
        if off < 0:
            return fail("Pattern not found", 1)
        sh.world.events.add("pwn:cyclic-find")
        return ok(f"{off}\n")
    if not pos:
        return fail("usage: cyclic [-l VALUE] LENGTH")
    try:
        n = int(pos[0], 0)
        out = ok(make_pattern(n).decode() + "\n")
        sh.world.events.add("pwn:cyclic")
        return out
    except ValueError as e:
        return fail(f"cyclic: {e}" if "exhausted" in str(e) else f"cyclic: invalid length: {pos[0]}")


def _hex(sh, args, stdin) -> Result:
    data = " ".join(args).encode() if args else stdin.rstrip(b"\n")
    return ok(data.hex() + "\n")


def _unhex(sh, args, stdin) -> Result:
    text = "".join(args) if args else stdin.decode(errors="replace").strip()
    try:
        return Result(out=bytes.fromhex(text))
    except ValueError:
        return fail("unhex: input is not valid hex")


def hexdump_text(data: bytes) -> str:
    lines = []
    for off in range(0, len(data), 16):
        chunk = data[off : off + 16]
        cells = [f"{b:02x}" for b in chunk] + ["  "] * (16 - len(chunk))
        hexpart = " ".join(" ".join(cells[i : i + 4]) for i in range(0, 16, 4))
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{off:08x}  {hexpart}  │{asc}│")
    lines.append(f"{len(data):08x}")
    return "\n".join(lines) + "\n"


def _phd(sh, args, stdin) -> Result:
    if args:
        try:
            data = sh.vfs.read(args[0], sh.cwd)
        except VFSError as e:
            return fail(f"phd: {e}")
    else:
        data = stdin
    return ok(hexdump_text(data))


TOOLS = {"cyclic": _cyclic, "hex": _hex, "unhex": _unhex, "phd": _phd}


@command("cyclic")
def cyclic_cmd(sh, args, stdin):
    return _cyclic(sh, args, stdin)


@command("pwn")
def pwn(sh, args, stdin):
    if not args or args[0] in ("-h", "--help"):
        return ok("usage: pwn [-h] {cyclic,checksec,hex,unhex,phd} ...\n")
    tool = args[0]
    if tool == "checksec":
        return REGISTRY["checksec"](sh, args[1:], stdin)
    if tool not in TOOLS:
        return fail(f"pwn: error: argument command: invalid choice: '{tool}' (choose from cyclic, checksec, hex, unhex, phd)", 2)
    return TOOLS[tool](sh, args[1:], stdin)
