"""The simulated network the shell's networking tools talk to. Nothing here touches a real socket.

A :class:`Network` holds hosts (with services, DNS records, accounts and files), decides who can
reach whom, and tracks listeners that the player opened (``nc -l``). Every shell has a *vantage*:
the IP of the machine it is running on, so the same tools work after an ``ssh`` hop.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from dataclasses import dataclass, field
from typing import Callable

WELL_KNOWN = {
    21: "ftp", 22: "ssh", 23: "telnet", 25: "smtp", 53: "domain", 80: "http", 110: "pop3", 111: "rpcbind", 139: "netbios-ssn",
    143: "imap", 443: "https", 445: "microsoft-ds", 993: "imaps", 995: "pop3s", 1433: "ms-sql-s", 3306: "mysql",
    3389: "ms-wbt-server", 5432: "postgresql", 5900: "vnc", 6379: "redis", 8000: "http-alt", 8080: "http-proxy", 8443: "https-alt",
}
# nmap's default scan covers the 1000 most common ports; in this simulation: 1-985 plus these 15
TOP_EXTRA = {993, 995, 1025, 1433, 2049, 3306, 3389, 5000, 5432, 5900, 6379, 8000, 8008, 8080, 8443}
TOP_PORTS = sorted(set(range(1, 986)) | TOP_EXTRA)
FAST_PORTS = sorted(set(range(1, 86)) | {110, 111, 139, 143, 443, 445, 993, 995, 3306, 3389, 5432, 5900, 8000, 8080, 8443})  # -F: about 100


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]  # lower-cased names
    body: bytes
    cookies: dict[str, str]
    client_ip: str
    host: str = ""
    net: "Network | None" = None  # lets an app reach the network (e.g. to deliver a connect-back)
    server_host: "Host | None" = None  # the Host actually serving this request


@dataclass
class Response:
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | str = b""
    cookies: dict[str, str] = field(default_factory=dict)  # Set-Cookie name -> value

    def raw_body(self) -> bytes:
        return self.body.encode() if isinstance(self.body, str) else self.body


REASONS = {200: "OK", 301: "Moved Permanently", 302: "Found", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
           404: "Not Found", 405: "Method Not Allowed", 500: "Internal Server Error"}


@dataclass
class Service:
    port: int
    name: str  # nmap's service name: ssh, http, ftp, domain, ...
    kind: str = "banner"  # banner | http | ssh | dns
    product: str = ""  # shown by -sV
    extra: str = ""
    banner: str = ""  # sent when a client connects
    app: Callable | None = None  # http: (Request) -> Response; banner: (line) -> str | None
    acl: set[str] | None = None  # only these source IPs/subnets may connect; others see the port filtered
    server: str = ""  # HTTP Server header
    tls: bool = False  # speaks TLS (https); plain HTTP to it gets the classic 400
    cert: dict | None = None  # {"cn", "san": [...], "not_before", "not_after"}; self-signed


@dataclass
class Account:
    password: str = ""
    key: str | None = None  # contents a private key file must match to log in with -i
    uid: int = 1000


@dataclass
class Host:
    name: str
    ips: list[str]
    services: dict[int, Service] = field(default_factory=dict)
    udp: dict[int, Service] = field(default_factory=dict)  # UDP services; app(line, channel) -> reply
    aliases: list[str] = field(default_factory=list)
    os: str = "Linux"
    ttl: int = 64
    icmp: bool = True  # answers ping
    stealth: bool = False  # drops everything it does not explicitly serve (ports look filtered)
    accounts: dict[str, Account] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)  # absolute path -> content
    motd: str = ""
    hostkey: str = ""
    level_id: str = ""  # a remote_only level installed here, setuid-root, when a shell reaches it

    @property
    def fqdn(self) -> str:
        return self.name


@dataclass
class ShellChannel:
    """A connect-back that ends in a shell on ``host`` as ``user`` (what a reverse shell delivers)."""

    host: str
    user: str


@dataclass
class Listener:
    port: int
    owner_ip: str
    job: object
    pending: object | None = None  # a Channel that connected back and has not been picked up yet
    peer: str = ""


class Network:
    def __init__(self) -> None:
        self.player_ip = "10.10.0.2"
        self.gateway = "10.10.0.1"
        self.subnets = ["10.10.0.0/24", "10.20.0.0/24"]
        self.hosts: list[Host] = []
        self.secrets: dict[str, str] = {}  # the lab's discovered passwords/flags; set by lab.build()
        self.zones: dict[str, dict[str, dict[str, list[str]]]] = {}  # zone -> name -> type -> values
        self.axfr_servers: set[str] = set()
        self.listeners: dict[tuple[str, int], Listener] = {}
        self.notices: dict[str, list[str]] = {}
        self.remote_worlds: dict[tuple[str, str], object] = {}  # (host name, user) -> Shell
        self.remote_pristine: dict[tuple[str, str], dict] = {}  # the same machines as first built, to diff against when saving
        self.world_factory: Callable | None = None

    # --------------------------------------------------------------- lookups
    def add(self, host: Host) -> Host:
        self.hosts.append(host)
        return host

    def host_by_ip(self, ip: str) -> Host | None:
        return next((h for h in self.hosts if ip in h.ips), None)

    def host_by_name(self, name: str) -> Host | None:
        n = name.lower().rstrip(".")
        return next((h for h in self.hosts if n == h.name or n in h.aliases), None)

    @staticmethod
    def subnet_of(ip: str) -> str:
        return str(ipaddress.ip_network(f"{ip}/24", strict=False))

    def same_subnet(self, a: str, b: str) -> bool:
        return self.subnet_of(a) == self.subnet_of(b)

    def direct(self, src: str, dst: str) -> bool:
        """On a shared subnet (any interface of the source machine counts)."""
        if self.same_subnet(src, dst):
            return True
        host = self.host_by_ip(src)
        return bool(host and any(self.same_subnet(i, dst) for i in host.ips))

    def route(self, src: str, dst: str) -> list[str] | None:
        """Hop list (ending at dst) from src, or None when there is no route."""
        if dst == src or dst.startswith("127."):
            return [dst]
        if self.same_subnet(src, dst):
            return [dst]
        known = any(ipaddress.ip_address(dst) in ipaddress.ip_network(s) for s in self.subnets)
        if not known:
            return None  # outside the lab: no uplink
        src_host = self.host_by_ip(src)
        if src_host and any(self.same_subnet(i, dst) for i in src_host.ips):
            return [dst]  # a multi-homed host
        return None

    @staticmethod
    def latency(ip: str, i: int = 0, scale: float = 1.0) -> float:
        h = int(hashlib.md5(f"{ip}/{i}".encode()).hexdigest()[:6], 16)
        return round((0.18 + (h % 700) / 1000.0) * scale, 3)

    # ------------------------------------------------------------------- DNS
    def add_zone(self, zone: str, records: dict[str, dict[str, list[str]]]) -> None:
        self.zones[zone] = {k.lower(): v for k, v in records.items()}

    def zone_for(self, name: str) -> str | None:
        n = name.lower().rstrip(".")
        return next((z for z in self.zones if n == z or n.endswith("." + z)), None)

    def dns_lookup(self, name: str, rtype: str = "A") -> list[str] | None:
        """Values for name/type; None = NXDOMAIN, [] = exists but no such type."""
        n = name.lower().rstrip(".")
        zone = self.zone_for(n)
        if zone is None:
            return None
        rec = self.zones[zone].get(n)
        if rec is None:
            return None
        if rtype == "ANY":
            return [f"{t}\t{v}" for t, vs in rec.items() for v in vs]
        vals = rec.get(rtype, [])
        if not vals and rtype in ("A", "AAAA") and "CNAME" in rec:
            return rec["CNAME"] + (self.dns_lookup(rec["CNAME"][0], rtype) or [])
        return vals

    def reverse_lookup(self, ip: str) -> str | None:
        for zone in self.zones.values():
            for name, rec in zone.items():
                if ip in rec.get("A", []):
                    return name
        return None

    def resolve(self, name: str, hosts_file: str = "") -> str | None:
        n = name.lower().rstrip(".")
        try:
            ipaddress.ip_address(n)
            return n
        except ValueError:
            pass
        for line in hosts_file.splitlines():
            parts = line.split("#")[0].split()
            if len(parts) >= 2 and n in [p.lower() for p in parts[1:]]:
                return parts[0]
        if n == "localhost":
            return "127.0.0.1"
        for cand in (n, f"{n}.brightside.lab"):
            vals = self.dns_lookup(cand, "A")
            if vals:
                return vals[-1] if not re.match(r"\d", vals[0]) else vals[0]
        return None

    # ------------------------------------------------------------- reachability
    def port_state(self, src: str, host: Host, port: int) -> str:
        """open | closed | filtered, as seen from src."""
        svc = host.services.get(port)
        if svc is None:
            return "filtered" if host.stealth else "closed"
        if svc.acl is not None:
            src_host = self.host_by_ip(src)
            origins = src_host.ips if src_host else [src]  # a multi-homed machine may use any of its interfaces
            allowed = any(o == a or (("/" in a) and ipaddress.ip_address(o) in ipaddress.ip_network(a)) for o in origins for a in svc.acl)
            if not allowed:
                return "filtered"
        return "open"

    def reachable_host(self, src: str, ip: str) -> Host | None:
        host = self.host_by_ip(ip)
        if host is None or self.route(src, ip) is None:
            return None
        return host

    # --------------------------------------------------------------- listeners
    def notice(self, ip: str, text: str) -> None:
        self.notices.setdefault(ip, []).append(text)

    def take_notices(self, ip: str) -> list[str]:
        return self.notices.pop(ip, [])

    def deliver(self, src_ip: str, dst_ip: str, dst_port: int, channel) -> bool:
        """A remote program connects to ``dst_ip:dst_port``. True if a listener there accepted."""
        listener = self.listeners.get((dst_ip, dst_port))
        if listener is None or listener.pending is not None or self.route(src_ip, dst_ip) is None:
            return False
        listener.pending = channel
        listener.peer = src_ip
        self.notice(dst_ip, f"[{getattr(listener.job, 'num', '?')}]+  nc: connection received on port {dst_port} from {src_ip} (type `fg %{getattr(listener.job, 'num', '?')}` to take it)")
        return True


# ------------------------------------------------------------------ channels
class Channel:
    """One TCP conversation with a simulated service, driven a chunk at a time."""

    def __init__(self, net: Network, host: Host, service: Service, src_ip: str) -> None:
        self.net, self.host, self.service, self.src_ip = net, host, service, src_ip
        self.buf = b""
        self.closed = False

    def greeting(self) -> bytes:
        s = self.service
        if s.kind == "ssh":
            return s.banner.encode() + b"\r\n"
        return s.banner.encode() if s.kind == "banner" else b""

    def feed(self, data: bytes) -> bytes:
        s = self.service
        if self.closed:
            return b""
        if s.kind == "ssh":
            self.closed = True
            return b"Invalid SSH identification string.\n"
        if s.kind == "http":
            self.buf += data
            if b"\r\n\r\n" not in self.buf and b"\n\n" not in self.buf:
                return b""
            raw, self.buf = self.buf, b""
            self.closed = True
            return http_exchange(self.net, self.host, s, raw, self.src_ip)
        out = b""
        for line in data.decode(errors="replace").splitlines():
            reply = s.app(line, self) if s.app else None
            if reply is not None:
                out += reply.encode()
            if getattr(self, "quit", False):
                self.closed = True
                break
        return out


PLAIN_TO_TLS = """<html>
<head><title>400 The plain HTTP request was sent to HTTPS port</title></head>
<body>
<center><h1>400 Bad Request</h1></center>
<center>The plain HTTP request was sent to HTTPS port</center>
<hr><center>nginx/1.18.0 (Ubuntu)</center>
</body>
</html>
"""


def plain_to_tls(service: Service) -> Response:
    return Response(400, {"Server": service.server or "nginx", "Content-Type": "text/html"}, PLAIN_TO_TLS)


class UdpChannel:
    """A connectionless 'conversation': every line is a datagram, replies come back if the service has any."""

    def __init__(self, net: Network, host: Host, service: Service, src_ip: str) -> None:
        self.net, self.host, self.service, self.src_ip = net, host, service, src_ip
        self.closed = False

    def greeting(self) -> bytes:
        return b""

    def feed(self, data: bytes) -> bytes:
        out = b""
        for line in data.decode(errors="replace").splitlines():
            reply = self.service.app(line, self) if self.service.app else None
            if reply:
                out += reply.encode()
        return out


def parse_request(raw: bytes) -> tuple[str, str, str, dict[str, str], bytes]:
    head, _, body = raw.replace(b"\r\n", b"\n").partition(b"\n\n")
    lines = head.decode(errors="replace").split("\n")
    parts = lines[0].split()
    method = parts[0].upper() if parts else ""
    target = parts[1] if len(parts) > 1 else "/"
    version = parts[2] if len(parts) > 2 else "HTTP/1.0"
    headers = {}
    for ln in lines[1:]:
        if ":" in ln:
            k, _, v = ln.partition(":")
            headers[k.strip().lower()] = v.strip()
    return method, target, version, headers, body


def parse_cookies(header: str) -> dict[str, str]:
    out = {}
    for part in header.split(";"):
        if "=" in part:
            k, _, v = part.strip().partition("=")
            out[k] = v
    return out


def serve(service: Service, req: Request) -> Response:
    """Run the app and add the headers every response carries."""
    try:
        resp = service.app(req) if service.app else Response(404, body="not found")
    except Exception:  # noqa: BLE001 - a broken simulated app is a 500, not a crash
        resp = Response(500, body="Internal Server Error")
    resp.headers.setdefault("Server", service.server or "nginx")
    resp.headers.setdefault("Date", __import__("time").strftime("%a, %d %b %Y %H:%M:%S GMT", __import__("time").gmtime()))
    resp.headers.setdefault("Content-Type", "text/html")
    resp.headers["Content-Length"] = str(len(resp.raw_body()))
    resp.headers.setdefault("Connection", "close")
    return resp


def format_response(resp: Response, version: str = "HTTP/1.1") -> bytes:
    lines = [f"{version} {resp.status} {REASONS.get(resp.status, 'Unknown')}"]
    lines += [f"{k}: {v}" for k, v in resp.headers.items()]
    lines += [f"Set-Cookie: {k}={v}; Path=/; HttpOnly" for k, v in resp.cookies.items()]
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + resp.raw_body()


def http_exchange(net: Network, host: Host, service: Service, raw: bytes, src_ip: str) -> bytes:
    method, target, version, headers, body = parse_request(raw)
    if service.tls:  # a plaintext request to a TLS port never reaches the app
        return format_response(serve(Service(service.port, "https", "http", app=lambda r: plain_to_tls(service), server=service.server), Request("GET", "/", {}, headers, b"", {}, src_ip)), version)
    path, _, qs = target.partition("?")
    query = dict(p.partition("=")[::2] for p in qs.split("&") if p)
    req = Request(method, path, query, headers, body, parse_cookies(headers.get("cookie", "")), src_ip, headers.get("host", ""), net=net, server_host=host)
    if method not in ("GET", "HEAD", "POST", "PUT", "DELETE", "OPTIONS"):
        return format_response(serve(service, Request("GET", "/__bad__", {}, headers, b"", {}, src_ip)), version)
    resp = serve(service, req)
    if method == "HEAD":
        resp.body = b""
        out = format_response(resp, version)
        return out
    return format_response(resp, version)
