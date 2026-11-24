"""Built-in commands for the fake shell. Add a new program with ``@command("name")``."""

from __future__ import annotations

import math
import re
import time
from typing import Callable

from ..engine.vfs import VFSError, Node
from .shell import Result, Shell, fail, ok

REGISTRY: dict[str, Callable[[Shell, list[str], bytes], Result]] = {}
# Commands with no /usr/bin file at all (pure shell builtins)...
BUILTIN_ONLY = {
    "cd", "exit", "logout", "export", "help", "alias", "unalias", "history", "jobs", "fg", "bg",
    "wait", "kill", "break", "continue", "source", ".", "type", "return", "local", "unset", "declare", "read",
}
# ...and everything bash resolves itself, regardless of $PATH. Others also exist in /usr/bin.
SHELL_BUILTINS = BUILTIN_ONLY | {"echo", "pwd", "true", "false", "test", "["}


def command(*names: str):
    def deco(fn):
        for n in names:
            REGISTRY[n] = fn
        return fn

    return deco


def parse_flags(args: list[str], with_value: str = "", digit_flags: bool = False) -> tuple[dict[str, str | bool], list[str]]:
    """Tiny getopt: '-abc' flags, '-n 5' / '-n5' for letters in ``with_value``."""
    flags: dict[str, str | bool] = {}
    pos: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            pos.extend(args[i + 1 :])
            break
        if a.startswith("-") and len(a) > 1 and (digit_flags or not a[1:].isdigit()):
            j = 1
            while j < len(a):
                ch = a[j]
                if ch in with_value:
                    val = a[j + 1 :]
                    if not val:
                        i += 1
                        val = args[i] if i < len(args) else ""
                    flags[ch] = val
                    break
                flags[ch] = True
                j += 1
        else:
            pos.append(a)
        i += 1
    return flags, pos


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _read_inputs(sh: Shell, name: str, files: list[str], stdin: bytes) -> tuple[list[tuple[str, bytes]], list[str]]:
    """Resolve file operands (or stdin) into [(label, data)] plus error lines."""
    if not files:
        return [("-", stdin)], []
    out, errs = [], []
    for f in files:
        try:
            out.append((f, sh.vfs.read(f, sh.cwd)))
        except VFSError as e:
            errs.append(f"{name}: {e}")
    return out, errs


def _errres(errs: list[str], out: str = "") -> Result:
    r = ok(out, 1 if errs else 0)
    r.err = "".join(e + "\n" for e in errs).encode()
    return r


# ------------------------------------------------------------------ basics
@command("echo")
def echo(sh, args, stdin):
    newline = True
    interpret = False
    while args and args[0] in ("-n", "-e", "-ne", "-en"):
        if "n" in args[0]:
            newline = False
        if "e" in args[0]:
            interpret = True
        args = args[1:]
    text = " ".join(args)
    if interpret:
        text = text.encode().decode("unicode_escape", errors="replace")
    return ok(text + ("\n" if newline else ""))


@command("pwd")
def pwd(sh, args, stdin):
    return ok(sh.cwd + "\n")


@command("cd")
def cd(sh, args, stdin):
    target = args[0] if args else sh.env["HOME"]
    if target == "-":
        target = sh.oldpwd
    try:
        node = sh.vfs.get(target, sh.cwd)
    except VFSError:
        return fail(f"bash: cd: {target}: No such file or directory")
    if not node.is_dir:
        return fail(f"bash: cd: {target}: Not a directory")
    sh.oldpwd = sh.cwd
    sh.cwd = sh.abspath(target)
    sh.env["PWD"] = sh.cwd
    return ok()


@command("clear")
def clear(sh, args, stdin):
    return Result(action=("clear",))


@command("exit", "logout")
def exit_(sh, args, stdin):
    return Result(action=("quit",))


@command("whoami")
def whoami(sh, args, stdin):
    return ok(sh.vfs.user + "\n")


@command("id")
def id_(sh, args, stdin):
    u = sh.vfs.user
    if u == "root":
        return ok("uid=0(root) gid=0(root) groups=0(root)\n")
    return ok(f"uid=1000({u}) gid=1000({u}) groups=1000({u})\n")


