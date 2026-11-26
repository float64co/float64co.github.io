"""Simulated networking tools: nmap, ping, traceroute, dig, host, curl. All talk to engine.network."""

from __future__ import annotations

import base64
import ipaddress
import re
import time

from ..engine.network import FAST_PORTS, REASONS, TOP_PORTS, WELL_KNOWN, Host, Network, Request, plain_to_tls, serve
from ..engine.vfs import VFSError
from .commands import _errres, command, parse_flags
from .shell import Result, fail, ok


# ------------------------------------------------------------------ helpers
def hosts_file(sh) -> str:
    try:
        return sh.vfs.read("/etc/hosts").decode(errors="replace")
    except VFSError:
        return ""


def resolve(sh, name: str) -> str | None:
    return sh.world.net.resolve(name, hosts_file(sh))


def clock() -> tuple[str, str]:
    t = time.gmtime()
    return time.strftime("%Y-%m-%d %H:%M", t), time.strftime("%a %b %d %H:%M:%S UTC %Y", t)


def event(sh, name: str) -> None:
    sh.world.events.add(name)


def service_name(port: int, host: Host | None = None) -> str:
    if host and port in host.services:
        return host.services[port].name
    return WELL_KNOWN.get(port, "unknown")


# --------------------------------------------------------------------- nmap
def _expand_targets(spec: str) -> list[str] | None:
    """A.B.C.D, A.B.C.D/N (N>=22), A.B.C.x-y, A.B.C.*, or a name (returned as-is)."""
    if "/" in spec:
        try:
            net = ipaddress.ip_network(spec, strict=False)
        except ValueError:
            return None
        if net.num_addresses > 1024:
            return None
        return [str(a) for a in net]  # nmap scans every address in the block, including .0 and .255
    m = re.fullmatch(r"(\d+\.\d+\.\d+)\.(\d+)-(\d+)", spec)
    if m:
        return [f"{m.group(1)}.{i}" for i in range(int(m.group(2)), min(int(m.group(3)), 255) + 1)]
    if spec.endswith(".*"):
        return [f"{spec[:-2]}.{i}" for i in range(1, 255)]
    return [spec]


def _parse_ports(spec: str) -> list[int] | None:
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if part == "-":
            return list(range(1, 65536))
        m = re.fullmatch(r"(\d*)-(\d*)", part)
        if m:
            lo, hi = int(m.group(1) or 1), int(m.group(2) or 65535)
            if not 1 <= lo <= hi <= 65535:
                return None
            out.update(range(lo, hi + 1))
        elif part.isdigit() and 0 < int(part) < 65536:
            out.add(int(part))
        else:
            return None
    return sorted(out)


def _http_get(net: Network, host: Host, port: int, path: str, src: str):
    svc = host.services.get(port)
    if svc is None or svc.kind != "http":
        return None
    return serve(svc, Request("GET", path, {}, {"host": host.name}, b"", {}, src, host.name, net=net, server_host=host))


def _nmap_scripts(net: Network, host: Host, port: int, src: str) -> list[str]:
    resp = _http_get(net, host, port, "/", src)
    if resp is None:
        return []
    lines = []
    cert = host.services[port].cert
    if cert:
        lines += [f"| ssl-cert: Subject: commonName={cert['cn']}",
                  "| Subject Alternative Name: " + ", ".join(f"DNS:{n}" for n in cert["san"]),
                  f"| Not valid before: {cert['not_before']}", f"|_Not valid after:  {cert['not_after']}"]
    m = re.search(r"<title>(.*?)</title>", resp.raw_body().decode(errors="replace"), re.S | re.I)
    if m:
        lines.append(f"|_http-title: {m.group(1).strip()}")
    robots = _http_get(net, host, port, "/robots.txt", src)
    if robots is not None and robots.status == 200:
        paths = [ln.split(":", 1)[1].strip() for ln in robots.raw_body().decode().splitlines() if ln.lower().startswith("disallow:")]
        if paths:
            lines += [f"| http-robots.txt: {len(paths)} disallowed entr{'y' if len(paths) == 1 else 'ies'} ", "|_" + " ".join(paths)]
    return lines


