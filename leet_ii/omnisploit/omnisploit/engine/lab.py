"""The default lab network: a few fictional Brightside machines to scan, talk to and log in to.

Hosts (all invented):
  10.10.0.1   gw        router / DNS (allows zone transfers: a classic misconfiguration)
  10.10.0.10  intranet  web server, SSH, and a second interface on the internal subnet 10.20.0.0/24
  10.10.0.20  files     FTP and a small custom service on a port a default scan does not cover
  10.10.0.30  db        drops pings, and only the intranet host may reach its database port
  10.20.0.5   vault     internal only: reachable from the intranet host, not from the player's machine
"""

from __future__ import annotations

import base64
import json
import random
import re

from .network import Account, Host, Network, Request, Response, Service, ShellChannel

SERIAL = "2026112201"
# the lab's discovered secrets: fixed defaults, used whenever no per-save seed is given (every
# existing test, and any World() built without flag_seed). _secrets(seed) below gives each player
# their own unique values instead -- stable across reloads, useless to copy from someone else's
# walkthrough. ADMIN_USER/STAFF_USER are plain usernames, not secrets, so they stay fixed either way.
DEPLOY_PASS = "Winter2026!"
ADMIN_USER = "admin"
ADMIN_PASS = "s3cretadmin"
STAFF_USER = "staff"
STAFF_PASS = "Spring#2026"
TICKET = "4417"
FLAG_ADMIN = "FLAG{curl_knows_the_way}"
FLAG_VHOST = "FLAG{virtual_hosts_hide_things}"
FLAG_CTL = "FLAG{nc_talks_tcp}"
FLAG_WWWDATA = "FLAG{reverse_shells_work}"
FLAG_PIVOT = "FLAG{pivot_complete}"

_DEFAULT_SECRETS = {
    "deploy_pass": DEPLOY_PASS, "admin_pass": ADMIN_PASS, "staff_pass": STAFF_PASS, "ticket": TICKET,
    "flag_admin": FLAG_ADMIN, "flag_vhost": FLAG_VHOST, "flag_ctl": FLAG_CTL,
    "flag_wwwdata": FLAG_WWWDATA, "flag_pivot": FLAG_PIVOT,
}


def _secrets(seed: int | None) -> dict[str, str]:
    """The same shape every time, but unique text for each (non-None) seed."""
    if seed is None:
        return dict(_DEFAULT_SECRETS)
    rng = random.Random(f"omnisploit-lab:{seed}")

    def word(n: int) -> str:
        return "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(n))

    def flag() -> str:
        return "FLAG{" + "".join(rng.choice("0123456789abcdefghijklmnopqrstuvwxyz") for _ in range(20)) + "}"

    return {
        "deploy_pass": f"{word(7).capitalize()}{rng.randint(1000, 9999)}!",
        "admin_pass": word(11),
        "staff_pass": f"{word(6).capitalize()}#{rng.randint(1000, 9999)}",
        "ticket": str(rng.randint(1000, 9999)),
        "flag_admin": flag(), "flag_vhost": flag(), "flag_ctl": flag(),
        "flag_wwwdata": flag(), "flag_pivot": flag(),
    }


def _secrets_of(net: Network | None) -> dict[str, str]:
    """The secrets a running service should use: the Network's own if it has them, else the fixed defaults."""
    if net is not None and net.secrets:
        return net.secrets
    return _DEFAULT_SECRETS

# a classic blind command-injection pattern: a separator character breaking out of the intended
# command, followed by a netcat reverse-shell one-liner (either argument order) or a /dev/tcp redirect
_INJECT_SEP = re.compile(r"[;|`]|\$\(")
_IPV4 = r"\d{1,3}(?:\.\d{1,3}){3}"
_REV_SHELL = re.compile(rf"n(?:c|cat)\b.*?({_IPV4}).*?\b(\d{{2,5}})\b|/dev/tcp/({_IPV4})/(\d{{1,5}})", re.S)
ZONE = {
    "brightside.lab": {
        "SOA": [f"ns1.brightside.lab. hostmaster.brightside.lab. {SERIAL} 7200 3600 1209600 3600"],
        "NS": ["ns1.brightside.lab."],
        "MX": ["10 mail.brightside.lab."],
        "TXT": ['"v=spf1 mx -all"'],
    },
    "gw.brightside.lab": {"A": ["10.10.0.1"]},
    "ns1.brightside.lab": {"A": ["10.10.0.1"]},
    "intranet.brightside.lab": {"A": ["10.10.0.10"]},
    "www.brightside.lab": {"CNAME": ["intranet.brightside.lab."]},
    "files.brightside.lab": {"A": ["10.10.0.20"]},
    "mail.brightside.lab": {"A": ["10.10.0.20"]},
    "db.brightside.lab": {"A": ["10.10.0.30"]},
    "vault.brightside.lab": {"A": ["10.20.0.5"]},
    "staging.brightside.lab": {"A": ["10.10.0.99"]},
    "_notes.brightside.lab": {"TXT": ['"ctl service for ops lives on files, port 9999"']},
}

