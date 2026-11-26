"""More fake programs: text filters, find/tree, sed/awk subsets, ps, nc, printf, ..."""

from __future__ import annotations

import re

from ..engine.vfs import Node, VFSError
from .commands import _errres, _read_inputs, _text, command, parse_flags
from .shell import Result, fail, ok


def _lines(data: bytes) -> list[str]:
    return _text(data).splitlines()


def _join(lines: list[str]) -> str:
    return "".join(l + "\n" for l in lines)


# ---------------------------------------------------------------- filters
@command("sort")
def sort(sh, args, stdin):
    flags, pos = parse_flags(args)
    inputs, errs = _read_inputs(sh, "sort", pos, stdin)
    lines = [l for _, d in inputs for l in _lines(d)]

    def key(l: str):
        if flags.get("n"):
            m = re.match(r"\s*(-?\d+(?:\.\d+)?)", l)
            return (float(m.group(1)) if m else 0.0, l)
        return (l.lower(), l) if flags.get("f") else (l,)

    lines.sort(key=key, reverse=bool(flags.get("r")))
    if flags.get("u"):
        seen, uniq = set(), []
        for l in lines:
            if l not in seen:
                seen.add(l)
                uniq.append(l)
        lines = uniq
    return _errres(errs, _join(lines))


@command("uniq")
def uniq(sh, args, stdin):
    flags, pos = parse_flags(args)
    inputs, errs = _read_inputs(sh, "uniq", pos[:1], stdin)
    out: list[tuple[str, int]] = []
    for _, d in inputs:
        for l in _lines(d):
            if out and out[-1][0] == l:
                out[-1] = (l, out[-1][1] + 1)
            else:
                out.append((l, 1))
    if flags.get("d"):
        out = [(l, c) for l, c in out if c > 1]
    text = "".join((f"{c:>7} {l}\n" if flags.get("c") else f"{l}\n") for l, c in out)
    return _errres(errs, text)


@command("tee")
def tee(sh, args, stdin):
    flags, pos = parse_flags(args)
    errs = []
    for f in pos:
        try:
            sh.vfs.write(f, stdin, sh.cwd, append=bool(flags.get("a")))
        except VFSError as e:
            errs.append(f"tee: {e}")
    res = _errres(errs)
    res.out = stdin
    return res


@command("rev")
def rev(sh, args, stdin):
    inputs, errs = _read_inputs(sh, "rev", args, stdin)
    return _errres(errs, _join([l[::-1] for _, d in inputs for l in _lines(d)]))


def _parse_ranges(spec: str) -> list[int] | None:
    out: list[int] = []
    for part in spec.split(","):
        m = re.fullmatch(r"(\d*)-(\d*)|(\d+)", part)
        if not m:
            return None
        if m.group(3):
            out.append(int(m.group(3)))
        else:
            lo, hi = int(m.group(1) or 1), int(m.group(2) or 200)
            out.extend(range(lo, hi + 1))
    return out


@command("cut")
def cut(sh, args, stdin):
    flags, pos = parse_flags(args, "dfc")
    delim = str(flags.get("d", "\t"))
    spec = flags.get("f") or flags.get("c")
    if not spec:
        return fail("cut: you must specify a list of bytes, characters, or fields", 1)
    idx = _parse_ranges(str(spec))
    if idx is None:
        return fail(f"cut: invalid field range: '{spec}'")
    inputs, errs = _read_inputs(sh, "cut", pos, stdin)
    out = []
    for _, d in inputs:
        for l in _lines(d):
            if "c" in flags:
                out.append("".join(l[i - 1] for i in idx if i <= len(l)))
            else:
                parts = l.split(delim)
                if len(parts) == 1 and delim not in l:
                    out.append(l)
                else:
                    out.append(delim.join(parts[i - 1] for i in idx if i <= len(parts)))
    return _errres(errs, _join(out))


def _expand_set(s: str) -> str:
    out, i = [], 0
    s = s.replace("\\n", "\n").replace("\\t", "\t")
    while i < len(s):
        if i + 2 < len(s) and s[i + 1] == "-":
            out.extend(chr(c) for c in range(ord(s[i]), ord(s[i + 2]) + 1))
            i += 3
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