@command("nmap")
def nmap(sh, args, stdin):
    net, src = sh.world.net, sh.world.vantage
    ports_spec, targets, opts = None, [], set()
    top_n = None
    outfile = None
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-p", "--ports") and i + 1 < len(args):
            ports_spec = args[i + 1]
            i += 1
        elif a.startswith("-p") and len(a) > 2 and not a.startswith("-P"):
            ports_spec = a[2:]
        elif a == "--top-ports" and i + 1 < len(args):
            top_n = int(args[i + 1]) if args[i + 1].isdigit() else None
            i += 1
        elif a == "-oN" and i + 1 < len(args):
            outfile = args[i + 1]
            i += 1
        elif a in ("-T0", "-T1", "-T2", "-T3", "-T4", "-T5", "-v", "-vv", "-n", "-R", "--open", "-Pn", "-PN", "-sn", "-sP", "-sV", "-sT",
                   "-A", "-F", "-sS", "-sU", "-O", "-sC", "--reason"):
            opts.add(a)
        elif a.startswith("-"):
            return fail(f"nmap: unrecognized option '{a}'\nSee the output of nmap -h for a summary of options.", 255)
        else:
            targets.append(a)
        i += 1
    if opts & {"-sS", "-sU", "-O"}:
        what = "TCP/IP fingerprinting (for OS scan)" if "-O" in opts else "You requested a scan type which"
        return fail(f"{what} requires root privileges.\nQUITTING!", 1)
    if not targets:
        return fail("Nmap 7.91 ( https://nmap.org )\nUsage: nmap [Scan Type(s)] [Options] {target specification}\nTARGET SPECIFICATION:\n  Can pass hostnames, IP addresses, networks, etc.\n  Ex: scanme.nmap.org, microsoft.com/24, 192.168.0.1; 10.0.0-255.1-254", 1)
    ping_only = bool(opts & {"-sn", "-sP"})
    version = bool(opts & {"-sV", "-A"})
    skip_ping = bool(opts & {"-Pn", "-PN"})
    if ports_spec is not None:
        ports = _parse_ports(ports_spec)
        if ports is None:
            return fail(f"Your port specifications are illegal.  Example of proper form: \"-100,200-1024,T:3000-4000,U:60000-\"\nQUITTING!", 1)
    elif "-F" in opts:
        ports = FAST_PORTS
    elif top_n:
        ports = TOP_PORTS[:top_n]
    else:
        ports = TOP_PORTS
    day, _ = clock()
    out = [f"Starting Nmap 7.91 ( https://nmap.org ) at {day} UTC\n"]
    errs: list[str] = []
    total = up = 0
    for spec in targets:
        ips = _expand_targets(spec)
        if ips is None:
            errs.append(f"Failed to resolve \"{spec}\".")
            continue
        for ip in ips:
            name = None
            if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", ip):
                resolved = resolve(sh, ip)
                if resolved is None:
                    errs.append(f"Failed to resolve \"{ip}\".")
                    continue
                name, ip = ip, resolved
            total += 1
            host = net.host_by_ip(ip)
            routed = host is not None and net.route(src, ip) is not None
            discoverable = routed and (host.icmp or not host.stealth)  # a host that drops pings and everything else looks dead
            if not (discoverable or skip_ping):
                if len(ips) == 1 and not ping_only:
                    out.append("Note: Host seems down. If it is really up, but blocking our ping probes, try -Pn\n")
                continue
            up += 1
            shown = host.name if (host and "-n" not in opts) else None
            title = f"{name or shown} ({ip})" if (name or shown) else ip
            if skip_ping and not routed:
                out.append(f"Nmap scan report for {title}\nHost is up.\nAll {len(ports)} scanned ports on {title} are in ignored states.\n"
                           f"Not shown: {len(ports)} filtered tcp ports (no-response)\n\n")
                continue
            out.append(f"Nmap scan report for {title}\nHost is up ({net.latency(ip) / 1000:.5f}s latency).\n")
            event(sh, "net:nmap-scan")
            if ping_only:
                out.append("\n")
                continue
            if len(ports) > 1000:
                event(sh, "net:nmap-allports")
            states = {p: net.port_state(src, host, p) for p in ports}
            open_ports = [p for p in ports if states[p] == "open"]
            counts = {"closed": sum(1 for p in ports if states[p] == "closed"), "filtered": sum(1 for p in ports if states[p] == "filtered")}
            ignored = max(counts, key=counts.get) if any(counts.values()) else "closed"
            collapse = {st for st in ("closed", "filtered") if counts[st] > 25}  # nmap only collapses big groups
            shown_ports = [p for p in ports if states[p] == "open" or states[p] not in collapse]
            if "--open" in opts:
                shown_ports = open_ports
            reason = {"closed": "conn-refused", "filtered": "no-response"}
            if not shown_ports and not open_ports:
                out.append(f"All {len(ports)} scanned ports on {title} are in ignored states.\n")
            hidden = [f"{counts[s]} {s} tcp ports ({reason[s]})" for s in ("closed", "filtered") if s in collapse and counts[s]]
            if hidden:
                out.append("Not shown: " + ", ".join(hidden) + "\n")
            if shown_ports:
                rows = []
                for p in shown_ports:
                    st = states[p]
                    svc = service_name(p, host) if st == "open" else WELL_KNOWN.get(p, "unknown")
                    if version and st == "open" and p in host.services and host.services[p].tls:
                        svc = "ssl/http"
                    ver = ""
                    if version and st == "open" and p in host.services:
                        s = host.services[p]
                        ver = (s.product + (" " + s.extra if s.extra else "")).strip()
                    rows.append((f"{p}/tcp", st, svc, ver))
                    if ("-A" in opts or "-sC" in opts) and st == "open":
                        for ln in _nmap_scripts(net, host, p, src):
                            rows.append(("", "", "", ln))
                w = [max(len(r[i]) for r in rows + [("PORT", "STATE", "SERVICE", "")]) for i in range(3)]
                out.append((f"{'PORT':<{w[0]}} {'STATE':<{w[1]}} {'SERVICE':<{w[2]}}" + (" VERSION" if version else "")).rstrip() + "\n")
                for port, st, svc, ver in rows:
                    if port == "" and st == "":
                        out.append(ver + "\n")
                    else:
                        out.append((f"{port:<{w[0]}} {st:<{w[1]}} {svc:<{w[2]}}" + (f" {ver}" if ver else "")).rstrip() + "\n")
                if version and open_ports:
                    out.append("Service Info: OS: Linux; CPE: cpe:/o:linux:linux_kernel\n")
            if version:
                event(sh, "net:nmap-sv")
            out.append("\n")
    if version and up:
        out.append("Service detection performed. Please report any incorrect results at https://nmap.org/submit/ .\n")
    secs = 0.31 + total * 0.0042 + (len(ports) / 20000.0 if not ping_only else 0.0)
    out.append(f"Nmap done: {total} IP address{'es' if total != 1 else ''} ({up} host{'s' if up != 1 else ''} up) scanned in {secs:.2f} seconds\n")
    text = "".join(out)
    if outfile:
        try:
            sh.vfs.write(outfile, text.encode(), sh.cwd)
        except VFSError as e:
            errs.append(f"Failed to open output file: {e}")
    res = _errres([e for e in errs], text)
    return res


