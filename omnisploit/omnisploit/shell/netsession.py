"""Interactive networking: nc (client and listener), ssh, and the sessions they open."""

from __future__ import annotations

import re

from ..engine.network import Channel, Listener, ShellChannel, UdpChannel
from ..engine.vfs import VFSError
from .commands import command, parse_flags
from .nettools import event, resolve
from .shell import Result, Session, Shell, fail, ok


# ----------------------------------------------------------------- sessions
class ChannelSession(Session):
    """A raw TCP conversation (nc to a service): what you type is sent, replies are printed."""

    prompt = ""

    def __init__(self, channel: Channel, greeting: bool = True) -> None:
        self.channel, self.greet = channel, greeting

    def banner(self) -> Result:
        return Result(out=self.channel.greeting() if self.greet else b"")

    def handle(self, line: str) -> Result:
        reply = self.channel.feed(line.encode() + b"\n")
        if self.channel.closed:
            self.done = True
        return Result(out=reply)

    def interrupt(self) -> None:
        self.done = True


class RemoteShellSession(Session):
    """A shell on another simulated machine (ssh, or a reverse shell caught by nc)."""

    def __init__(self, local: Shell, remote: Shell, intro: str = "", closing: str = "") -> None:
        self.local, self.remote, self.intro, self.closing = local, remote, intro, closing

    @property
    def prompt(self) -> str:  # type: ignore[override]
        return self.remote.prompt()

    def banner(self) -> Result:
        return Result(out=self.intro.encode())

    def interrupt(self) -> None:
        self.remote.abort_input()
        if self.remote.session is not None and self.remote.session.done:
            self.remote.session = None

    @property
    def secret(self) -> bool:  # type: ignore[override]
        return self.remote.secret_input()

    def eof_line(self) -> str:
        return self.remote.eof_line()

    def handle(self, line: str) -> Result:
        out = self.remote.run_line(line)
        data = b"".join(d for d, e in out.chunks if not e)
        err = b"".join(d for d, e in out.chunks if e)
        if ("quit",) in out.actions and self.remote.session is None:
            self.done = True
            data += self.closing.encode()
        return Result(out=data, err=err, status=self.remote.status)


def _open_channel_session(sh: Shell, pending) -> Session:
    net, world = sh.world.net, sh.world
    if isinstance(pending, ShellChannel):
        host = net.host_by_name(pending.host)
        remote = world.remote(host, pending.user)
        event(sh, f"net:revshell:{host.name}")
        return RemoteShellSession(sh, remote)
    return ChannelSession(pending)


def attach_listener(sh: Shell, job) -> Result:
    """`fg` on a listener job: pick up the connection that arrived, if any."""
    listener: Listener = job.listener
    if listener.pending is None:
        return fail(f"nc: still listening on port {listener.port}; nothing has connected yet (job {job.num} stays in the background)")
    pending, peer = listener.pending, listener.peer
    stop_listener(sh, job)
    sess = _open_channel_session(sh, pending)
    res = Result(session=sess, out=f"Connection received on {peer}\n".encode())
    return res


def stop_listener(sh: Shell, job) -> None:
    net = sh.world.net
    listener = job.listener
    if listener is not None:
        net.listeners.pop((sh.world.vantage, listener.port), None)
    job.state = "Done"
    sh.jobs[:] = [j for j in sh.jobs if j is not job]


# ----------------------------------------------------------------------- nc
def _ports(spec: str) -> list[int] | None:
    m = re.fullmatch(r"(\d+)(?:-(\d+))?", spec)
    if not m:
        return None
    lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
    return list(range(lo, hi + 1)) if 0 < lo <= hi < 65536 else None


def _state(net, src: str, host, port: int, udp: bool) -> str:
    if not udp:
        return net.port_state(src, host, port)
    if port in host.udp:
        return "open"
    return "filtered" if host.stealth else "closed"