@command("tr")
def tr(sh, args, stdin):
    flags, pos = parse_flags(args)
    text = _text(stdin)
    if flags.get("d"):
        if not pos:
            return fail("tr: missing operand")
        drop = set(_expand_set(pos[0]))
        return ok("".join(c for c in text if c not in drop))
    if len(pos) < 2:
        return fail("tr: missing operand")
    a, b = _expand_set(pos[0]), _expand_set(pos[1])
    b = b.ljust(len(a), b[-1] if b else "")
    return ok(text.translate({ord(x): y for x, y in zip(a, b)}))


@command("seq")
def seq(sh, args, stdin):
    try:
        nums = [int(a) for a in args]
    except ValueError:
        return fail(f"seq: invalid argument '{args[0]}'")
    if not nums or len(nums) > 3:
        return fail("seq: missing operand")
    first, step, last = (1, 1, nums[0]) if len(nums) == 1 else (nums[0], 1, nums[1]) if len(nums) == 2 else (nums[0], nums[1], nums[2])
    if step == 0 or abs(last - first) > 100000:
        return fail("seq: invalid increment")
    rng = range(first, last + (1 if step > 0 else -1), step)
    return ok(_join([str(i) for i in rng]))


@command("basename")
def basename(sh, args, stdin):
    if not args:
        return fail("basename: missing operand")
    name = args[0].rstrip("/").rsplit("/", 1)[-1] or "/"
    if len(args) > 1 and name.endswith(args[1]) and name != args[1]:
        name = name[: -len(args[1])]
    return ok(name + "\n")


@command("dirname")
def dirname(sh, args, stdin):
    if not args:
        return fail("dirname: missing operand")
    p = args[0].rstrip("/")
    return ok((p.rsplit("/", 1)[0] or ("/" if p.startswith("/") else ".")) + "\n" if "/" in p else ".\n")


@command("printf")
def printf(sh, args, stdin):
    if not args:
        return fail("printf: usage: printf format [arguments]")
    fmt, rest = args[0], list(args[1:])

    def esc(t: str) -> str:
        return re.sub(
            r"\\(x[0-9a-fA-F]{1,2}|[0-7]{1,3}|.)",
            lambda m: {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "0": "\0"}.get(m.group(1))
            or (chr(int(m.group(1)[1:], 16)) if m.group(1)[0] == "x" and len(m.group(1)) > 1
                else chr(int(m.group(1), 8)) if m.group(1)[0] in "01234567" else m.group(1)),
            t,
        )

    def once() -> str:
        def sub(m):
            conv = m.group(2)
            if conv == "%":
                return "%"
            val = rest.pop(0) if rest else ""
            try:
                if conv in "di":
                    return f"%{m.group(1)}d" % int(val or 0, 0)
                if conv in "xXo":
                    return f"%{m.group(1)}{conv}" % int(val or 0, 0)
                if conv == "c":
                    return val[:1]
            except ValueError:
                return "0"
            return f"%{m.group(1)}s" % val

        return re.sub(r"%([-0-9.]*)([sdixXoc%])", sub, esc(fmt))

    out = once()
    guard = 0
    while rest and guard < 1000 and re.search(r"%[-0-9.]*[sdixXoc]", fmt):
        out += once()
        guard += 1
    return Result(out=out.encode("latin-1", errors="replace"))


# ------------------------------------------------------------ find / tree
def _walk(sh, node: Node, path: str, depth: int, maxdepth: int | None, errs: list[str]):
    yield path, node, depth
    if not node.is_dir or (maxdepth is not None and depth >= maxdepth):
        return
    if not sh.vfs.can_read(node):
        errs.append(f"find: '{path}': Permission denied")
        return
    for name in sorted(node.children):
        yield from _walk(sh, node.children[name], path.rstrip("/") + "/" + name, depth + 1, maxdepth, errs)