# ---------------------------------------------------------------- ip / ifconfig
def _iface_cidr(sh) -> str:
    net = sh.world.net
    bits = 24  # every subnet in this lab is a /24
    return f"{sh.world.vantage}/{bits}"


@command("ip")
def ip_cmd(sh, args, stdin):
    pos = [a for a in args if not a.startswith("-")]
    sub = pos[0] if pos else "addr"
    if sub not in ("addr", "a", "address"):
        return fail(f"Object \"{sub}\" is unknown, try \"ip help\".", 1)
    cidr = _iface_cidr(sh)
    return ok(
        "1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000\n"
        "    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00\n"
        "    inet 127.0.0.1/8 scope host lo\n"
        "       valid_lft forever preferred_lft forever\n"
        "2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP group default qlen 1000\n"
        "    link/ether 02:42:0a:0a:00:02 brd ff:ff:ff:ff:ff:ff\n"
        f"    inet {cidr} brd {cidr.rsplit('.', 1)[0]}.255 scope global eth0\n"
        "       valid_lft forever preferred_lft forever\n"
    )


@command("ifconfig")
def ifconfig(sh, args, stdin):
    cidr = _iface_cidr(sh)
    ip, bits = cidr.split("/")
    mask = {24: "255.255.255.0", 16: "255.255.0.0", 8: "255.0.0.0"}.get(int(bits), "255.255.255.0")
    return ok(
        f"eth0: flags=4163<UP,BROADCAST,RUNNING,MULTICAST>  mtu 1500\n"
        f"        inet {ip}  netmask {mask}  broadcast {ip.rsplit('.', 1)[0]}.255\n"
        "        ether 02:42:0a:0a:00:02  txqueuelen 0  (Ethernet)\n\n"
        "lo: flags=73<UP,LOOPBACK,RUNNING>  mtu 65536\n"
        "        inet 127.0.0.1  netmask 255.0.0.0\n"
        "        loop  txqueuelen 1000  (Local Loopback)\n"
    )


# --------------------------------------------------------------------- ping
@command("ping")
def ping(sh, args, stdin):
    flags, pos = parse_flags(args, "cWwiqs")
    if not pos:
        return fail("ping: usage error: Destination address required", 2)
    count = int(str(flags.get("c", 4))) if str(flags.get("c", "4")).isdigit() else 4
    target = pos[0]
    ip = resolve(sh, target)
    if ip is None:
        return fail(f"ping: {target}: Name or service not known", 2)
    net, src = sh.world.net, sh.world.vantage
    host = net.host_by_ip(ip)
    shown = target if not re.fullmatch(r"[\d.]+", target) else (host.name if host else ip)
    head = f"PING {target} ({ip}) 56(84) bytes of data.\n"
    event(sh, "net:ping")
    if net.route(src, ip) is None:
        return fail("ping: connect: Network is unreachable", 2)
    if host is None:
        lines = [f"From {src} icmp_seq={i} Destination Host Unreachable\n" for i in range(1, count + 1)]
        return ok(head + "".join(lines) + f"\n--- {target} ping statistics ---\n{count} packets transmitted, 0 received, +{count} errors, 100% packet loss, time {1000 * (count - 1)}ms\n\n", 1)
    if not host.icmp:
        stats = f"\n--- {target} ping statistics ---\n{count} packets transmitted, 0 received, 100% packet loss, time {1000 * (count - 1) + 3}ms\n\n"
        return ok(head + stats, 1)
    ttl = host.ttl if net.direct(src, ip) else host.ttl - 1
    times = [net.latency(ip, i, 1.0) for i in range(count)]
    lines = [f"64 bytes from {shown} ({ip}): icmp_seq={i + 1} ttl={ttl} time={t:.3f} ms\n" for i, t in enumerate(times)]
    mn, mx, avg = min(times), max(times), sum(times) / len(times)
    mdev = (sum((t - avg) ** 2 for t in times) / len(times)) ** 0.5
    return ok(head + "".join(lines) + f"\n--- {target} ping statistics ---\n{count} packets transmitted, {count} received, 0% packet loss, time {1000 * (count - 1) + 3}ms\n"
              f"rtt min/avg/max/mdev = {mn:.3f}/{avg:.3f}/{mx:.3f}/{mdev:.3f} ms\n")


