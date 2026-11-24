"""Shell builtins and helpers: alias, history, jobs, type, test, source, man, ..."""

from __future__ import annotations

import re

from ..engine.vfs import VFSError
from . import manpages
from .commands import BUILTIN_ONLY, REGISTRY, _errres, _text, command, parse_flags
from .shell import Result, fail, ok


# ---------------------------------------------------------------- aliases
@command("alias")
def alias(sh, args, stdin):
    if not args:
        return ok("".join(f"alias {k}='{v}'\n" for k, v in sorted(sh.aliases.items())))
    out, errs = [], []
    for a in args:
        if "=" in a:
            k, v = a.split("=", 1)
            if not re.fullmatch(r"[A-Za-z0-9_.!+,:@%-]+", k):
                errs.append(f"bash: alias: `{k}': invalid alias name")
            else:
                sh.aliases[k] = v
        elif a in sh.aliases:
            out.append(f"alias {a}='{sh.aliases[a]}'\n")
        else:
            errs.append(f"bash: alias: {a}: not found")
    return _errres(errs, "".join(out))


@command("unalias")
def unalias(sh, args, stdin):
    flags, pos = parse_flags(args)
    if flags.get("a"):
        sh.aliases.clear()
        return ok()
    errs = []
    for a in pos:
        if sh.aliases.pop(a, None) is None:
            errs.append(f"bash: unalias: {a}: not found")
    return _errres(errs)


# ---------------------------------------------------------------- history
@command("history")
def history(sh, args, stdin):
    flags, pos = parse_flags(args)
    if flags.get("c"):
        sh.history.clear()
        return ok()
    items = list(enumerate(sh.history, 1))
    if pos and pos[0].isdigit():
        items = items[-int(pos[0]) :]
    return ok("".join(f"{i:>5}  {l}\n" for i, l in items))


# ------------------------------------------------------------- job control
def _find_job(sh, spec: str | None):
    live = [j for j in sh.jobs if j.state == "Running"]
    if spec is None:
        return live[-1] if live else None
    if not spec.lstrip("%").isdigit():
        return None
    return next((j for j in live if j.num == int(spec.lstrip("%"))), None)


@command("jobs")
def jobs(sh, args, stdin):
    lines = []
    for j in sh.jobs:
        lines.append(f"[{j.num}]+  {j.state:<23} {j.text}{' &' if j.state == 'Running' and not j.text.endswith('&') else ''}\n")
    sh.jobs[:] = [j for j in sh.jobs if j.state == "Running"]
    return ok("".join(lines))


@command("fg")
def fg(sh, args, stdin):
    job = _find_job(sh, args[0] if args else None)
    if job is None:
        return fail(f"bash: fg: {args[0] if args else 'current'}: no such job" if args else "bash: fg: current: no such job")
    from .netsession import attach_listener  # local import: netsession builds on this module

    return attach_listener(sh, job)


@command("bg")
def bg(sh, args, stdin):
    job = _find_job(sh, args[0] if args else None)
    if job is None:
        return fail("bash: bg: current: no such job")
    return fail(f"bash: bg: job {job.num} already in background")


@command("wait")
def wait(sh, args, stdin):
    return ok()


@command("kill")
def kill(sh, args, stdin):
    _, pos = parse_flags(args)
    if not pos:
        return fail("kill: usage: kill [-s sigspec | -n signum | -sigspec] pid | jobspec ... or kill -l [sigspec]", 2)
    errs, done = [], []
    for p in pos:
        if p.startswith("%"):
            job = _find_job(sh, p)
            if job is None:
                errs.append(f"bash: kill: {p}: no such job")
            else:
                from .netsession import stop_listener

                stop_listener(sh, job)
                done.append(f"[{job.num}]+  Terminated              {job.text}\n")
            continue
        if not p.lstrip("%").isdigit():
            errs.append(f"bash: kill: {p}: arguments must be process or job IDs")
        else:
            errs.append(f"bash: kill: ({p}) - No such process")
    return _errres(errs, "".join(done))


REGISTRY["return"] = lambda sh, args, stdin: sh.builtin_return(args)
REGISTRY["local"] = lambda sh, args, stdin: sh.builtin_local(args)
REGISTRY["read"] = lambda sh, args, stdin: sh.builtin_read(args)


@command("unset")
def unset(sh, args, stdin):
    flags, pos = parse_flags(args)
    for name in pos:
        if flags.get("f"):
            sh.functions.pop(name, None)
        else:
            sh.env.pop(name, None)
    return ok()


@command("declare")
def declare(sh, args, stdin):
    flags, pos = parse_flags(args)
    if flags.get("f"):
        names = pos or sorted(sh.functions)
        bad = [n for n in names if n not in sh.functions]
        return _errres([f"bash: declare: {n}: not found" for n in bad], "".join(sh.function_source(n) + "\n" for n in names if n in sh.functions))
    if flags.get("F"):
        return ok("".join(f"declare -f {n}\n" for n in sorted(sh.functions)))
    for a in pos:
        if "=" in a:
            k, v = a.split("=", 1)
            sh.env[k] = v
    return ok()