@command("hostname")
def hostname(sh, args, stdin):
    flags, _ = parse_flags(args)
    if flags.get("i") or flags.get("I"):
        return ok(sh.world.vantage + "\n")
    return ok(sh.world.hostname + "\n")


@command("uname")
def uname(sh, args, stdin):
    if any("a" in a for a in args):
        return ok(
            f"Linux {sh.world.hostname} 5.15.0-97-generic #107-Ubuntu SMP "
            "Wed Feb 7 13:26:48 UTC 2024 x86_64 x86_64 x86_64 GNU/Linux\n"
        )
    return ok("Linux\n")


@command("date")
def date(sh, args, stdin):
    return ok(time.strftime("%a %b %e %H:%M:%S UTC %Y\n", time.gmtime()))


@command("sleep", "true")
def noop(sh, args, stdin):
    return ok()


@command("false")
def false_(sh, args, stdin):
    return ok(status=1)


@command("env")
def env(sh, args, stdin):
    return ok("".join(f"{k}={v}\n" for k, v in sh.env.items()))


@command("export")
def export(sh, args, stdin):
    for a in args:
        if "=" in a:
            k, v = a.split("=", 1)
            # bash expands '~' at the start of an assignment value and after each ':'
            v = re.sub(r"(^|:)~(?=/|:|$)", lambda m: m.group(1) + sh.env.get("HOME", "/"), v)
            sh.env[k] = v
    return ok()


@command("which")
def which(sh, args, stdin):
    lines, status = [], 0
    for a in args:
        found = sh.find_in_path(a) if "/" not in a else None
        if found:
            lines.append(found[0])
        else:
            status = 1
    return ok("".join(l + "\n" for l in lines), status)