# --------------------------------------------------------------- traceroute
@command("traceroute", "tracepath")
def traceroute(sh, args, stdin):
    flags, pos = parse_flags(args, "mwq")
    if not pos:
        return fail("Specify \"host\" missing argument.", 2)
    target = pos[0]
    ip = resolve(sh, target)
    if ip is None:
        return fail(f"{target}: Name or service not known\nCannot handle \"host\" cmdline arg `{target}' on position 1 (argc 1)", 2)
    net, src = sh.world.net, sh.world.vantage
    maxh = int(str(flags.get("m", 30))) if str(flags.get("m", "30")).isdigit() else 30
    event(sh, "net:traceroute")
    route = net.route(src, ip)
    if route is None:
        return fail("connect: Network is unreachable", 2)
    host = net.host_by_ip(ip)
    head = f"traceroute to {target} ({ip}), {maxh} hops max, 60 byte packets\n"
    rows = []

    def label(addr: str) -> str:
        h = net.host_by_ip(addr)
        return addr if flags.get("n") or h is None else f"{h.name} ({addr})"

    if net.direct(src, ip) or ip == src or ip.startswith("127."):
        hops = [ip]
    else:
        hops = [net.gateway, ip]
    for n, h in enumerate(hops[:maxh], 1):
        t = [net.latency(h, i, 0.8) for i in range(3)]
        if h == ip and (host is None or not host.icmp) and host is not None and host.stealth:
            rows.append(f"{n:>2}  * * *\n")
        else:
            rows.append(f"{n:>2}  {label(h)}  " + "  ".join(f"{x:.3f} ms" for x in t) + "\n")
    if host is not None and host.stealth and not host.icmp:
        rows += [f"{n:>2}  * * *\n" for n in range(len(hops) + 1, min(len(hops) + 3, maxh) + 1)]
    return ok(head + "".join(rows))


# ---------------------------------------------------------------------- dig
def _fmt_dns_value(rtype: str, value: str) -> str:
    return value


def _dns_server(sh, server: str | None):
    """(ip, error) for the DNS server a query goes to."""
    net = sh.world.net
    if server is None:
        try:
            for ln in sh.vfs.read("/etc/resolv.conf").decode().splitlines():
                if ln.startswith("nameserver"):
                    server = ln.split()[1]
        except VFSError:
            pass
        server = server or net.gateway
    ip = resolve(sh, server)
    return ip


def _dns_reachable(sh, ip: str | None) -> bool:
    net = sh.world.net
    if ip is None:
        return False
    host = net.host_by_ip(ip)
    return bool(host and 53 in host.services and net.route(sh.world.vantage, ip) is not None
                and net.port_state(sh.world.vantage, host, 53) == "open")


def _rr(name: str, rtype: str, value: str, ttl: int = 3600) -> str:
    n = name.rstrip(".") + "."
    return f"{n}\t{ttl}\tIN\t{rtype}\t{value}"


def _records(net: Network, name: str, rtype: str) -> tuple[str, list[str]]:
    """(status, answer lines) for a query."""
    n = name.lower().rstrip(".")
    zone = net.zone_for(n)
    rec = net.zones[zone].get(n) if zone else None
    if rec is None:
        return "NXDOMAIN", []
    if rtype == "ANY":
        return "NOERROR", [_rr(n, t, v) for t, vs in rec.items() for v in vs]
    if rtype in ("A", "AAAA") and "CNAME" in rec:
        target = rec["CNAME"][0]
        return "NOERROR", [_rr(n, "CNAME", target)] + [_rr(target, rtype, v) for v in (net.dns_lookup(target, rtype) or [])]
    return "NOERROR", [_rr(n, rtype, v) for v in rec.get(rtype, [])]