REGISTRY["break"] = lambda sh, args, stdin: sh.loop_control("break", args)
REGISTRY["continue"] = lambda sh, args, stdin: sh.loop_control("continue", args)


# ----------------------------------------------------------------- source
@command("source", ".")
def source(sh, args, stdin):
    if not args:
        return fail("bash: source: filename argument required", 2)
    try:
        text = _text(sh.vfs.read(args[0], sh.cwd))
    except VFSError as e:
        return fail(f"bash: {args[0]}: {str(e).split(': ', 1)[-1]}")
    sh.run_script(text, args[1:] if len(args) > 1 else None, args[0])
    return Result(status=sh.status)


# --------------------------------------------------------------- type/man
@command("type")
def type_(sh, args, stdin):
    out, errs = [], []
    for a in args:
        if a in sh.aliases:
            out.append(f"{a} is aliased to `{sh.aliases[a]}'\n")
        elif a in sh.functions:
            out.append(f"{a} is a function\n{sh.function_source(a)}\n")
        elif a in BUILTIN_ONLY:
            out.append(f"{a} is a shell builtin\n")
        elif (found := sh.find_in_path(a)) is not None:
            out.append(f"{a} is {found[0]}\n")
        else:
            errs.append(f"bash: type: {a}: not found")
    return _errres(errs, "".join(out))


@command("man")
def man(sh, args, stdin):
    _, pos = parse_flags(args)
    if not pos:
        return fail("What manual page do you want?\nFor example, try 'man man'.", 1)
    out, errs = [], []
    for name in pos:
        page = manpages.render(name, min(sh.width, 80))
        if page is None:
            errs.append(f"No manual entry for {name}")
        else:
            out.append(page)
    return _errres(errs, "\n".join(out))


@command("help")
def help_(sh, args, stdin):
    if args:
        page = manpages.PAGES.get(args[0])
        if page is None:
            return fail(f"bash: help: no help topics match `{args[0]}'.  Try `man {args[0]}'.")
        summary, synopsis, _, _ = page
        return ok(f"{args[0]}: {synopsis}\n    {summary[0].upper()}{summary[1:]}.\n")
    names = sorted(n for n in REGISTRY if n not in ("nano", "logout", "vi", "sh", "[", ".", "ncat", "netcat"))
    return ok(
        "omnisploit shell -- everything here is simulated.\n\n"
        "Commands: " + ", ".join(names) + "\n\n"
        "Read ~/todo for your targets; take them on in any order.\n"
        "`man NAME` and `help NAME` describe each command and what it supports.\n"
        "Pipes, redirects, && || ;, $VARS, $(...), $((...)), globs, aliases, and\n"
        "if/for/while all work. Tab completes; PgUp/PgDn scroll; Ctrl-L clears.\n"
        "Progress is saved automatically.\n"
    )


# ------------------------------------------------------------------ test
def _int(s: str):
    try:
        return int(s)
    except ValueError:
        raise ValueError(f"{s}: integer expression expected")


def _eval_test(sh, a: list[str]) -> bool:
    if not a:
        return False
    if a[0] == "!":
        return not _eval_test(sh, a[1:])
    if len(a) == 1:
        return a[0] != ""
    if len(a) == 2:
        op, v = a
        if op == "-z":
            return v == ""
        if op == "-n":
            return v != ""
        if op not in ("-e", "-f", "-d", "-r", "-w", "-x", "-s"):
            raise ValueError(f"{op}: unary operator expected")
        try:
            node = sh.vfs.get(v, sh.cwd)
        except VFSError:
            return False
        return {
            "-e": True,
            "-f": not node.is_dir,
            "-d": node.is_dir,
            "-r": sh.vfs.can_read(node),
            "-w": sh.vfs.can_write(node),
            "-x": sh.vfs.can_exec(node),
            "-s": bool(node.data) or node.is_dir,
        }[op]
    if len(a) == 3:
        l, op, r = a
        if op in ("=", "=="):
            return l == r
        if op == "!=":
            return l != r
        ops = {"-eq": lambda x, y: x == y, "-ne": lambda x, y: x != y, "-lt": lambda x, y: x < y,
               "-le": lambda x, y: x <= y, "-gt": lambda x, y: x > y, "-ge": lambda x, y: x >= y}
        if op in ops:
            return ops[op](_int(l), _int(r))
        raise ValueError(f"{op}: binary operator expected")
    raise ValueError("too many arguments")


@command("test")
def test(sh, args, stdin):
    try:
        return ok(status=0 if _eval_test(sh, args) else 1)
    except ValueError as e:
        return fail(f"bash: test: {e}", 2)


@command("[")
def bracket(sh, args, stdin):
    if not args or args[-1] != "]":
        return fail("bash: [: missing `]'", 2)
    return test(sh, args[:-1], stdin)
