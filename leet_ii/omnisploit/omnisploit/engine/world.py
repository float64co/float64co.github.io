"""The simulated host the player is logged in to."""

from __future__ import annotations

import random

from . import lab as _lab
from .machine import Level, Process, load_all_levels
from .network import Host, Network
from .missions import ACME_TOKEN, BLOB_SECRET, CULPRIT_IP, MissionLog
from .vfs import VFS, Node


def _random_flag(seed: int, level_id: str) -> str:
    """A flag unique to this (seed, level) pair, but the same every time this seed loads this level."""
    rng = random.Random(f"omnisploit-flag:{seed}:{level_id}")
    token = "".join(rng.choice("0123456789abcdefghijklmnopqrstuvwxyz") for _ in range(20))
    return f"FLAG{{{token}}}"


README_MOTD = """\
== motd-daemon ==================================================

A small greeter service from the Brightside intranet. It asks for a
name and says hello. Ops swears it has been "audited".

You are an unprivileged user on this box. The flag lives in
/opt/motd/flag.txt and is only readable by root -- but the motd binary
has a helper that runs with higher privileges...

Goal: make ./motd read the flag for you.

Toolbox: file, strings, xxd, checksec, cyclic, gdb, r2, python3, vim and the usual shell tools. Try the
program (`./motd`) before you try to break it, and keep notes in
~/workspace. Type `help` for everything available.
"""

ETC_PASSWD = """\
root:x:0:0:root:/root:/bin/bash
daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin
{user}:x:1000:1000:{user},,,:/home/{user}:/bin/bash
"""