@command("dig")
def dig(sh, args, stdin):
    net = sh.world.net
    server, short, rtype, name, reverse = None, False, "A", None, None
    type_given = False
    plus = set()
    i = 0
    toks = list(args)
    while i < len(toks):
        a = toks[i]
        if a.startswith("@"):
            server = a[1:]
        elif a == "-x" and i + 1 < len(toks):
            reverse = toks[i + 1]
            i += 1
        elif a == "-t" and i + 1 < len(toks):
            rtype, type_given = toks[i + 1].upper(), True
            i += 1
        elif a.startswith("+"):
            plus.add(a)
        elif not type_given and a.upper() in ("A", "AAAA", "MX", "TXT", "NS", "CNAME", "SOA", "ANY", "PTR", "AXFR"):
            rtype, type_given = a.upper(), True  # like real dig: a bare query type may come before or after the name
        elif name is None:
            name = a
        i += 1
    short = "+short" in plus
    if reverse:
        try:
            parts = str(ipaddress.ip_address(reverse)).split(".")
        except ValueError:
            return fail(f"Invalid IP address {reverse}", 10)
        name = ".".join(reversed(parts)) + ".in-addr.arpa"
        rtype = "PTR"
    if name is None:
        name, rtype = ".", "NS"
    ip = _dns_server(sh, server)
    sip = ip or "?"
    when = clock()[1]
    if not _dns_reachable(sh, ip):
        if short:
            return fail(";; communications error", 9)
        return ok(f"\n; <<>> DiG 9.18.18-0ubuntu0.22.04.2-Ubuntu <<>> {' '.join(args)}\n;; global options: +cmd\n"
                  f";; communications error to {sip}#53: timed out\n;; no servers could be reached\n", 9)
    event(sh, "net:dig")
    hdr = f"\n; <<>> DiG 9.18.18-0ubuntu0.22.04.2-Ubuntu <<>> {' '.join(args)}\n;; global options: +cmd\n"
    if rtype == "AXFR":
        zone = name.lower().rstrip(".")
        if zone not in net.zones or sip not in net.axfr_servers:
            return ok(hdr + "; Transfer failed.\n", 9)
        event(sh, "net:dns-axfr")
        recs = net.zones[zone]
        lines = [_rr(zone, "SOA", recs[zone]["SOA"][0])]
        for n, rec in recs.items():
            for t, vs in rec.items():
                if n == zone and t == "SOA":
                    continue
                lines += [_rr(n, t, v) for v in vs]
        lines.append(_rr(zone, "SOA", recs[zone]["SOA"][0]))
        return ok(hdr + "\n".join(lines).replace("\n", "\n") + f"\n;; Query time: 2 msec\n;; SERVER: {sip}#53({sip}) (TCP)\n;; WHEN: {when}\n;; XFR size: {len(lines)} records (messages 1, bytes {len(lines) * 62})\n\n")
    if rtype == "PTR" and name.endswith(".in-addr.arpa"):
        octets = name[: -len(".in-addr.arpa")].split(".")
        target_ip = ".".join(reversed(octets))
        ans = net.reverse_lookup(target_ip)
        status = "NOERROR" if ans else "NXDOMAIN"
        lines = [_rr(name, "PTR", ans + ".")] if ans else []
    else:
        status, lines = _records(net, name, rtype)
        if rtype in ("A", "AAAA") and status == "NOERROR" and lines:
            pass
    if short:
        vals = [ln.split("\t", 4)[-1] for ln in lines]
        return ok("".join(v + "\n" for v in vals))
    qname = name.rstrip(".") + "."
    ans = "\n".join(lines)
    return ok(hdr + f";; Got answer:\n;; ->>HEADER<<- opcode: QUERY, status: {status}, id: {int(time.time()) % 60000 + 1000}\n"
              f";; flags: qr rd ra; QUERY: 1, ANSWER: {len(lines)}, AUTHORITY: 0, ADDITIONAL: 1\n\n"
              f";; OPT PSEUDOSECTION:\n; EDNS: version: 0, flags:; udp: 65494\n;; QUESTION SECTION:\n;{qname:<23} IN\t{rtype}\n\n"
              + (f";; ANSWER SECTION:\n{ans}\n\n" if lines else "")
              + f";; Query time: 1 msec\n;; SERVER: {sip}#53({sip}) (UDP)\n;; WHEN: {when}\n;; MSG SIZE  rcvd: {60 + 20 * len(lines)}\n\n")


@command("host", "nslookup")
def host_cmd(sh, args, stdin):
    flags, pos = parse_flags(args, "t")
    if not pos:
        return fail("Usage: host [-t type] name [server]", 1)
    net = sh.world.net
    name = pos[0]
    rtype = str(flags.get("t", "")).upper()
    server = pos[1] if len(pos) > 1 else None
    ip = _dns_server(sh, server)
    if not _dns_reachable(sh, ip):
        return fail(";; communications error to {0}#53: timed out\n;; no servers could be reached".format(ip or "?"), 1)
    event(sh, "net:dig")
    if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", name):
        ans = net.reverse_lookup(name)
        octets = ".".join(reversed(name.split(".")))
        if ans:
            return ok(f"{octets}.in-addr.arpa domain name pointer {ans}.\n")
        return fail(f"Host {octets}.in-addr.arpa. not found: 3(NXDOMAIN)", 1)
    n = name.lower().rstrip(".")
    if rtype in ("", "A"):
        vals = net.dns_lookup(n, "A")
        if vals is None:
            vals2 = net.dns_lookup(f"{n}.brightside.lab", "A")
            if vals2 is None:
                return fail(f"Host {name} not found: 3(NXDOMAIN)", 1)
            n, vals = f"{n}.brightside.lab", vals2
        out = []
        rec = net.zones[net.zone_for(n)][n]
        if "CNAME" in rec:
            out.append(f"{n} is an alias for {rec['CNAME'][0]}")
        for v in vals:
            if re.match(r"\d", v):
                out.append(f"{rec['CNAME'][0].rstrip('.') if 'CNAME' in rec else n} has address {v}")
        return ok("\n".join(out) + "\n")
    if rtype == "MX":
        vals = net.dns_lookup(n, "MX")
        if not vals:
            return ok(f"{n} has no MX record\n") if vals == [] else fail(f"Host {name} not found: 3(NXDOMAIN)", 1)
        return ok("".join(f"{n} mail is handled by {v}\n" for v in vals))
    if rtype == "TXT":
        vals = net.dns_lookup(n, "TXT")
        if not vals:
            return ok(f"{n} has no TXT record\n") if vals == [] else fail(f"Host {name} not found: 3(NXDOMAIN)", 1)
        return ok("".join(f'{n} descriptive text {v}\n' for v in vals))
    if rtype == "NS":
        vals = net.dns_lookup(n, "NS") or []
        return ok("".join(f"{n} name server {v}\n" for v in vals) or f"{n} has no NS record\n")
    return fail(f"host: invalid type: {rtype}", 1)


