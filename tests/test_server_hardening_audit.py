import json

from conftest import FIXTURES, load_script, run_script

SKILL = "server-hardening-audit"
SCRIPT = "audit_server.py"
mod = load_script(SKILL, SCRIPT)
BAD = FIXTURES / SKILL / "bad"
GOOD = FIXTURES / SKILL / "good"


def checks(findings, severity=None):
    return {f.check for f in findings if severity is None or f.severity == severity}


def test_bad_server_findings():
    findings = mod.audit(mod.collect_from_dir(BAD))
    high = checks(findings, "high")
    assert {
        "ssh.permitrootlogin",
        "ssh.passwordauthentication",
        "firewall.default",
        "firewall.rule",
        "accounts.uid0",
        "ports.risky",
    } <= high
    assert "fail2ban" in checks(findings, "medium")
    assert "updates" in checks(findings, "medium")
    assert "updates.reboot" in checks(findings, "low")
    # findings are sorted high first
    assert findings[0].severity == "high"


def test_good_server_has_only_info():
    findings = mod.audit(mod.collect_from_dir(GOOD))
    assert {f.severity for f in findings} == {"info"}


def test_fixes_use_early_dropin_so_they_win_over_cloud_init():
    findings = mod.audit(mod.collect_from_dir(BAD))
    fixes = [c for f in findings if f.check.startswith("ssh.") for c in f.fix]
    assert any("00-hardening.conf" in c for c in fixes)
    assert not any("99-" in c for c in fixes)


def test_parse_ss_handles_ipv6_and_interface_suffix():
    rows = mod.parse_ss(
        'tcp LISTEN 0 511 [::]:443 [::]:* users:(("nginx",pid=1,fd=7))\n'
        'tcp LISTEN 0 4096 127.0.0.53%lo:53 0.0.0.0:* users:(("systemd-resolve",pid=2,fd=3))\n'
    )
    assert rows[0]["address"] == "::" and rows[0]["port"] == "443" and rows[0]["process"] == "nginx"
    assert rows[1]["address"] == "127.0.0.53"
    assert not mod.is_public_address(rows[1]["address"])
    assert mod.is_public_address("0.0.0.0")
    assert not mod.is_public_address("10.0.0.5")
    assert not mod.is_public_address("172.20.0.1")


def test_missing_inputs_are_reported_not_crashing():
    findings = mod.audit(mod.Sources())
    titles = " ".join(f.title for f in findings)
    assert "SSH configuration not checked" in titles
    assert "Firewall state unknown" in titles


def test_inactive_ufw_is_high():
    f = mod.check_firewall(mod.Sources(ufw_status="Status: inactive\n"))
    assert f[0].severity == "high"
    assert "sudo ufw allow OpenSSH" in f[0].fix


def test_cli_exit_codes_and_json():
    bad = run_script(SKILL, SCRIPT, "--from-dir", str(BAD), "--json")
    assert bad.returncode == 1
    data = json.loads(bad.stdout)
    assert any(f["check"] == "ssh.passwordauthentication" for f in data["findings"])
    good = run_script(SKILL, SCRIPT, "--from-dir", str(GOOD))
    assert good.returncode == 0
    assert "Nothing was changed" in good.stdout
    none = run_script(SKILL, SCRIPT, "--from-dir", str(BAD), "--fail-on", "none")
    assert none.returncode == 0


def test_cli_help_and_collect_commands():
    assert run_script(SKILL, SCRIPT, "--help").returncode == 0
    out = run_script(SKILL, SCRIPT, "--print-collect-commands").stdout
    assert "sshd -T > sshd_T.txt" in out


def test_cli_missing_dir_is_error(tmp_path):
    r = run_script(SKILL, SCRIPT, "--from-dir", str(tmp_path / "nope"))
    assert r.returncode == 2