INDEX = """<html><head><title>Brightside Intranet</title></head>
<body><h1>Brightside Intranet</h1>
<p>Welcome. <a href="/login">Staff login</a> | <a href="/api/status">Status</a></p>
<!-- TODO: remove /backup before the audit -->
</body></html>
"""
NOTFOUND = """<html>
<head><title>404 Not Found</title></head>
<body>
<center><h1>404 Not Found</h1></center>
<hr><center>nginx/1.18.0 (Ubuntu)</center>
</body>
</html>
"""
def _deploy_notes(secrets: dict[str, str]) -> str:
    return (
        "deploy notes\n------------\n"
        "The deploy account on intranet was reset after the incident.\n"
        f"Temporary password: {secrets['deploy_pass']}   (change it on first login)\n\n"
        f"Admin console (/admin): {ADMIN_USER} / {secrets['admin_pass']}   -- TODO rotate, it is in three wikis\n"
    )


def _hr_page(secrets: dict[str, str]) -> str:
    return (
        "<html><head><title>Brightside HR</title></head>\n"
        f"<body><h1>HR portal (internal vhost)</h1><p>Reference: {secrets['flag_vhost']}</p></body></html>\n"
    )


CERT = {
    "cn": "intranet.brightside.lab",
    "san": ["intranet.brightside.lab", "www.brightside.lab", "hr.brightside.lab"],
    "not_before": "2026-09-01T00:00:00",
    "not_after": "2027-09-01T00:00:00",
}
VAULT_KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nSIM-KEY-ops-vault-7f3a\n-----END OPENSSH PRIVATE KEY-----\n"


def _text(body: str, status: int = 200, ctype: str = "text/plain", **headers) -> Response:
    return Response(status, {"Content-Type": ctype, **headers}, body)


def _form(body: bytes) -> dict[str, str]:
    out = {}
    for part in body.decode(errors="replace").split("&"):
        if "=" in part:
            k, _, v = part.partition("=")
            out[k] = v.replace("+", " ").replace("%23", "#").replace("%21", "!")
    return out


def intranet_app(req: Request) -> Response:
    secrets = _secrets_of(req.net)
    p = req.path.rstrip("/") or "/"
    vhost = req.host.split(":")[0].lower()
    if vhost == "hr.brightside.lab":  # a virtual host: same server, different site, chosen by the Host header
        return Response(200, {}, _hr_page(secrets)) if p == "/" else Response(404, {}, NOTFOUND)
    if p == "/":
        return Response(200, {}, INDEX)
    if p == "/robots.txt":
        return _text("User-agent: *\nDisallow: /backup/\nDisallow: /admin/\n")
    if p == "/backup":
        if not req.path.endswith("/"):
            return Response(301, {"Location": "/backup/"}, "")
        return Response(200, {}, '<html><head><title>Index of /backup/</title></head><body><h1>Index of /backup/</h1><hr><pre>\n'
                                 '<a href="deploy-notes.txt">deploy-notes.txt</a>    21-Nov-2026 09:14    142\n</pre><hr></body></html>\n')
    if p == "/backup/deploy-notes.txt":
        return _text(_deploy_notes(secrets))
    if p == "/admin":
        auth = req.headers.get("authorization", "")
        want = "Basic " + base64.b64encode(f"{ADMIN_USER}:{secrets['admin_pass']}".encode()).decode()
        if auth != want:
            return Response(401, {"WWW-Authenticate": 'Basic realm="Brightside Admin"'}, "<html><body><h1>401 Authorization Required</h1></body></html>\n")
        return Response(200, {}, "<html><head><title>Admin</title></head><body><h1>Admin console</h1><p>" + secrets["flag_admin"] + "</p></body></html>\n")
    if p == "/login":
        if req.method == "POST":
            f = _form(req.body)
            if f.get("user") == STAFF_USER and f.get("pass") == secrets["staff_pass"]:
                return Response(302, {"Location": "/portal"}, "", cookies={"session": "staff-8f2a61"})
            return Response(401, {}, "<html><body>Invalid credentials</body></html>\n")
        return Response(200, {}, '<html><head><title>Login</title></head><body><form method="POST" action="/login">'
                                 'user <input name="user"> pass <input name="pass" type="password"><input type="submit"></form>'
                                 f'<!-- test account for QA: {STAFF_USER} / {secrets["staff_pass"]} --></body></html>\n')
    if p == "/portal":
        if req.cookies.get("session") != "staff-8f2a61":
            return Response(302, {"Location": "/login"}, "")
        return Response(200, {}, "<html><head><title>Staff portal</title></head><body><h1>Staff portal</h1>"
                                 "<p>Welcome back. Ticket " + secrets["ticket"] + ": rotate the files-server ctl token.</p></body></html>\n")
    if p == "/api/status":
        return Response(200, {"Content-Type": "application/json", "X-Powered-By": "Brightside/2.4"}, json.dumps({"status": "ok", "version": "2.4.1"}) + "\n")
    return Response(404, {}, NOTFOUND)