# --------------------------------------------------------------------- curl
CURL_ERR = {
    6: "Could not resolve host: {host}",
    7: "Failed to connect to {host} port {port} after 0 ms: Connection refused",
    28: "Failed to connect to {host} port {port} after 130001 ms: Connection timed out",
    35: "error:0A00010B:SSL routines::wrong version number",
}


def _read_cookie_file(sh, spec: str) -> dict[str, str]:
    if "=" in spec and not spec.startswith("/") and "\n" not in spec:
        return dict(p.strip().split("=", 1) for p in spec.split(";") if "=" in p)
    try:
        text = sh.vfs.read(spec, sh.cwd).decode(errors="replace")
    except VFSError:
        return {}
    out = {}
    for ln in text.splitlines():
        ln = ln.replace("#HttpOnly_", "")
        if ln.startswith("#") or not ln.strip():
            continue
        f = ln.split("\t")
        if len(f) >= 7:
            out[f[5]] = f[6]
    return out


def _cert_date(iso: str) -> str:
    import datetime

    try:
        return datetime.datetime.fromisoformat(iso).strftime("%b %e %H:%M:%S %Y GMT")
    except ValueError:
        return iso


def _split_short_flags(args: list[str]) -> list[str]:
    """-sSL -> -s -S -L, and -XPOST -> -X POST (flags that take a value end the cluster)."""
    takes_value = set("XHdbcuAeowm")
    out: list[str] = []
    for a in args:
        if len(a) > 2 and a[0] == "-" and a[1] != "-":
            for k, ch in enumerate(a[1:], 1):
                out.append("-" + ch)
                if ch in takes_value:
                    if a[k + 1 :]:
                        out.append(a[k + 1 :])
                    break
        else:
            out.append(a)
    return out