@command("find")
def find(sh, args, stdin):
    paths, i = [], 0
    while i < len(args) and not args[i].startswith("-"):
        paths.append(args[i])
        i += 1
    name_pat = ftype = perm_mask = None
    maxdepth = None
    while i < len(args):
        a = args[i]
        val = args[i + 1] if i + 1 < len(args) else None
        if a in ("-name", "-iname") and val is not None:
            name_pat = (val, a == "-iname")
        elif a == "-type" and val in ("f", "d"):
            ftype = val
        elif a == "-maxdepth" and val is not None and val.isdigit():
            maxdepth = int(val)
        elif a == "-perm" and val is not None and val.startswith("-") and val[1:].isdigit():
            perm_mask = int(val[1:], 8)  # "-4000": every bit in the mask must be set (the find convention)
        elif a in ("-print",):
            i += 1
            continue
        else:
            return fail(f"find: unknown predicate `{a}'")
        i += 2
    out, errs = [], []
    import fnmatch

    for p in paths or ["."]:
        try:
            root = sh.vfs.get(p, sh.cwd)
        except VFSError:
            errs.append(f"find: '{p}': No such file or directory")
            continue
        for path, node, _ in _walk(sh, root, p, 0, maxdepth, errs):
            if ftype == "f" and node.is_dir or ftype == "d" and not node.is_dir:
                continue
            if perm_mask is not None and node.mode & perm_mask != perm_mask:
                continue
            if name_pat:
                pat, ci = name_pat
                base = path.rstrip("/").rsplit("/", 1)[-1] or "/"
                if not (fnmatch.fnmatch(base.lower(), pat.lower()) if ci else fnmatch.fnmatchcase(base, pat)):
                    continue
            out.append(path)
    return _errres(errs, _join(out))


@command("tree")
def tree(sh, args, stdin):
    flags, pos = parse_flags(args)
    root_arg = pos[0] if pos else "."
    try:
        root = sh.vfs.get(root_arg, sh.cwd)
    except VFSError:
        return fail(f"{root_arg} [error opening dir]")
    counts = [0, 0]
    lines = [root_arg]

    def rec(node: Node, prefix: str) -> None:
        if not sh.vfs.can_read(node):
            return
        kids = [node.children[k] for k in sorted(node.children) if flags.get("a") or not k.startswith(".")]
        for idx, k in enumerate(kids):
            last = idx == len(kids) - 1
            lines.append(f"{prefix}{'└── ' if last else '├── '}{k.name}")
            counts[0 if k.is_dir else 1] += 1
            if k.is_dir:
                rec(k, prefix + ("    " if last else "│   "))

    if root.is_dir:
        rec(root, "")
    lines.append("")
    lines.append(f"{counts[0]} director{'y' if counts[0] == 1 else 'ies'}, {counts[1]} file{'' if counts[1] == 1 else 's'}")
    return ok(_join(lines))


# ------------------------------------------------------------- sed (subset)
def _parse_sed(script: str):
    """Yield (addr, cmd, args). addr: None | ('n',k) | ('$',) | ('re',rx) | ('range',a,b)."""
    i, n, cmds = 0, len(script), []

    def addr(i):
        if i < n and script[i].isdigit():
            j = i
            while j < n and script[j].isdigit():
                j += 1
            return ("n", int(script[i:j])), j
        if i < n and script[i] == "$":
            return ("$",), i + 1
        if i < n and script[i] == "/":
            j = script.find("/", i + 1)
            if j < 0:
                raise ValueError("unterminated address regex")
            return ("re", re.compile(script[i + 1 : j])), j + 1
        return None, i

    while i < n:
        while i < n and script[i] in " \t;\n":
            i += 1
        if i >= n:
            break
        a1, i = addr(i)
        a2 = None
        if a1 and i < n and script[i] == ",":
            a2, i = addr(i + 1)
            if a2 is None:
                raise ValueError("unexpected `,'")
        if i >= n:
            raise ValueError("missing command")
        c = script[i]
        i += 1
        if c in "dp":
            cmds.append((("range", a1, a2) if a2 else a1, c, None))
        elif c == "s":
            if i >= n:
                raise ValueError("unterminated `s' command")
            d = script[i]
            parts, cur, j = [], [], i + 1
            while j < n and len(parts) < 2:
                if script[j] == "\\" and j + 1 < n:
                    cur.append(script[j : j + 2] if script[j + 1] != d else d)
                    j += 2
                elif script[j] == d:
                    parts.append("".join(cur))
                    cur = []
                    j += 1
                else:
                    cur.append(script[j])
                    j += 1
            if len(parts) < 2:
                raise ValueError("unterminated `s' command")
            flags = ""
            while j < n and script[j] in "gpi":
                flags += script[j]
                j += 1
            rep = re.sub(r"&", r"\\g<0>", parts[1])
            cmds.append((("range", a1, a2) if a2 else a1, "s", (re.compile(parts[0], re.I if "i" in flags else 0), rep, flags)))
            i = j
        else:
            raise ValueError(f"unknown command: `{c}'")
    return cmds