def _ping_diag(req: Request) -> Response:
    target = req.query.get("host", "")
    if not target:
        return _text("Usage: /ping?host=HOST\n", 400)
    if _INJECT_SEP.search(target):
        m = _REV_SHELL.search(target)
        if m and req.net is not None and req.server_host is not None:
            ip, port = (m.group(1) or m.group(3)), int(m.group(2) or m.group(4))
            req.net.deliver(req.server_host.ips[0], req.client_ip, port, ShellChannel(req.server_host.name, "www-data"))
    # blind: the response never reveals whether the injected command actually ran
    shown = target.split()[0] if target.split() else target
    return _text(f"PING {shown}: diagnostic complete\n")


def tools_app(req: Request) -> Response:
    if req.path == "/status":
        return _text("OK\n")
    if req.path == "/ping":
        return _ping_diag(req)
    return Response(403, {"Content-Type": "text/html"}, "<html><body><h1>403 Forbidden</h1><p>Staff network only.</p></body></html>\n")


def ftp_app(line: str, ch) -> str | None:
    cmd, _, arg = line.strip().partition(" ")
    cmd = cmd.upper()
    if cmd == "USER":
        ch.user = arg
        return "331 Please specify the password.\r\n"
    if cmd == "PASS":
        return "230 Login successful.\r\n" if getattr(ch, "user", "") in ("anonymous", "ftp") else "530 Login incorrect.\r\n"
    if cmd == "SYST":
        return "215 UNIX Type: L8\r\n"
    if cmd == "PWD":
        return '257 "/" is the current directory\r\n'
    if cmd == "FEAT":
        return "211-Features:\r\n PASV\r\n UTF8\r\n211 End\r\n"
    if cmd == "QUIT":
        ch.quit = True
        return "221 Goodbye.\r\n"
    if cmd in ("LIST", "RETR", "STOR"):
        return "425 Use PORT or PASV first.\r\n"
    return "500 Unknown command.\r\n" if cmd else None


def bctl_app(line: str, ch) -> str | None:
    cmd = line.strip().upper()
    if not cmd:
        return None
    if cmd == "HELP":
        return "Commands: HELP VERSION STATUS QUIT\n"
    if cmd == "VERSION":
        return "bctl 1.2\n"
    if cmd == "STATUS":
        return "ok\n"
    if cmd == "QUIT":
        ch.quit = True
        return "bye\n"
    if cmd == "FLAG":  # undocumented
        return _secrets_of(getattr(ch, "net", None))["flag_ctl"] + "\n"
    return "ERR unknown command\n"


def udp_echo(line: str, ch) -> str | None:
    return line + "\n"


def dns_app(req):  # pragma: no cover - the DNS service is answered by the dig/host tools directly
    return None