@command("curl")
def curl(sh, args, stdin):
    net, src = sh.world.net, sh.world.vantage
    method, headers, data, urls = None, {}, [], []
    show_headers = head_only = verbose = silent = follow = fail_http = show_err = False
    out_file, remote_name, cookies_in, jar_out, auth, writeout = None, False, {}, None, None, None
    insecure = False
    pinned: dict[tuple[str, int], str] = {}  # --resolve HOST:PORT:ADDR
    i = 0
    A = _split_short_flags(list(args))
    while i < len(A):
        a = A[i]

        def val() -> str:
            nonlocal i
            i += 1
            return A[i] if i < len(A) else ""

        if a in ("-X", "--request"):
            method = val().upper()
        elif a in ("-H", "--header"):
            k, _, v = val().partition(":")
            headers[k.strip()] = v.strip()
        elif a in ("-d", "--data", "--data-raw", "--data-binary", "--data-urlencode"):
            d = val()
            if d.startswith("@") and a != "--data-raw":
                try:
                    d = sh.vfs.read(d[1:], sh.cwd).decode(errors="replace").rstrip("\n")
                except VFSError:
                    return fail(f"Warning: Couldn't read data from file \"{d[1:]}\", this makes an empty POST.\n", 26)
            data.append(d)
        elif a in ("-b", "--cookie"):
            cookies_in.update(_read_cookie_file(sh, val()))
        elif a in ("-c", "--cookie-jar"):
            jar_out = val()
        elif a in ("-u", "--user"):
            auth = val()
        elif a in ("-A", "--user-agent"):
            headers["User-Agent"] = val()
        elif a in ("-e", "--referer"):
            headers["Referer"] = val()
        elif a in ("-o", "--output"):
            out_file = val()
        elif a in ("-w", "--write-out"):
            writeout = val()
        elif a in ("-m", "--max-time", "--max-redirs", "--connect-timeout", "--retry"):
            val()
        elif a == "--url":
            urls.append(val())
        elif a == "--resolve":
            hp = val().split(":")
            if len(hp) == 3 and hp[1].isdigit():
                pinned[(hp[0].lower(), int(hp[1]))] = hp[2]
        elif a.startswith("-") and not a.startswith("--") and len(a) > 1:
            for ch in a[1:]:
                if ch == "i":
                    show_headers = True
                elif ch == "I":
                    head_only = True
                elif ch == "v":
                    verbose = True
                elif ch == "s":
                    silent = True
                elif ch == "S":
                    show_err = True
                elif ch == "L":
                    follow = True
                elif ch == "f":
                    fail_http = True
                elif ch == "O":
                    remote_name = True
                elif ch == "k":
                    insecure = True
                elif ch in "gG":
                    pass
                else:
                    return fail(f"curl: option -{ch}: is unknown\ncurl: try 'curl --help' or 'curl --manual' for more information", 2)
        elif a in ("--silent",):
            silent = True
        elif a in ("--location",):
            follow = True
        elif a in ("--verbose",):
            verbose = True
        elif a in ("--include",):
            show_headers = True
        elif a in ("--head",):
            head_only = True
        elif a in ("--insecure", "--compressed", "--fail", "--show-error", "--globoff"):
            fail_http = fail_http or a == "--fail"
            insecure = insecure or a == "--insecure"
        elif a.startswith("--"):
            return fail(f"curl: option {a}: is unknown\ncurl: try 'curl --help' or 'curl --manual' for more information", 2)
        else:
            urls.append(a)
        i += 1
    if not urls:
        return fail("curl: try 'curl --help' or 'curl --manual' for more information", 2)
    if method is None:
        method = "HEAD" if head_only else ("POST" if data else "GET")
    body = "&".join(data).encode()
    if auth:
        headers["Authorization"] = "Basic " + base64.b64encode(auth.encode()).decode()
    out: list[bytes] = []
    err: list[str] = []
    status_code = 0
    exit_code = 0
    jar_host = ""
    event(sh, "net:curl")
    for url in urls:
        cur, redirects = url, 0
        while True:
            m = re.fullmatch(r"(?:(https?)://)?([^/:?#]+)(?::(\d+))?([/?#].*)?", cur)
            if not m:
                err.append(f"curl: (3) URL using bad/illegal format or missing URL")
                exit_code = 3
                break
            scheme, hostname, port_s, rest = m.group(1) or "http", m.group(2), m.group(3), m.group(4) or "/"
            port = int(port_s) if port_s else (443 if scheme == "https" else 80)
            jar_host = hostname
            ip = pinned.get((hostname.lower(), port)) or resolve(sh, hostname)
            if ip is None:
                err.append("curl: (6) " + CURL_ERR[6].format(host=hostname))
                exit_code = 6
                break
            host = net.host_by_ip(ip)
            fail_line = None
            if ip == src or ip.startswith("127."):
                state = "closed"
            elif net.route(src, ip) is None or host is None:
                state = "unreach"
            else:
                state = net.port_state(src, host, port)
            if state in ("closed", "unreach"):
                if state == "unreach":
                    err.append(f"curl: (7) Failed to connect to {hostname} port {port} after 0 ms: No route to host" if host is None and net.route(src, ip) is not None
                               else f"curl: (7) Failed to connect to {hostname} port {port} after 0 ms: Network is unreachable")
                else:
                    err.append("curl: (7) " + CURL_ERR[7].format(host=hostname, port=port))
                exit_code = 7
                break
            if state == "filtered":
                err.append("curl: (28) " + CURL_ERR[28].format(host=hostname, port=port))
                exit_code = 28
                break
            svc = host.services[port]
            if svc.kind != "http":
                err.append("curl: (1) Received HTTP/0.9 when not allowed" if svc.kind == "banner" else "curl: (56) Recv failure: Connection reset by peer")
                exit_code = 1 if svc.kind == "banner" else 56
                break
            tls_lines: list[str] = []
            if scheme == "https":
                if not svc.tls:
                    err.append("curl: (35) " + CURL_ERR[35])
                    exit_code = 35
                    break
                cert = svc.cert or {}
                if not insecure:
                    err.append("curl: (60) SSL certificate problem: self-signed certificate\nMore details here: https://curl.se/docs/sslcerts.html\n\n"
                               "curl failed to verify the legitimacy of the server and therefore could not\nestablish a secure connection to it. "
                               "To learn more about this situation and\nhow to fix it, please visit the web page mentioned above.")
                    exit_code = 60
                    break
                tls_lines = [
                    "* TLSv1.3 (OUT), TLS handshake, Client hello (1):", "* TLSv1.3 (IN), TLS handshake, Server hello (2):",
                    "* TLSv1.3 (IN), TLS handshake, Certificate (11):", "* TLSv1.3 (OUT), TLS handshake, Finished (20):",
                    "* SSL connection using TLSv1.3 / TLS_AES_256_GCM_SHA384", "* Server certificate:",
                    f"*  subject: CN={cert.get('cn', hostname)}", f"*  start date: {_cert_date(cert.get('not_before', ''))}",
                    f"*  expire date: {_cert_date(cert.get('not_after', ''))}", f"*  issuer: CN={cert.get('cn', hostname)}",
                    "*  SSL certificate verify result: self-signed certificate (18), continuing anyway.",
                ]
            path, _, qs = rest.split("#")[0].partition("?")
            hdrs = {"host": hostname if not port_s else f"{hostname}:{port}", "user-agent": "curl/7.81.0", "accept": "*/*"}
            hdrs.update({k.lower(): v for k, v in headers.items()})
            if cookies_in:
                hdrs["cookie"] = "; ".join(f"{k}={v}" for k, v in cookies_in.items())
            if body:
                hdrs.setdefault("content-type", "application/x-www-form-urlencoded")
                hdrs["content-length"] = str(len(body))
            query = dict(p.partition("=")[::2] for p in qs.split("&") if p)
            req = Request("GET" if method == "HEAD" else method, path or "/", query, hdrs, body, {k: v for k, v in cookies_in.items()}, src, hdrs["host"], net=net, server_host=host)
            resp = plain_to_tls(svc) if (svc.tls and scheme == "http") else serve(svc, req)
            for k, v in resp.cookies.items():
                cookies_in[k] = v
            if verbose:
                err.append(f"*   Trying {ip}:{port}...\n* Connected to {hostname} ({ip}) port {port} (#0)")
                err += tls_lines
                err.append(f"> {method} {rest} HTTP/1.1")
                err += [f"> {k.title()}: {v}" for k, v in hdrs.items() if k != "content-length" or body]
                err.append(">")
                err.append(f"< HTTP/1.1 {resp.status} {REASONS.get(resp.status, 'Unknown')}")
                err += [f"< {k}: {v}" for k, v in resp.headers.items()] + [f"< Set-Cookie: {k}={v}; Path=/; HttpOnly" for k, v in resp.cookies.items()]
                err.append("<")
            status_code = resp.status
            if (show_headers or head_only) and not verbose:
                lines = [f"HTTP/1.1 {resp.status} {REASONS.get(resp.status, 'Unknown')}"] + [f"{k}: {v}" for k, v in resp.headers.items()] \
                    + [f"Set-Cookie: {k}={v}; Path=/; HttpOnly" for k, v in resp.cookies.items()]
                out.append(("\r\n".join(lines) + "\r\n\r\n").encode())
            if follow and resp.status in (301, 302) and "Location" in resp.headers and redirects < 10:
                loc = resp.headers["Location"]
                cur = loc if re.match(r"https?://", loc) else f"{scheme}://{hostname}{':' + port_s if port_s else ''}{loc}"
                method, body, redirects = ("GET" if method in ("POST",) else method), (b"" if method == "POST" else body), redirects + 1
                continue
            if fail_http and resp.status >= 400:
                err.append(f"curl: (22) The requested URL returned error: {resp.status}")
                exit_code = 22
                break
            if method != "HEAD":
                payload = resp.raw_body()
                if remote_name or out_file:
                    name = out_file or (path.rsplit("/", 1)[-1] or "index.html")
                    try:
                        sh.vfs.write(name, payload, sh.cwd)
                    except VFSError as e:
                        err.append(f"curl: (23) Failure writing output to destination ({e})")
                        exit_code = 23
                else:
                    out.append(payload)
            break
    if jar_out is not None:
        lines = ["# Netscape HTTP Cookie File", "# https://curl.se/docs/http-cookies.html", "# This file was generated by libcurl! Edit at your own risk.", ""]
        lines += [f"#HttpOnly_{jar_host}\tFALSE\t/\tFALSE\t0\t{k}\t{v}" for k, v in cookies_in.items()]
        try:
            sh.vfs.write(jar_out, ("\n".join(lines) + "\n").encode(), sh.cwd)
        except VFSError:
            pass
    payload = b"".join(out)
    if writeout:
        payload += writeout.replace("\\n", "\n").replace("%{http_code}", str(status_code)).encode()
    shown_err = [e for e in err if (verbose and not e.startswith("curl:")) or (e.startswith("curl:") and (not silent or show_err))]
    if verbose:
        shown_err = [e for e in err]
    r = Result(out=payload, err="".join(e + "\n" for e in shown_err).encode(), status=exit_code)
    return r