@command("nc", "ncat", "netcat")
def nc(sh, args, stdin):
    flags, pos = parse_flags(args, "pwsi")
    verbose = bool(flags.get("v"))
    udp = bool(flags.get("u"))
    if flags.get("l"):
        if udp:
            return fail("nc: UDP listeners are not simulated (use TCP: nc -l -p PORT &)", 1)
        return _listen(sh, flags, pos, verbose)
    if len(pos) < 2:
        return fail("usage: nc [-46bCDdFhklNnrStUuvZz] [-I length] [-i interval] [-M ttl]\n\t  [-m minttl] [-O length] [-P proxy_username] [-p source_port] [-q seconds]\n\t  [-s sourceaddr] [-T keyword] [-V rtable] [-W recvlimit] [-w timeout]\n\t  [-X proxy_protocol] [-x proxy_address[:port]] [destination] [port]", 1)
    target, port_spec = pos[0], pos[1]
    ports = _ports(port_spec)
    if ports is None:
        return fail(f"nc: port number invalid: {port_spec}", 1)
    net, src = sh.world.net, sh.world.vantage
    ip = resolve(sh, target) if not flags.get("n") or re.fullmatch(r"[\d.]+", target) else (target if re.fullmatch(r"[\d.]+", target) else None)
    if ip is None:
        return fail(f"nc: getaddrinfo for host \"{target}\" port {ports[0]}: Name or service not known", 1)
    host = net.host_by_ip(ip)
    reachable = host is not None and net.route(src, ip) is not None
    event(sh, "net:nc-connect")
    proto = "udp" if udp else "tcp"
    errs, opened = [], []
    for port in ports:
        state = _state(net, src, host, port, udp) if reachable else "unreach"
        table = host.udp if (udp and host) else (host.services if host else {})
        if flags.get("z"):
            if state == "open":
                if verbose:
                    errs.append(f"Connection to {target} {port} port [{proto}/{table[port].name}] succeeded!")
                opened.append(port)
            elif verbose:
                errs.append(f"nc: connect to {target} port {port} ({proto}) failed: " + {"closed": "Connection refused", "filtered": "Connection timed out", "unreach": "No route to host"}[state])
            continue
        if state != "open" and not (udp and state == "filtered"):
            msg = {"closed": "Connection refused", "filtered": "Operation timed out", "unreach": "No route to host"}[state]
            return fail(f"nc: connect to {target} port {port} ({proto}) failed: {msg}", 1)
        if udp:
            if port not in host.udp:
                return Result(session=ChannelSession(UdpChannel(net, host, Service_stub(port), src)))  # open|filtered: nothing ever answers
            chan = UdpChannel(net, host, host.udp[port], src)
        else:
            chan = Channel(net, host, host.services[port], src)
        if verbose:
            errs.append(f"Connection to {target} {port} port [{proto}/{table[port].name}] succeeded!")
        err = "".join(e + "\n" for e in errs).encode()
        if stdin or not sh.tty:  # piped: send it all, print what comes back
            data = chan.greeting() + chan.feed(stdin) if stdin else chan.greeting()
            return Result(out=data, err=err)
        return Result(session=ChannelSession(chan), err=err)
    if flags.get("z"):
        return Result(out=b"", err="".join(e + "\n" for e in errs).encode(), status=0 if opened else 1)
    return fail("nc: no connection", 1)


def Service_stub(port: int):
    from ..engine.network import Service

    return Service(port, "unknown", "banner")


def _listen(sh, flags, pos, verbose: bool) -> Result:
    port_s = str(flags.get("p", "")) or (pos[-1] if pos else "")
    if not port_s.isdigit() or not 0 < int(port_s) < 65536:
        return fail("nc: missing or invalid port\nusage: nc -l [-p port]", 1)
    port = int(port_s)
    net, world = sh.world.net, sh.world
    if port < 1024 and world.user != "root":
        return fail("nc: Permission denied", 1)
    if (world.vantage, port) in net.listeners:
        return fail("nc: Address already in use", 1)
    event(sh, "net:nc-listen")
    msg = f"Listening on 0.0.0.0 {port}\n".encode() if verbose else b""
    job = sh.bg_job
    if job is not None:  # `nc -l ... &`: stay alive in the background
        listener = Listener(port, world.vantage, job)
        net.listeners[(world.vantage, port)] = listener
        job.listener = listener
        return Result(err=msg)
    return Result(err=msg + f"nc: nothing is connecting to port {port} yet. Run it in the background (`nc -l -p {port} &`), "
                  "start the thing that connects, then bring it forward with `fg`.\n".encode(), status=1)