# ------------------------------------------------------------- filesystem
def _ls_columns(names: list[str], width: int) -> str:
    if not names:
        return ""
    colw = max(len(n) for n in names) + 2
    cols = max(1, width // colw)
    rows = math.ceil(len(names) / cols)
    cols = math.ceil(len(names) / rows)
    lines = []
    for r in range(rows):
        cells = [names[c * rows + r] for c in range(cols) if c * rows + r < len(names)]
        lines.append("".join(c.ljust(colw) for c in cells[:-1]) + cells[-1])
    return "\n".join(lines) + "\n"


def _perm_string(node: Node) -> str:
    s = "d" if node.is_dir else "-"
    special = {6: node.mode & 0o4000, 3: node.mode & 0o2000, 0: node.mode & 0o1000}
    special_char = {6: "s", 3: "s", 0: "t"}
    for shift in (6, 3, 0):
        bits = (node.mode >> shift) & 7
        s += ("r" if bits & 4 else "-") + ("w" if bits & 2 else "-")
        if special[shift]:
            s += special_char[shift] if bits & 1 else special_char[shift].upper()
        else:
            s += "x" if bits & 1 else "-"
    return s


@command("ls")
def ls(sh, args, stdin):
    flags, pos = parse_flags(args, digit_flags=True)
    show_all, long_fmt = "a" in flags or "A" in flags, "l" in flags
    targets = pos or ["."]
    out, errs = [], []
    for t_idx, target in enumerate(targets):
        try:
            nodes = sh.vfs.listdir(target, sh.cwd)
            base = sh.vfs.get(target, sh.cwd)
        except VFSError as e:
            msg = str(e)
            if "No such file" in msg:
                msg = f"cannot access '{target}': No such file or directory"
            errs.append(f"ls: {msg}")
            continue
        if base.is_dir:
            nodes = [n for n in nodes if show_all or not n.name.startswith(".")]
            if "a" in flags:
                nodes = [Node(".", True, mode=0o755, owner=base.owner), Node("..", True, mode=0o755, owner="root")] + nodes
        if len(targets) > 1:
            out.append(("\n" if out else "") + f"{target}:\n")
        if long_fmt:
            total = sum(max(1, len(n.data) // 1024 + 1) * 4 if not n.is_dir else 4 for n in nodes)
            lines = [f"total {total}\n"] if base.is_dir else []
            for n in nodes:
                size = 4096 if n.is_dir else len(n.data)
                lines.append(
                    f"{_perm_string(n)} {2 if n.is_dir else 1} {n.owner:<6} {n.owner:<6} {size:>6} Nov 22 01:00 {n.name}\n"
                )
            out.append("".join(lines))
        elif sh.tty and "1" not in flags:
            out.append(_ls_columns([n.name for n in nodes], sh.width))
        else:
            out.append("".join(n.name + "\n" for n in nodes))
    return _errres(errs, "".join(out))


@command("cat")
def cat(sh, args, stdin):
    flags, pos = parse_flags(args)
    inputs, errs = _read_inputs(sh, "cat", pos, stdin)
    data = b"".join(d for _, d in inputs)
    if flags.get("n"):
        lines = data.decode(errors="replace").splitlines(keepends=True)
        data = "".join(f"{i + 1:>6}\t{l}" for i, l in enumerate(lines)).encode()
    res = _errres(errs)
    res.out = data
    return res


@command("touch")
def touch(sh, args, stdin):
    errs = []
    for a in args:
        try:
            if not sh.vfs.exists(a, sh.cwd):
                sh.vfs.write(a, b"", sh.cwd)
        except VFSError as e:
            errs.append(f"touch: cannot touch '{a}': {str(e).split(': ', 1)[-1]}")
    return _errres(errs)


@command("mkdir")
def mkdir(sh, args, stdin):
    flags, pos = parse_flags(args)
    errs = []
    for a in pos:
        try:
            sh.vfs.mkdir(a, sh.cwd, parents="p" in flags)
        except VFSError as e:
            errs.append(f"mkdir: {e}")
    return _errres(errs)


@command("rm")
def rm(sh, args, stdin):
    flags, pos = parse_flags(args)
    errs = []
    for a in pos:
        try:
            sh.vfs.remove(a, sh.cwd, recursive="r" in flags or "R" in flags)
        except VFSError as e:
            if "f" not in flags:
                errs.append(f"rm: {e}")
    return _errres(errs)


def _dest(sh: Shell, src: str, dst: str) -> str:
    try:
        if sh.vfs.get(dst, sh.cwd).is_dir:
            return dst.rstrip("/") + "/" + src.rstrip("/").rsplit("/", 1)[-1]
    except VFSError:
        pass
    return dst


@command("cp")
def cp(sh, args, stdin):
    _, pos = parse_flags(args)
    if len(pos) != 2:
        return fail("cp: missing file operand")
    try:
        node = sh.vfs.get(pos[0], sh.cwd)
        data = sh.vfs.read(pos[0], sh.cwd)
        sh.vfs.write(_dest(sh, *pos), data, sh.cwd)
        if node.program is None and sh.vfs.exists(_dest(sh, *pos), sh.cwd):
            sh.vfs.get(_dest(sh, *pos), sh.cwd).mode = node.mode
    except VFSError as e:
        return fail(f"cp: {e}")
    return ok()


@command("mv")
def mv(sh, args, stdin):
    _, pos = parse_flags(args)
    if len(pos) != 2:
        return fail("mv: missing file operand")
    try:
        data = sh.vfs.read(pos[0], sh.cwd)
        sh.vfs.write(_dest(sh, *pos), data, sh.cwd)
        sh.vfs.remove(pos[0], sh.cwd)
    except VFSError as e:
        return fail(f"mv: {e}")
    return ok()


@command("chmod")
def chmod(sh, args, stdin):
    if len(args) < 2:
        return fail("chmod: missing operand")
    spec, files = args[0], args[1:]
    errs = []
    for f in files:
        try:
            node = sh.vfs.get(f, sh.cwd)
            if node.owner != sh.vfs.user and sh.vfs.user != "root":
                errs.append(f"chmod: changing permissions of '{f}': Operation not permitted")
                continue
            if re.fullmatch(r"[0-7]{3,4}", spec):
                node.mode = int(spec, 8)
            else:
                m = re.fullmatch(r"([ugoa]*)([+-])([rwx]+)", spec)
                if not m:
                    return fail(f"chmod: invalid mode: '{spec}'")
                who, op, perms = m.groups()
                bits = sum({"r": 4, "w": 2, "x": 1}[p] for p in perms)
                mask = 0
                for w in who or "a":
                    mask |= {"u": bits << 6, "g": bits << 3, "o": bits, "a": bits * 0o111}[w]
                node.mode = node.mode | mask if op == "+" else node.mode & ~mask
        except VFSError as e:
            errs.append(f"chmod: cannot access '{f}': No such file or directory")
    return _errres(errs)


# ---------------------------------------------------------------- text tools
def _headtail(which: str):
    def fn(sh, args, stdin):
        flags, pos = parse_flags(args, "n")
        n = 10
        if "n" in flags:
            try:
                n = int(flags["n"])
            except ValueError:
                return fail(f"{which}: invalid number of lines: '{flags['n']}'")
        else:
            for a in args:
                if re.fullmatch(r"-\d+", a):
                    n = int(a[1:])
                    pos = [p for p in pos if p != a]
        inputs, errs = _read_inputs(sh, which, pos, stdin)
        chunks = []
        for label, data in inputs:
            lines = data.splitlines(keepends=True)
            sel = lines[:n] if which == "head" else lines[-n:] if n else []
            if len(inputs) > 1:
                chunks.append(f"==> {label} <==\n")
            chunks.append(b"".join(sel).decode(errors="replace"))
        return _errres(errs, "".join(chunks))

    return fn


REGISTRY["head"] = _headtail("head")
REGISTRY["tail"] = _headtail("tail")


@command("wc")
def wc(sh, args, stdin):
    flags, pos = parse_flags(args)
    inputs, errs = _read_inputs(sh, "wc", pos, stdin)
    lines_out = []
    want = [k for k in "lwc" if flags.get(k)] or ["l", "w", "c"]
    for label, data in inputs:
        counts = {"l": data.count(b"\n"), "w": len(data.split()), "c": len(data)}
        row = " ".join(str(counts[k]) for k in want)
        lines_out.append(row + ("" if label == "-" else f" {label}") + "\n")
    return _errres(errs, "".join(lines_out))


def _walk_files(sh: Shell, path: str, errs: list[str]):
    """Yield (display_path, bytes) for every readable file under path."""
    try:
        node = sh.vfs.get(path, sh.cwd)
    except VFSError as e:
        errs.append(f"grep: {e}")
        return
    if not node.is_dir:
        if sh.vfs.can_read(node):
            yield path, node.data
        else:
            errs.append(f"grep: {path}: Permission denied")
        return
    if not sh.vfs.can_read(node):
        errs.append(f"grep: {path}: Permission denied")
        return
    for name in sorted(node.children):
        child = path.rstrip("/") + "/" + name
        yield from _walk_files(sh, child, errs)


@command("grep")
def grep(sh, args, stdin):
    flags, pos = parse_flags(args, "e")
    if "e" in flags:  # -e PATTERN: every positional is then a file
        pos = [str(flags["e"])] + pos
    if not pos:
        return fail("Usage: grep [OPTION]... PATTERNS [FILE]...", 2)
    pattern, files = pos[0], pos[1:]
    try:
        rx = re.compile(pattern, re.I if "i" in flags else 0)
    except re.error:
        rx = re.compile(re.escape(pattern))
    recursive = bool(flags.get("r") or flags.get("R"))
    errs: list[str] = []
    if recursive:
        inputs = []
        for f in files or ["."]:
            inputs.extend(_walk_files(sh, f, errs))
        label_all = True
    else:
        inputs, errs = _read_inputs(sh, "grep", files, stdin)
        label_all = len(inputs) > 1
    if flags.get("h"):
        label_all = False
    out, matched = [], False
    for label, data in inputs:
        hit_file = False
        count = 0
        for i, line in enumerate(_text(data).splitlines(), 1):
            if bool(rx.search(line)) != bool(flags.get("v")):
                matched = hit_file = True
                count += 1
                if flags.get("l"):
                    break
                if flags.get("c"):
                    continue
                prefix = f"{label}:" if label_all else ""
                prefix += f"{i}:" if flags.get("n") else ""
                if flags.get("o") and not flags.get("v"):  # only the matching parts, one per line
                    out.extend(prefix + m.group(0) + "\n" for m in rx.finditer(line) if m.group(0))
                else:
                    out.append(prefix + line + "\n")
        if flags.get("c") and not flags.get("l"):
            out.append((f"{label}:" if label_all else "") + f"{count}\n")
        if flags.get("l") and hit_file:
            out.append(f"{label}\n")
    res = _errres(errs, "".join(out))
    res.status = 0 if matched else 1
    if errs and not matched:
        res.status = 2
    return res


@command("xxd")
def xxd(sh, args, stdin):
    flags, pos = parse_flags(args, "ls")
    inputs, errs = _read_inputs(sh, "xxd", pos[:1], stdin)
    if errs:
        return _errres([e.replace("xxd: ", "xxd: ") for e in errs])
    data = inputs[0][1]
    start = int(str(flags.get("s", "0")), 0)
    data = data[start:]
    if "l" in flags:
        data = data[: int(str(flags["l"]), 0)]
    lines = []
    for off in range(0, len(data), 16):
        chunk = data[off : off + 16]
        groups = [chunk[i : i + 2].hex() for i in range(0, len(chunk), 2)]
        hexpart = " ".join(groups).ljust(39)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{start + off:08x}: {hexpart}  {asc}\n")
    return ok("".join(lines))


@command("strings")
def strings(sh, args, stdin):
    flags, pos = parse_flags(args, "n")
    minlen = int(str(flags.get("n", 4)))
    inputs, errs = _read_inputs(sh, "strings", pos, stdin)
    out = []
    for _, data in inputs:
        for m in re.finditer(rb"[\x20-\x7e]{%d,}" % minlen, data):
            out.append(m.group().decode() + "\n")
    return _errres(errs, "".join(out))


@command("file")
def file_(sh, args, stdin):
    out, errs = [], []
    for a in args:
        try:
            node = sh.vfs.get(a, sh.cwd)
        except VFSError:
            errs.append(f"{a}: cannot open `{a}' (No such file or directory)")
            continue
        if node.is_dir:
            desc = "directory"
        elif node.data.startswith(b"\x7fELF"):
            lv = sh.world.level_for(node)
            build = lv.build_id if lv else "0" * 40
            pie = "LSB executable" if lv and not lv.mitigations.get("pie") else "LSB pie executable"
            desc = (
                f"ELF 64-bit {pie}, x86-64, version 1 (SYSV), dynamically linked, "
                f"interpreter /lib64/ld-linux-x86-64.so.2, BuildID[sha1]={build}, "
                "for GNU/Linux 3.2.0, not stripped"
            )
        elif not node.data:
            desc = "empty"
        elif all(b < 128 and (b >= 32 or b in (9, 10, 13)) for b in node.data):
            desc = "ASCII text"
        else:
            desc = "data"
        out.append(f"{a}: {desc}\n")
    return _errres(errs, "".join(out))


# ------------------------------------------------------------- bash and vim
@command("bash", "sh")
def bash(sh, args, stdin):
    flags, pos = parse_flags(args, "c")
    if "c" in flags:
        return sh.run_captured(str(flags["c"]))
    if pos:
        try:
            text = _text(sh.vfs.read(pos[0], sh.cwd))
        except VFSError as e:
            return fail(f"bash: {e}", 127)
        return sh.run_captured(text, pos[1:], pos[0])
    return fail("bash: nested interactive shells are not supported (use `bash -c ...`)")


@command("vim", "vi", "nano")
def vim(sh, args, stdin):
    _, pos = parse_flags(args)
    if not pos:
        return Result(action=("edit", None, ""))
    path = sh.abspath(pos[0])
    try:
        node = sh.vfs.get(path)
        if node.is_dir:
            return fail(f'"{pos[0]}" is a directory')
        if not sh.vfs.can_read(node):
            return fail(f'"{pos[0]}" E484: Can\'t open file {pos[0]}')
    except VFSError:
        pass
    return Result(action=("edit", path, pos[0]))


@command("less", "more")
def less_(sh, args, stdin):
    _, pos = parse_flags(args)
    if not pos and not stdin:
        return fail('Missing filename ("less --help" for help)')
    inputs, errs = _read_inputs(sh, "less", pos[:1], stdin)
    if errs:
        return _errres(errs)
    name = pos[0] if pos else "standard input"
    text = inputs[0][1].decode("utf-8", errors="replace")
    if not sh.tty:
        return ok(text)  # stdout is not a terminal: page through it like cat instead
    return Result(action=("page", text, name))


from . import extra, gdb, more, netsession, nettools, pwn, pyinterp, r2  # noqa: E402,F401  (register additional programs)