# ------------------------------------------------------------------ ss / netstat
def _my_listeners(sh):
    net, vantage = sh.world.net, sh.world.vantage
    return sorted((l for (ip, port), l in net.listeners.items() if ip == vantage), key=lambda l: l.port)


@command("ss")
def ss(sh, args, stdin):
    flags, _ = parse_flags(args)
    show_proc = bool(flags.get("p"))
    head = "State       Recv-Q Send-Q Local Address:Port    Peer Address:Port  " + ("Process\n" if show_proc else "\n")
    rows = []
    for l in _my_listeners(sh):
        local = f"0.0.0.0:{l.port}"
        proc = f"users:((\"nc\",pid={l.job.pid},fd=3))" if show_proc and getattr(l.job, "pid", 0) else ""
        rows.append(f"{'LISTEN':<11} {0:<6} {128:<6} {local:<22} {'0.0.0.0:*':<18} {proc}\n")
    return ok(head + "".join(rows))


@command("netstat")
def netstat(sh, args, stdin):
    flags, _ = parse_flags(args)
    show_proc = bool(flags.get("p"))
    out = ["Active Internet connections (only servers)\n",
           "Proto Recv-Q Send-Q Local Address           Foreign Address         State       " + ("PID/Program name\n" if show_proc else "\n")]
    for l in _my_listeners(sh):
        local = f"0.0.0.0:{l.port}"
        proc = f"{l.job.pid}/nc" if show_proc and getattr(l.job, "pid", 0) else ""
        out.append(f"tcp        0      0 {local:<23} {'0.0.0.0:*':<23} {'LISTEN':<11} {proc}\n")
    return ok("".join(out))