# --------------------------------------------------------------------- ssh
class SshLogin(Session):
    """The handshake: host key question, then password attempts, then a shell (or one command)."""

    def __init__(self, sh: Shell, host, name: str, ip: str, port: int, user: str, key_text: str | None, command_line: str | None, strict: bool,
                 transfer=None) -> None:
        self.sh, self.host, self.name, self.ip, self.port, self.user = sh, host, name, ip, port, user
        self.key_text, self.command, self.strict = key_text, command_line, strict
        self.transfer = transfer  # scp: a function run on the remote shell instead of opening one
        self.tries = 0
        self.state = "hostkey" if self._unknown() and strict else "password"
        self.prompt = ""
        self.text = ""

    @property
    def secret(self) -> bool:  # type: ignore[override]
        return self.state == "password"

    def interrupt(self) -> None:
        self.done = True

    def _known_hosts(self) -> str:
        try:
            return self.sh.vfs.read(f"{self.sh.env.get('HOME', '/')}/.ssh/known_hosts").decode(errors="replace")
        except VFSError:
            return ""

    def _unknown(self) -> bool:
        return self.name not in self._known_hosts() and self.ip not in self._known_hosts()

    def _remember(self) -> str:
        path = f"{self.sh.env.get('HOME', '/')}/.ssh/known_hosts"
        line = f"{self.name},{self.ip} ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA{self.host.hostkey[7:30]}\n"
        try:
            self.sh.vfs.write(path, line.encode(), "/", append=True)
        except VFSError:
            self.sh.vfs.install_dir(path.rsplit("/", 1)[0], owner=self.sh.vfs.user)
            self.sh.vfs.install(path, line, owner=self.sh.vfs.user, mode=0o644)
        return f"Warning: Permanently added '{self.name}' (ED25519) to the list of known hosts.\n"

    def banner(self) -> Result:
        if self.state == "hostkey":
            self.prompt = "Are you sure you want to continue connecting (yes/no/[fingerprint])? "
            return ok(f"The authenticity of host '{self.name} ({self.ip})' can't be established.\n"
                      f"ED25519 key fingerprint is {self.host.hostkey}.\nThis key is not known by any other names\n")
        return self._begin_auth()

    def _begin_auth(self) -> Result:
        acct = self.host.accounts.get(self.user)
        if self.key_text is not None and acct is not None and acct.key and acct.key in self.key_text:
            return self._success()
        self.state = "password"
        self.prompt = f"{self.user}@{self.name}'s password: "
        return Result()

    def _success(self) -> Result:
        event(self.sh, f"net:ssh:{self.host.name}")
        remote = self.sh.world.remote(self.host, self.user)
        remote_sh: Shell = remote
        if self.transfer is not None:
            self.done = True
            return self.transfer(remote_sh)
        if self.command is not None:
            out = remote_sh.run_line(self.command)
            self.done = True
            return Result(out=b"".join(d for d, e in out.chunks if not e), err=b"".join(d for d, e in out.chunks if e), status=remote_sh.status)
        intro = (self.host.motd or "") + f"\nLast login: Sat Nov 21 22:41:07 2026 from {self.sh.world.vantage}\n"
        return Result(session=RemoteShellSession(self.sh, remote_sh, intro, f"logout\nConnection to {self.name} closed.\n"))

    def handle(self, line: str) -> Result:
        if self.state == "hostkey":
            ans = line.strip().lower()
            if ans in ("yes", self.host.hostkey.lower()):
                note = self._remember()
                res = self._begin_auth()
                res.err = note.encode() + res.err
                return res
            self.done = True
            return Result(err=b"Host key verification failed.\n", status=255)
        acct = self.host.accounts.get(self.user)
        if acct is not None and acct.password and line == acct.password:
            return self._success()
        self.tries += 1
        if self.tries >= 3:
            self.done = True
            return Result(err=f"{self.user}@{self.name}: Permission denied (publickey,password).\n".encode(), status=255)
        return Result(err=b"Permission denied, please try again.\n")


def _connect(sh: Shell, dest: str, port: int, tool: str = "ssh"):
    """Resolve and check a destination. Returns (host, ip, None) or (None, None, error_text)."""
    net, src = sh.world.net, sh.world.vantage
    ip = resolve(sh, dest)
    if ip is None:
        return None, None, f"{tool}: Could not resolve hostname {dest}: Name or service not known"
    host = net.host_by_ip(ip)
    if net.route(src, ip) is None:
        return None, None, f"{tool}: connect to host {dest} port {port}: Network is unreachable"
    if host is None:
        return None, None, f"{tool}: connect to host {dest} port {port}: No route to host"
    state = net.port_state(src, host, port)
    if state == "closed":
        return None, None, f"{tool}: connect to host {dest} port {port}: Connection refused"
    if state == "filtered":
        return None, None, f"{tool}: connect to host {dest} port {port}: Connection timed out"
    if host.services[port].kind != "ssh":
        return None, None, f"kex_exchange_identification: read: Connection reset by peer\nConnection reset by {ip} port {port}"
    return host, ip, None


def _read_identity(sh: Shell, ident: str | None) -> str | None:
    if not ident:
        return None
    try:
        return sh.vfs.read(ident, sh.cwd).decode(errors="replace")
    except VFSError:
        sh._err(f"Warning: Identity file {ident} not accessible: No such file or directory.\n")
        return None