def _addr_match(a, lineno: int, total: int, line: str, state: dict, idx: int) -> bool:
    if a is None:
        return True
    if a[0] == "n":
        return lineno == a[1]
    if a[0] == "$":
        return lineno == total
    if a[0] == "re":
        return bool(a[1].search(line))
    _, lo, hi = a
    if state.get(idx):
        if _addr_match(hi, lineno, total, line, state, -1):
            state[idx] = False
        return True
    if _addr_match(lo, lineno, total, line, state, -1):
        if not (hi[0] == "n" and hi[1] <= lineno) and not _addr_match(hi, lineno, total, line, state, -1):
            state[idx] = True
        return True
    return False


@command("sed")
def sed(sh, args, stdin):
    flags, pos = parse_flags(args, "e")
    script = str(flags["e"]) if "e" in flags else (pos.pop(0) if pos else None)
    if script is None:
        return fail("Usage: sed [OPTION]... {script-only-if-no-other-script} [input-file]...")
    try:
        cmds = _parse_sed(script)
    except (ValueError, re.error) as e:
        return fail(f"sed: -e expression #1, char {len(script)}: {e}")
    inputs, errs = _read_inputs(sh, "sed", pos, stdin)
    results = []
    for label, data in inputs:
        lines = _lines(data)
        out, state = [], {}
        for no, line in enumerate(lines, 1):
            deleted, printed = False, False
            for idx, (a, c, extra_) in enumerate(cmds):
                if not _addr_match(a, no, len(lines), line, state, idx):
                    continue
                if c == "d":
                    deleted = True
                    break
                if c == "p":
                    out.append(line)
                elif c == "s":
                    rx, rep, fl = extra_
                    line, k = rx.subn(rep, line, count=0 if "g" in fl else 1)
                    if k and "p" in fl:
                        out.append(line)
            if not deleted and not flags.get("n"):
                out.append(line)
        results.append((label, _join(out)))
    if flags.get("i"):
        for label, text in results:
            if label != "-":
                try:
                    sh.vfs.write(label, text.encode(), sh.cwd)
                except VFSError as e:
                    errs.append(f"sed: {e}")
        return _errres(errs)
    return _errres(errs, "".join(t for _, t in results))


# ------------------------------------------------------------- awk (subset)
def _awk_split(prog: str):
    m = re.fullmatch(r"\s*(?:(BEGIN|END)\s*)?(?:(/.*?/|[^{]*?)\s*)?\{(.*)\}\s*", prog, re.S) or None
    if not m:
        m2 = re.fullmatch(r"\s*(/.*/|[^{]+?)\s*", prog)
        if m2:
            return None, m2.group(1).strip(), "print"
        raise ValueError("unsupported program")
    return m.group(1), (m.group(2) or "").strip() or None, m.group(3).strip()


def _awk_eval(expr: str, fields: list[str], nr: int):
    """Evaluate one print operand: $N, $NF, NF, NR, "str", number; juxtaposition concatenates."""
    toks = re.findall(r'"(?:[^"\\]|\\.)*"|\$\w+|\w+|\S', expr)
    out = []
    for t in toks:
        if t.startswith('"'):
            out.append(t[1:-1].encode().decode("unicode_escape"))
        elif t == "$0":
            out.append(fields[0])
        elif t == "$NF":
            out.append(fields[-1] if len(fields) > 1 else "")
        elif t.startswith("$") and t[1:].isdigit():
            k = int(t[1:])
            out.append(fields[k] if k < len(fields) else "")
        elif t == "NF":
            out.append(str(len(fields) - 1))
        elif t == "NR":
            out.append(str(nr))
        elif re.fullmatch(r"\d+(\.\d+)?", t):
            out.append(t)
        else:
            raise ValueError(f"unsupported expression: {t}")
    return "".join(out)