class World:
    def __init__(self, user: str = "player", hostname: str = "lab", missions: bool = True,
                 network: Network | None = None, vantage: str | None = None, lite: bool = False,
                 flag_seed: int | None = None) -> None:
        self.lite = lite  # a remote machine: just the basics, no game targets
        self.net: Network = network if network is not None else _lab.build(Network(), seed=flag_seed)
        self.net.world_factory = self.net.world_factory or World
        self.vantage = vantage or self.net.player_ip  # the IP this machine has on the network
        self.levels: dict[str, Level] = {} if lite else load_all_levels()
        self.flag_seed = flag_seed
        if flag_seed is not None:
            # a per-player seed, stable across reloads (saved and restored), so a walkthrough's exact
            # flag text does not work for anyone else -- the technique to find it is unchanged.
            for lv in self.levels.values():
                old_flag = lv.flag
                lv.flag = _random_flag(flag_seed, lv.id)
                # a level may also bake its flag text directly into the binary's own data (e.g. a
                # format-string leak reads it straight out of memory, never off disk) -- keep that
                # copy in sync too, or the embedded string stays the stale literal from the TOML
                # forever and can never match the solved-check's randomized lv.flag.
                for addr, text in lv.strings.items():
                    if text == old_flag:
                        lv.strings[addr] = lv.flag
        self.user = user
        self.home = f"/home/{user}"
        self.vfs = VFS(user)
        self.hostname = hostname
        self._pid = 4241
        self.events: set[str] = set()  # things the player has done (feeds missions)
        self._build()
        self.missions: MissionLog | None = None
        if missions and not lite:
            self.missions = MissionLog(self)
            self.missions.refresh_file()

    def remote(self, host: Host, user: str):
        """The shell for ``user`` on a simulated host; created on first use and kept for the session."""
        from ..shell.shell import Shell

        key = (host.name, user)
        cache = self.net.remote_worlds
        if key not in cache:
            w = World(user, host.name.split(".")[0], missions=False, network=self.net, vantage=host.ips[0], lite=True)
            w.events = self.events  # what you do on a remote box counts toward your targets
            for path, spec in host.files.items():
                if isinstance(spec, tuple):
                    text, owner, mode = spec
                else:
                    text = spec
                    owner = path.split("/")[2] if path.startswith("/home/") else "root"
                    mode = 0o600 if "/.ssh/" in path else 0o644
                w.vfs.install(path, text, owner=owner, mode=mode)
            w.vfs.install("/etc/motd", host.motd)
            if host.level_id and host.level_id in self.levels:
                lv = self.levels[host.level_id]
                w.levels[lv.id] = lv  # so level_for() resolves the binary once it is run
                w.vfs.install(lv.binary_path, lv.build_elf(), owner="root", mode=0o4755, program=f"level:{lv.id}")
                w.vfs.install_dir(lv.flag_path.rsplit("/", 1)[0])
                w.vfs.install(lv.flag_path, lv.flag + "\n", owner="root", mode=0o600)
            from .save import snapshot

            self.net.remote_pristine[key] = snapshot(w)
            sh = Shell(w)
            cache[key] = sh
        return cache[key]

    def new_process(self, level: Level, suid: bool = False) -> Process:
        self._pid += 1
        return Process(level, self._pid, suid=suid)

    def level_for(self, node: Node) -> Level | None:
        if node.program and node.program.startswith("level:"):
            return self.levels.get(node.program[6:])
        return None

    def _build(self) -> None:
        from ..shell.commands import REGISTRY, BUILTIN_ONLY  # local import: avoids a cycle

        v = self.vfs
        for d in ("/bin", "/usr/bin", "/etc", "/tmp", "/opt", "/root", "/dev", "/home"):
            v.install_dir(d)
        v.get("/tmp").mode = 0o1777
        v.install_dir(self.home, owner=self.user, mode=0o755)
        v.install("/etc/passwd", ETC_PASSWD.format(user=self.user))
        v.install("/etc/hostname", self.hostname + "\n")
        v.install("/etc/os-release", 'NAME="Ubuntu"\nVERSION="22.04.4 LTS (Jammy Jellyfish)"\n')
        v.install("/etc/hosts", f"127.0.0.1 localhost\n{self.vantage} {self.hostname}\n")
        v.install("/etc/resolv.conf", f"nameserver {self.net.gateway}\nsearch brightside.lab\n")
        v.install("/dev/null", b"", mode=0o666)
        for name in REGISTRY:
            if name in BUILTIN_ONLY:
                continue
            v.install(f"/usr/bin/{name}", f"\x7fELF fake {name}\n", mode=0o755, program=f"builtin:{name}")
        for name in ("bash", "sh", "ls", "cat"):
            v.install(f"/bin/{name}", f"\x7fELF fake {name}\n", mode=0o755, program=f"builtin:{name}")

        if self.lite:
            return
        self._build_acme()
        self._build_weblogs()
        self._build_drop()
        for lv in self.levels.values():
            if lv.remote_only:
                continue  # placed on a network host instead, by that Host's level_id
            lv.binary_path = lv.binary_path.replace("/home/player", self.home, 1)
            level_dir = lv.binary_path.rsplit("/", 1)[0]
            v.install_dir(level_dir, owner=self.user)
            v.install(lv.binary_path, lv.build_elf(), mode=0o755, program=f"level:{lv.id}")
            v.install(level_dir + "/README.txt", README_MOTD if lv.id == "motd-daemon" else lv.readme, owner=self.user)
            v.install_dir(lv.flag_path.rsplit("/", 1)[0])
            v.install(lv.flag_path, lv.flag + "\n", owner="root", mode=0o600)

    def _build_acme(self) -> None:
        """Scenery for the 'dig' target: one live token among stale ones."""
        v = self.vfs
        v.install_dir("/var/lib/acme")
        v.install("/var/lib/acme/.token", ACME_TOKEN + "\n")
        v.install("/var/lib/acme/.token.bak", "TKN-0000-expired\n")
        v.install(
            "/var/lib/acme/README",
            "acme agent state. The live token is in .token (readable by everyone).\n"
            ".token.bak and any *-cache copies are expired; do not use them.\n",
        )
        v.install_dir("/var/lib/acme/private", mode=0o700)
        v.install("/var/lib/acme/private/signing.key", "not for you\n", mode=0o600)
        v.install_dir("/var/tmp")
        v.install("/var/tmp/.token-cache", "TKN-1111-stale\n")
        v.install_dir("/var/log")
        v.install("/var/log/acme.log", "acme: agent started\nacme: token rotated\nacme: agent idle\n")

    def _build_weblogs(self) -> None:
        """Deterministic web access logs; one client got the admin export."""
        import random

        rng = random.Random(1337)
        ips = [f"198.51.100.{n}" for n in rng.sample(range(2, 250), 14)] + [f"192.0.2.{n}" for n in rng.sample(range(2, 250), 10)]
        ips = [ip for ip in ips if ip != CULPRIT_IP]
        paths = ["/", "/index.html", "/login", "/static/app.js", "/static/site.css", "/api/items", "/api/items/7", "/favicon.ico", "/about"]
        agents = ["Mozilla/5.0", "curl/8.4.0", "python-requests/2.31", "Googlebot/2.1"]

        def line(ts: int, ip: str, method: str, path: str, status: int, size: int) -> str:
            h, m, sec = 1 + ts // 3600, ts // 60 % 60, ts % 60
            return f'{ip} - - [22/Nov/2026:{h:02d}:{m:02d}:{sec:02d} +0000] "{method} {path} HTTP/1.1" {status} {size}'

        def make(count: int, with_culprit: bool) -> list[str]:
            rows = []
            t = 0
            for _ in range(count):
                t += rng.randint(1, 40)
                roll = rng.random()
                ip = rng.choice(ips)
                if roll < 0.80:
                    rows.append(line(t, ip, "GET", rng.choice(paths), rng.choice([200, 200, 200, 304, 404]), rng.randint(120, 9000)))
                elif roll < 0.88:
                    rows.append(line(t, ip, "POST", "/login", rng.choice([200, 302, 401]), rng.randint(80, 600)))
                elif roll < 0.94:
                    rows.append(line(t, ip, "GET", "/admin/login", rng.choice([200, 302]), rng.randint(300, 1200)))
                elif roll < 0.98:
                    rows.append(line(t, ip, "GET", "/admin/export", 403, 153))  # refused
                else:
                    rows.append(line(t, ip, "POST", "/admin/export", 405, 153))
            if with_culprit:
                at = rng.randint(count // 3, 2 * count // 3)
                rows.insert(at, line(0 + at * 14, CULPRIT_IP, "GET", "/admin/export", 200, 48213))
                # the culprit also browses normally, so grepping the IP alone proves nothing
                rows.insert(at - 5, line(at * 14 - 60, CULPRIT_IP, "GET", "/login", 200, 1432))
                rows.insert(at + 3, line(at * 14 + 20, CULPRIT_IP, "GET", "/admin/export", 403, 153))
            return rows

        v = self.vfs
        v.install_dir("/var/log/webapp")
        v.install("/var/log/webapp/access.log", "\n".join(make(420, True)) + "\n")
        v.install("/var/log/webapp/access.log.1", "\n".join(make(160, False)) + "\n")
        v.install("/var/log/webapp/error.log", "[error] upstream timed out\n[warn] slow query on /api/items\n")

    def _build_drop(self) -> None:
        """A partner-supplied opaque file with one real secret among decoy strings."""
        import random

        rng = random.Random(7)

        def junk(n: int) -> bytes:
            # bytes that rarely form long printable runs, so `strings` stays readable
            return bytes(b if b < 0x20 or b > 0x7E else b | 0x80 for b in rng.randbytes(n))

        parts = [
            b"\x89BLB\r\n\x1a\n",
            junk(180),
            b"\0build=2026.11.3\0",
            junk(64),
            b"\0cfg:mode=prod\0",
            junk(120),
            b"\0token-format: ORCHID-NNNN (4 digits)\0",
            junk(90),
            f"\0secret={BLOB_SECRET}\0".encode(),
            junk(200),
            b"\0notice: do not distribute\0",
            junk(48),
        ]
        v = self.vfs
        v.install_dir("/srv/drop")
        v.install("/srv/drop/artifact.bin", b"".join(parts))
        v.install("/srv/drop/NOTE", "Partner drop. artifact.bin is opaque data, not a program.\n")