@command("ssh")
def ssh(sh, args, stdin):
    port, user, ident, strict, dest, remote_cmd = 22, None, None, True, None, []
    i = 0
    while i < len(args):
        a = args[i]
        if dest is not None and remote_cmd:
            remote_cmd.append(a)  # once the remote command starts, everything is part of it
        elif dest is not None and not a.startswith("-"):
            remote_cmd.append(a)
        elif a == "-p" and i + 1 < len(args):
            port = int(args[i + 1]) if args[i + 1].isdigit() else 22
            i += 1
        elif a == "-l" and i + 1 < len(args):
            user = args[i + 1]
            i += 1
        elif a == "-i" and i + 1 < len(args):
            ident = args[i + 1]
            i += 1
        elif a == "-o" and i + 1 < len(args):
            if re.match(r"StrictHostKeyChecking[= ]+no", args[i + 1], re.I):
                strict = False
            i += 1
        elif a.startswith("-") and len(a) > 1:
            pass  # -v, -q, -T, -t, -N ...: accepted and ignored
        else:
            dest = a
        i += 1
    if dest is None:
        return fail("usage: ssh [-46AaCfGgKkMNnqsTtVvXxYy] [-B bind_interface]\n           [-b bind_address] [-c cipher_spec] [-D [bind_address:]port]\n           [-E log_file] [-e escape_char] [-F configfile] [-I pkcs11]\n           [-i identity_file] [-J [user@]host[:port]] [-L address]\n           [-l login_name] [-m mac_spec] [-O ctl_cmd] [-o option] [-p port]\n           [-Q query_option] [-R address] [-S ctl_path] [-W host:port]\n           [-w local_tun[:remote_tun]] destination [command [argument ...]]", 255)
    if "@" in dest:
        user, _, dest = dest.partition("@")
    user = user or sh.world.user
    host, ip, err = _connect(sh, dest, port)
    if err:
        return fail(err, 255)
    login = SshLogin(sh, host, dest, ip, port, user, _read_identity(sh, ident), " ".join(remote_cmd) or None, strict)
    return Result(session=login)


# --------------------------------------------------------------------- scp
REMOTE_SPEC = re.compile(r"^(?:([^@/:]+)@)?([^:/@]+):(.*)$")


def _progress(name: str, size: int) -> str:
    rate = max(size / 1024.0, 0.1) * 11.3
    return f"{name:<24}100% {size:>5}   {rate:>6.1f}KB/s   00:00\n"


@command("scp")
def scp(sh, args, stdin):
    port, ident, strict, pos = 22, None, True, []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-P" and i + 1 < len(args):
            port = int(args[i + 1]) if args[i + 1].isdigit() else 22
            i += 1
        elif a == "-i" and i + 1 < len(args):
            ident = args[i + 1]
            i += 1
        elif a == "-o" and i + 1 < len(args):
            if re.match(r"StrictHostKeyChecking[= ]+no", args[i + 1], re.I):
                strict = False
            i += 1
        elif a == "-r":
            return fail("scp: -r is not simulated (copy single files)", 1)
        elif a.startswith("-") and len(a) > 1:
            pass
        else:
            pos.append(a)
        i += 1
    if len(pos) != 2:
        return fail("usage: scp [-346BCpqrTv] [-c cipher] [-F ssh_config] [-i identity_file]\n            [-J destination] [-l limit] [-o ssh_option] [-P port] [-S program]\n            source ... target", 1)
    src_m, dst_m = REMOTE_SPEC.match(pos[0]), REMOTE_SPEC.match(pos[1])
    if (src_m is None) == (dst_m is None):
        return fail("scp: exactly one of source and target must be remote (user@host:path)", 1)
    download = src_m is not None
    m = src_m or dst_m
    user, dest, rpath = m.group(1) or sh.world.user, m.group(2), m.group(3)
    local_arg = pos[1] if download else pos[0]
    host, ip, err = _connect(sh, dest, port, "scp")
    if err:
        return fail(err.replace("scp: connect to host", "ssh: connect to host"), 255)

    def transfer(remote: Shell) -> Result:
        event(sh, "net:scp")
        try:
            if download:
                data = remote.vfs.read(rpath or ".", remote.cwd)
                name = (rpath or ".").rstrip("/").rsplit("/", 1)[-1]
                target = local_arg
                try:
                    if sh.vfs.get(target, sh.cwd).is_dir:
                        target = target.rstrip("/") + "/" + name
                except VFSError:
                    pass
                sh.vfs.write(target, data, sh.cwd)
            else:
                data = sh.vfs.read(local_arg, sh.cwd)
                name = local_arg.rsplit("/", 1)[-1]
                target = rpath or name
                try:
                    if remote.vfs.get(target, remote.cwd).is_dir:
                        target = target.rstrip("/") + "/" + name
                except VFSError:
                    pass
                remote.vfs.write(target, data, remote.cwd)
        except VFSError as e:
            return Result(err=f"scp: {e}\n".encode(), status=1)
        return Result(out=_progress(name, len(data)).encode())

    login = SshLogin(sh, host, dest, ip, port, user, _read_identity(sh, ident), None, strict, transfer=transfer)
    return Result(session=login)