def build(net: Network, seed: int | None = None) -> Network:
    secrets = _secrets(seed)
    net.secrets = secrets
    net.add_zone("brightside.lab", ZONE)
    net.axfr_servers = {"10.10.0.1"}

    net.add(Host(
        "gw.brightside.lab", ["10.10.0.1"], aliases=["ns1.brightside.lab"], os="Linux (router)", ttl=255,
        services={53: Service(53, "domain", "dns", product="dnsmasq 2.86")},
        udp={53: Service(53, "domain", "dns", product="dnsmasq 2.86")},
        hostkey="SHA256:gwKey0n3v9Yd1b0cX7bQxk8mHq2Zp4s6aWl1uTjDFe0",
    ))
    net.add(Host(
        "intranet.brightside.lab", ["10.10.0.10", "10.20.0.10"], aliases=["www.brightside.lab"],
        services={
            22: Service(22, "ssh", "ssh", product="OpenSSH 8.9p1 Ubuntu 3ubuntu0.6", extra="(Ubuntu Linux; protocol 2.0)",
                        banner="SSH-2.0-OpenSSH_8.9p1 Ubuntu-3ubuntu0.6"),
            80: Service(80, "http", "http", product="nginx 1.18.0", extra="(Ubuntu)", app=intranet_app, server="nginx/1.18.0 (Ubuntu)"),
            443: Service(443, "https", "http", product="nginx 1.18.0", extra="(Ubuntu)", app=intranet_app, server="nginx/1.18.0 (Ubuntu)",
                         tls=True, cert=CERT),
            8080: Service(8080, "http-proxy", "http", product="Jetty 9.4.43.v20210629", app=tools_app, server="Jetty(9.4.43.v20210629)"),
        },
        accounts={"deploy": Account(secrets["deploy_pass"])},
        files={
            "/home/deploy/README.txt": "Deploy box. Web root is /var/www/html.\nVault access: see vault_key and notes.txt.\n",
            "/home/deploy/notes.txt": "The vault host is only reachable from here (10.20.0.5). Log in as ops with ~/.ssh/vault_key.\n",
            "/home/deploy/.ssh/vault_key": VAULT_KEY,
            "/home/deploy/flag.txt": "FLAG{ssh_is_a_door}\n",
            "/var/www/.secret.txt": (secrets["flag_wwwdata"] + "\n", "www-data", 0o600),
        },
        motd="Welcome to Ubuntu 22.04.3 LTS (GNU/Linux 5.15.0-91-generic x86_64)\n",
        hostkey="SHA256:q7Xb2n4KJd0wUe1sYtV8nZ3mPfH6rA9cLo5iGv1BkTE",
        level_id="logsweep",
    ))
    net.add(Host(
        "files.brightside.lab", ["10.10.0.20"], aliases=["mail.brightside.lab"],
        services={
            21: Service(21, "ftp", "banner", product="vsftpd 3.0.5", banner="220 (vsFTPd 3.0.5)\r\n", app=ftp_app),
            9999: Service(9999, "abyss", "banner", product="", banner="BCTL 1.2 ready\n", app=bctl_app),
        },
        udp={7: Service(7, "echo", "banner", app=udp_echo)},
    ))
    net.add(Host(
        "db.brightside.lab", ["10.10.0.30"], icmp=False, stealth=True,
        services={3306: Service(3306, "mysql", "banner", product="MySQL 5.7.42-log", banner="5.7.42-log\n",
                                acl={"10.10.0.10", "10.20.0.10"})},
    ))
    net.add(Host(
        "vault.brightside.lab", ["10.20.0.5"],
        services={
            22: Service(22, "ssh", "ssh", product="OpenSSH 8.9p1 Ubuntu 3ubuntu0.6", extra="(Ubuntu Linux; protocol 2.0)",
                        banner="SSH-2.0-OpenSSH_8.9p1 Ubuntu-3ubuntu0.6"),
            5432: Service(5432, "postgresql", "banner", product="PostgreSQL DB 14.9", acl={"10.20.0.0/24"}),
        },
        accounts={"ops": Account(password="", key="SIM-KEY-ops-vault-7f3a")},
        files={"/home/ops/flag.txt": secrets["flag_pivot"] + "\n", "/home/ops/README.txt": "Vault host. Internal use only.\n"},
        motd="Welcome to Ubuntu 22.04.3 LTS (GNU/Linux 5.15.0-91-generic x86_64)\n",
        hostkey="SHA256:vLt3Z9bR1uXq0e7Wc5mYh2nKfA8oSdP4jGi6TwE1BxU",
    ))
    return net