def _awk_cond(cond: str | None, fields: list[str], nr: int) -> bool:
    if cond is None:
        return True
    if cond.startswith("/") and cond.endswith("/") and len(cond) > 1:
        return bool(re.search(cond[1:-1], fields[0]))
    m = re.fullmatch(r"(\$\w+|NR|NF)\s*(==|!=|>=|<=|>|<|~)\s*(.+)", cond)
    if not m:
        raise ValueError("unsupported pattern")
    lhs = _awk_eval(m.group(1), fields, nr)
    rhs = m.group(3).strip()
    rhs = rhs[1:-1] if rhs.startswith('"') and rhs.endswith('"') else rhs
    op = m.group(2)
    if op == "~":
        return bool(re.search(rhs.strip("/"), lhs))
    try:
        a, b = float(lhs), float(rhs)
    except ValueError:
        a, b = lhs, rhs
    return {"==": a == b, "!=": a != b, ">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b}[op]


@command("awk")
def awk(sh, args, stdin):
    flags, pos = parse_flags(args, "F")
    if not pos:
        return fail("usage: awk [-F fs] 'program' [file ...]")
    prog, files = pos[0], pos[1:]
    sep = str(flags["F"]) if "F" in flags else None
    try:
        kind, cond, body = _awk_split(prog)
    except ValueError as e:
        return fail(f"awk: {e}")
    inputs, errs = _read_inputs(sh, "awk", files, stdin)
    out, nr = [], 0
    try:
        for _, data in inputs:
            for line in _lines(data):
                nr += 1
                if kind in ("BEGIN", "END"):
                    continue
                parts = re.split(sep, line) if sep and len(sep) > 1 else (line.split(sep) if sep else line.split())
                fields = [line] + parts
                if not _awk_cond(cond, fields, nr):
                    continue
                if not body.startswith("print"):
                    raise ValueError("unsupported action")
                items = body[5:].strip()
                if not items:
                    out.append(line)
                else:
                    pieces = re.split(r",(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)", items)
                    out.append(" ".join(_awk_eval(p.strip(), fields, nr) for p in pieces))
        if kind in ("BEGIN", "END") and body.startswith("print"):
            items = body[5:].strip()
            fields = [""]
            out.append(" ".join(_awk_eval(p.strip(), fields, nr) for p in items.split(",")) if items else "")
    except ValueError as e:
        return fail(f"awk: {e}")
    return _errres(errs, _join(out))


# --------------------------------------------------------- system flavour
@command("ps")
def ps(sh, args, stdin):
    user = sh.vfs.user
    if args and any(a in ("aux", "-ef", "ef", "-aux") for a in args):
        rows = [
            ("root", 1, "/sbin/init"),
            ("root", 412, "/usr/sbin/cron -f"),
            ("root", 488, "/usr/sbin/sshd -D"),
            (user, 2301, "-bash"),
            (user, 2388, "ps " + " ".join(args)),
        ]
        head = "USER         PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND\n"
        return ok(head + "".join(f"{u:<10} {p:>5}  0.0  0.1  10240  3456 ?        Ss   01:00   0:00 {c}\n" for u, p, c in rows))
    return ok("    PID TTY          TIME CMD\n   2301 pts/0    00:00:00 bash\n   2388 pts/0    00:00:00 ps\n")


@command("nc", "ncat", "netcat")
def nc(sh, args, stdin):
    _, pos = parse_flags(args)
    if len(pos) < 2:
        return fail("usage: nc [-options] hostname port[s] [ports] ...", 1)
    return fail(f"nc: connect to {pos[0]} port {pos[1]} (tcp) failed: Connection refused", 1)


@command("uptime")
def uptime(sh, args, stdin):
    return ok(" 01:07:42 up 3 days,  4:12,  1 user,  load average: 0.00, 0.01, 0.05\n")


# --------------------------------------------------------------- checksec
def _mitigation_rows(level) -> list[tuple[str, str]]:
    """(label, text) rows in pwntools' wording, from the level's mitigation table."""
    m = level.mitigations if level else {"relro": "full", "canary": True, "nx": True, "pie": True}
    relro = {"full": "Full RELRO", "partial": "Partial RELRO"}.get(str(m.get("relro", "no")).lower(), "No RELRO")
    pie = f"PIE enabled" if m.get("pie") else f"No PIE (0x{level.base:x})" if level else "No PIE"
    return [
        ("RELRO", relro),
        ("Stack", "Canary found" if m.get("canary") else "No canary found"),
        ("NX", "NX enabled" if m.get("nx") else "NX unknown - GNU_STACK missing"),
        ("PIE", pie),
    ]


@command("checksec")
def checksec(sh, args, stdin):
    flags, pos = parse_flags([a for a in args if not a.startswith("--file=")], "")
    files = pos + [a[len("--file=") :] for a in args if a.startswith("--file=")]
    script_style = any(a.startswith("--file=") for a in args)
    if not files:
        return fail("usage: checksec [--file=]FILE")
    out, errs = [], []
    for name in files:
        try:
            node = sh.vfs.get(name, sh.cwd)
        except VFSError:
            errs.append(f"checksec: cannot open {name}: No such file or directory")
            continue
        if node.is_dir or not node.data.startswith(b"\x7fELF"):
            errs.append(f"checksec: {name}: not an ELF file")
            continue
        level = sh.world.level_for(node)
        sh.world.events.add("checksec:used")
        rows = _mitigation_rows(level)
        if script_style:
            col = {"RELRO": rows[0][1], "STACK CANARY": rows[1][1], "NX": rows[2][1], "PIE": rows[3][1]}
            out.append(
                f"{'RELRO':<15} {'STACK CANARY':<17} {'NX':<13} {'PIE':<18} {'RPATH':<10} {'RUNPATH':<12} {'Symbols':<15} FILE\n"
                f"{col['RELRO']:<15} {col['STACK CANARY']:<17} {col['NX']:<13} {col['PIE']:<18} {'No RPATH':<10} {'No RUNPATH':<12} {'6 Symbols':<15} {name}\n"
            )
        else:
            out.append(f"[*] '{sh.abspath(name)}'\n    Arch:     amd64-64-little\n" + "".join(f"    {k + ':':<9} {v}\n" for k, v in rows))
    return _errres(errs, "".join(out))


# ------------------------------------------------------------------- base64
@command("base64")
def base64_(sh, args, stdin):
    import base64 as _b64

    flags, pos = parse_flags(args, "")
    inputs, errs = _read_inputs(sh, "base64", pos[:1], stdin)
    if errs:
        return _errres(errs)
    data = inputs[0][1]
    if flags.get("d") or flags.get("decode"):
        try:
            return Result(out=_b64.b64decode(data, validate=False))
        except Exception:
            return fail("base64: invalid input")
    enc = _b64.b64encode(data).decode()
    wrapped = "\n".join(enc[i : i + 76] for i in range(0, len(enc), 76)) or ""
    return ok(wrapped + "\n" if wrapped else "\n")


# --------------------------------------------------------------- md5/sha256sum
def _hashsum(name: str, algo: str):
    import hashlib

    @command(name)
    def run(sh, args, stdin):
        flags, pos = parse_flags(args, "")
        inputs, errs = _read_inputs(sh, name, pos, stdin)
        lines = []
        for label, data in inputs:
            digest = hashlib.new(algo, data).hexdigest()
            lines.append(f"{digest}  {label}\n")
        return _errres(errs, "".join(lines))

    return run


_hashsum("md5sum", "md5")
_hashsum("sha1sum", "sha1")
_hashsum("sha256sum", "sha256")


# -------------------------------------------------------------------- diff
@command("diff")
def diff_(sh, args, stdin):
    import difflib

    flags, pos = parse_flags(args, "")
    if len(pos) != 2:
        return fail("usage: diff [-u] FILE1 FILE2", 2)
    errs = []
    sides = []
    for p in pos:
        try:
            sides.append(_lines(sh.vfs.read(p, sh.cwd)))
        except VFSError as e:
            errs.append(f"diff: {e}")
    if errs:
        return _errres(errs, "", )
    a, b = sides
    if flags.get("u"):
        out = "\n".join(difflib.unified_diff(a, b, pos[0], pos[1], lineterm="")) + "\n"
        return Result(out=out.encode(), status=0 if a == b else 1)
    out = []
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue

        def rng(lo, hi):
            # an empty range (nothing on this side) is printed as the line it falls after, not lo+1
            if hi == lo:
                return f"{lo}"
            return f"{lo + 1}" if hi - lo == 1 else f"{lo + 1},{hi}"

        code = {"replace": "c", "delete": "d", "insert": "a"}[tag]
        out.append(f"{rng(i1, i2)}{code}{rng(j1, j2)}\n")
        if tag in ("replace", "delete"):
            out += [f"< {ln}\n" for ln in a[i1:i2]]
        if tag == "replace":
            out.append("---\n")
        if tag in ("replace", "insert"):
            out += [f"> {ln}\n" for ln in b[j1:j2]]
    return Result(out="".join(out).encode(), status=0 if a == b else 1)


# --------------------------------------------------------------------- tar
def _tar_collect(sh, path: str, errs: list[str]):
    """Yield (arcname, data) for path (relative to sh.cwd), recursing into directories."""
    try:
        node = sh.vfs.get(path, sh.cwd)
    except VFSError as e:
        errs.append(f"tar: {path}: Cannot stat: {e}")
        return
    arc = path.strip("/") or "."
    if not node.is_dir:
        if sh.vfs.can_read(node):
            yield arc, node.data
        else:
            errs.append(f"tar: {path}: Cannot open: Permission denied")
        return
    for name in sorted(node.children):
        yield from _tar_collect(sh, path.rstrip("/") + "/" + name, errs)


@command("tar")
def tar(sh, args, stdin):
    import tarfile
    from io import BytesIO

    mode, gz, archive, chdir, pos = None, False, None, None, []
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("-") and len(a) > 1 and not a.startswith("--"):
            for ch in a[1:]:
                if ch in "cxt":
                    mode = ch
                elif ch == "z":
                    gz = True
                elif ch == "v":
                    pass
                elif ch == "f":
                    i += 1
                    archive = args[i] if i < len(args) else None
                elif ch == "C":
                    i += 1
                    chdir = args[i] if i < len(args) else None
                else:
                    return fail(f"tar: invalid option -- '{ch}'", 2)
        else:
            pos.append(a)
        i += 1
    if mode is None or archive is None:
        return fail("tar: refusing to read archive contents from terminal (missing -f option?)", 2)
    gz = gz or archive.endswith(".gz") or archive.endswith(".tgz")
    tar_mode = {"c": "w", "x": "r", "t": "r"}[mode] + (":gz" if gz else "")

    if mode == "c":
        if not pos:
            return fail("tar: Cowardly refusing to create an empty archive", 2)
        errs: list[str] = []
        buf = BytesIO()
        with tarfile.open(fileobj=buf, mode=tar_mode) as tf:
            for p in pos:
                for arcname, data in _tar_collect(sh, p, errs):
                    info = tarfile.TarInfo(name=arcname)
                    info.size = len(data)
                    tf.addfile(info, BytesIO(data))
        try:
            sh.vfs.write(archive, buf.getvalue(), sh.cwd)
        except VFSError as e:
            errs.append(f"tar: {e}")
        return _errres(errs)

    try:
        data = sh.vfs.read(archive, sh.cwd)
    except VFSError as e:
        return fail(f"tar: {e}", 2)
    try:
        tf = tarfile.open(fileobj=BytesIO(data), mode=tar_mode)
    except tarfile.TarError:
        return fail(f"tar: {archive}: not a valid tar archive", 2)
    with tf:
        members = tf.getmembers()
        if mode == "t":
            return ok("".join((m.name + "/\n" if m.isdir() else m.name + "\n") for m in members))
        errs = []
        for m in members:
            if m.isdir():
                continue
            target = (chdir.rstrip("/") + "/" + m.name) if chdir else m.name
            try:
                parent = target.rsplit("/", 1)[0] if "/" in target else ""
                if parent:
                    sh.vfs.mkdir(parent, sh.cwd, parents=True)
                sh.vfs.write(target, tf.extractfile(m).read(), sh.cwd)
            except VFSError as e:
                errs.append(f"tar: {m.name}: {e}")
        return _errres(errs)
