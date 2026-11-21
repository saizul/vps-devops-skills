#!/usr/bin/env python3
"""Read-only hardening audit for an Ubuntu/Debian server.

Collects SSH, firewall, fail2ban, unattended-upgrades, account and listening
port information and prints findings with suggested fixes. It never changes the
system: it only runs read-only commands (``sshd -T``, ``ufw status verbose``,
``fail2ban-client status``, ``ss -tulpnH``) and reads files.

Run it live on the server (``sudo python3 audit_server.py``) or point it at a
directory of captured command outputs (``--from-dir``) gathered from another
machine. ``--print-collect-commands`` shows how to capture them.

Exit codes: 0 = no findings at or above --fail-on, 1 = findings, 2 = error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

SEVERITIES = ["info", "low", "medium", "high"]

# Services that should almost never listen on a public interface.
RISKY_PORTS = {
    "2375": "Docker API (unauthenticated)",
    "2376": "Docker API",
    "3306": "MySQL/MariaDB",
    "5432": "PostgreSQL",
    "6379": "Redis",
    "9200": "Elasticsearch/OpenSearch",
    "9300": "Elasticsearch transport",
    "11211": "Memcached",
    "27017": "MongoDB",
    "5984": "CouchDB",
    "8086": "InfluxDB",
    "15672": "RabbitMQ management",
    "5672": "RabbitMQ",
    "9090": "Prometheus",
    "3000": "Grafana / Node dev server",
}

# name in --from-dir -> (command or file it was captured from)
CAPTURE_FILES = {
    "sshd_T.txt": "sudo sshd -T",
    "ufw_status.txt": "sudo ufw status verbose",
    "fail2ban_status.txt": "sudo fail2ban-client status",
    "auto_upgrades.txt": "cat /etc/apt/apt.conf.d/20auto-upgrades",
    "group.txt": "cat /etc/group",
    "passwd.txt": "cat /etc/passwd",
    "ss_listen.txt": "sudo ss -tulpnH",
    "reboot_required.txt": "cat /var/run/reboot-required 2>/dev/null",
}


@dataclass
class Finding:
    check: str
    severity: str
    title: str
    detail: str = ""
    fix: list[str] = field(default_factory=list)


@dataclass
class Sources:
    """Raw text for every input. ``None`` means "could not be collected"."""

    sshd_t: str | None = None
    ufw_status: str | None = None
    fail2ban_status: str | None = None
    auto_upgrades: str | None = None
    group: str | None = None
    passwd: str | None = None
    ss_listen: str | None = None
    reboot_required: str | None = None
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- parsers


def parse_sshd_t(text: str) -> dict[str, list[str]]:
    """Parse ``sshd -T`` output: lowercase keys, values kept as list (repeatable keys)."""
    out: dict[str, list[str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition(" ")
        out.setdefault(key.lower(), []).append(value.strip())
    return out


def parse_group(text: str) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for line in text.splitlines():
        parts = line.strip().split(":")
        if len(parts) >= 4:
            groups[parts[0]] = [m for m in parts[3].split(",") if m]
    return groups


def parse_passwd(text: str) -> list[dict[str, str]]:
    users = []
    for line in text.splitlines():
        parts = line.strip().split(":")
        if len(parts) >= 7:
            users.append({"name": parts[0], "uid": parts[2], "gid": parts[3], "shell": parts[6]})
    return users


def parse_ss(text: str) -> list[dict[str, str]]:
    """Parse ``ss -tulpnH`` (or ``ss -tulpn`` with a header) output."""
    rows = []
    for line in text.splitlines():
        cols = line.split()
        if len(cols) < 5 or cols[0] == "Netid":
            continue
        proto = cols[0]
        # ss output: Netid State Recv-Q Send-Q Local Peer [Process]
        local = cols[4]
        process = " ".join(cols[6:]) if len(cols) > 6 else ""
        m = re.search(r'"([^"]+)"', process)
        addr, _, port = local.rpartition(":")
        addr = addr.strip("[]")
        if "%" in addr:  # 0.0.0.0%lo or [::]%eth0
            addr = addr.split("%", 1)[0]
        rows.append(
            {
                "proto": proto,
                "address": addr,
                "port": port,
                "process": m.group(1) if m else "",
            }
        )
    return rows


def is_public_address(addr: str) -> bool:
    if addr in ("*", "0.0.0.0", "::", ""):
        return True
    if addr.startswith("127.") or addr == "::1" or addr.startswith("::ffff:127."):
        return False
    if addr.startswith(("10.", "192.168.", "169.254.", "fe80", "fd", "fc")):
        return False
    if re.match(r"^172\.(1[6-9]|2\d|3[01])\.", addr):
        return False
    return True


def parse_apt_periodic(text: str) -> dict[str, str]:
    values = {}
    for m in re.finditer(r'(APT::Periodic::[\w-]+)\s+"([^"]*)"\s*;', text):
        values[m.group(1)] = m.group(2)
    return values


# --------------------------------------------------------------------------- checks


def check_ssh(src: Sources) -> list[Finding]:
    if src.sshd_t is None:
        return [
            Finding(
                "ssh",
                "info",
                "SSH configuration not checked",
                "Could not run `sshd -T` (run the audit with sudo).",
            )
        ]
    cfg = parse_sshd_t(src.sshd_t)

    def get(key: str, default: str = "") -> str:
        return cfg.get(key, [default])[0].lower()

    f: list[Finding] = []
    reload = "sudo sshd -t && sudo systemctl reload ssh"
    dropin = "/etc/ssh/sshd_config.d/00-hardening.conf"
    root = get("permitrootlogin")
    if root == "yes":
        f.append(
            Finding(
                "ssh.permitrootlogin",
                "high",
                "Root can log in over SSH with a password",
                "PermitRootLogin yes",
                [f"echo 'PermitRootLogin no' | sudo tee -a {dropin}", reload],
            )
        )
    elif root in ("prohibit-password", "without-password"):
        f.append(
            Finding(
                "ssh.permitrootlogin",
                "low",
                "Root can log in over SSH with a key",
                f"PermitRootLogin {root}. Prefer a named sudo user and PermitRootLogin no.",
                [f"echo 'PermitRootLogin no' | sudo tee -a {dropin}", reload],
            )
        )
    if get("passwordauthentication") == "yes":
        f.append(
            Finding(
                "ssh.passwordauthentication",
                "high",
                "SSH password login is enabled",
                "Passwords can be brute-forced. Confirm key login works in a second session first.",
                [f"echo 'PasswordAuthentication no' | sudo tee -a {dropin}", reload],
            )
        )
    kbd = get("kbdinteractiveauthentication") or get("challengeresponseauthentication")
    if kbd == "yes" and get("usepam") == "yes":
        f.append(
            Finding(
                "ssh.kbdinteractive",
                "medium",
                "Keyboard-interactive auth is enabled (PAM password prompts)",
                "This can allow password logins even when PasswordAuthentication is no.",
                [f"echo 'KbdInteractiveAuthentication no' | sudo tee -a {dropin}", reload],
            )
        )
    if get("permitemptypasswords") == "yes":
        f.append(
            Finding(
                "ssh.permitemptypasswords",
                "high",
                "Accounts with empty passwords may log in",
                "",
                [f"echo 'PermitEmptyPasswords no' | sudo tee -a {dropin}", reload],
            )
        )
    if get("pubkeyauthentication", "yes") == "no":
        f.append(
            Finding(
                "ssh.pubkey",
                "medium",
                "Public key authentication is disabled",
                "",
                [f"echo 'PubkeyAuthentication yes' | sudo tee -a {dropin}", reload],
            )
        )
    if get("x11forwarding") == "yes":
        f.append(
            Finding(
                "ssh.x11forwarding",
                "low",
                "X11 forwarding is enabled",
                "Servers rarely need it.",
                [f"echo 'X11Forwarding no' | sudo tee -a {dropin}", reload],
            )
        )
    try:
        tries = int(get("maxauthtries", "6"))
    except ValueError:
        tries = 6
    if tries > 4:
        f.append(
            Finding(
                "ssh.maxauthtries",
                "low",
                f"MaxAuthTries is {tries}",
                "3 or 4 slows down guessing.",
                [f"echo 'MaxAuthTries 3' | sudo tee -a {dropin}", reload],
            )
        )
    if not any(cfg.get(k) for k in ("allowusers", "allowgroups")):
        f.append(
            Finding(
                "ssh.allowlist",
                "info",
                "No AllowUsers/AllowGroups restriction",
                "Optional: limit SSH to named users, e.g. AllowUsers deploy.",
            )
        )
    return f


def check_firewall(src: Sources) -> list[Finding]:
    if src.ufw_status is None:
        return [
            Finding(
                "firewall",
                "medium",
                "Firewall state unknown (ufw not installed or not readable)",
                "Check `sudo ufw status verbose` or `sudo nft list ruleset`.",
                ["sudo apt install ufw"],
            )
        ]
    text = src.ufw_status
    low = text.lower()
    f: list[Finding] = []
    if "status: inactive" in low:
        return [
            Finding(
                "firewall.ufw",
                "high",
                "UFW firewall is inactive",
                "Allow SSH before enabling or you will lock yourself out.",
                [
                    "sudo ufw default deny incoming",
                    "sudo ufw default allow outgoing",
                    "sudo ufw allow OpenSSH",
                    "sudo ufw enable",
                ],
            )
        ]
    m = re.search(r"default:\s*(\w+)\s*\(incoming\)", low)
    if m and m.group(1) != "deny" and m.group(1) != "reject":
        f.append(
            Finding(
                "firewall.default",
                "high",
                f"UFW default incoming policy is '{m.group(1)}'",
                "",
                ["sudo ufw default deny incoming"],
            )
        )
    for line in text.splitlines():
        rm = re.match(r"^\s*(\d+)(?:/(tcp|udp))?\s+(ALLOW|LIMIT)\s+IN\s+(.+?)\s*$", line)
        if not rm:
            continue
        port, _, _, source = rm.groups()
        if port in RISKY_PORTS and source.lower().startswith("anywhere"):
            f.append(
                Finding(
                    "firewall.rule",
                    "high",
                    f"Port {port} ({RISKY_PORTS[port]}) is open to the internet",
                    line.strip(),
                    [
                        "sudo ufw status numbered",
                        "sudo ufw delete <rule-number>",
                        f"sudo ufw allow from <trusted-ip> to any port {port}",
                    ],
                )
            )
    if not re.search(r"^\s*(22|OpenSSH)\b.*\bLIMIT\b", text, re.M) and not src.fail2ban_status:
        f.append(
            Finding(
                "firewall.ssh_rate_limit",
                "low",
                "SSH is not rate limited (no ufw limit rule and no fail2ban)",
                "",
                ["sudo ufw limit OpenSSH"],
            )
        )
    return f


def check_fail2ban(src: Sources) -> list[Finding]:
    if src.fail2ban_status is None:
        return [
            Finding(
                "fail2ban",
                "medium",
                "fail2ban is not installed or not running",
                "It bans IPs that repeatedly fail SSH logins.",
                ["sudo apt install fail2ban", "sudo systemctl enable --now fail2ban"],
            )
        ]
    m = re.search(r"Jail list:\s*(.*)", src.fail2ban_status)
    jails = [j.strip() for j in m.group(1).split(",") if j.strip()] if m else []
    if "sshd" not in jails:
        return [
            Finding(
                "fail2ban.sshd",
                "medium",
                "fail2ban has no sshd jail",
                f"Active jails: {', '.join(jails) or 'none'}",
                [
                    "printf '[sshd]\\nenabled = true\\n' | sudo tee /etc/fail2ban/jail.d/sshd.local",
                    "sudo systemctl restart fail2ban",
                ],
            )
        ]
    return []


def check_unattended(src: Sources) -> list[Finding]:
    fix = [
        "sudo apt install unattended-upgrades",
        "sudo dpkg-reconfigure -plow unattended-upgrades",
    ]
    if src.auto_upgrades is None:
        return [
            Finding(
                "updates",
                "medium",
                "Automatic security updates are not configured",
                "/etc/apt/apt.conf.d/20auto-upgrades is missing.",
                fix,
            )
        ]
    vals = parse_apt_periodic(src.auto_upgrades)
    if vals.get("APT::Periodic::Unattended-Upgrade", "0") in ("0", ""):
        return [
            Finding(
                "updates",
                "medium",
                "Unattended upgrades are disabled",
                'APT::Periodic::Unattended-Upgrade is not "1".',
                fix,
            )
        ]
    if vals.get("APT::Periodic::Update-Package-Lists", "0") in ("0", ""):
        return [
            Finding(
                "updates",
                "low",
                "Package lists are not refreshed automatically",
                'APT::Periodic::Update-Package-Lists is not "1".',
                fix,
            )
        ]
    return []


def check_accounts(src: Sources) -> list[Finding]:
    f: list[Finding] = []
    if src.passwd is not None:
        for u in parse_passwd(src.passwd):
            if u["uid"] == "0" and u["name"] != "root":
                f.append(
                    Finding(
                        "accounts.uid0",
                        "high",
                        f"Account '{u['name']}' has UID 0 (root privileges)",
                        "",
                        [f"sudo usermod -u <new-uid> {u['name']}  # or remove the account"],
                    )
                )
    if src.group is not None:
        groups = parse_group(src.group)
        admins = sorted(set(groups.get("sudo", []) + groups.get("admin", [])))
        f.append(
            Finding(
                "accounts.sudo",
                "info",
                f"{len(admins)} user(s) in sudo/admin groups: {', '.join(admins) or 'none'}",
                "Confirm each one still needs admin access. Also review /etc/sudoers.d/.",
                ["sudo gpasswd -d <user> sudo  # remove one that does not"],
            )
        )
    return f


def check_ports(src: Sources) -> list[Finding]:
    if src.ss_listen is None:
        return [Finding("ports", "info", "Listening ports not checked", "Could not run `ss -tulpnH`.")]
    f: list[Finding] = []
    public = []
    seen = set()
    for row in parse_ss(src.ss_listen):
        if not is_public_address(row["address"]):
            continue
        key = (row["proto"], row["port"])
        if key in seen:
            continue
        seen.add(key)
        label = f"{row['proto']}/{row['port']}" + (f" ({row['process']})" if row["process"] else "")
        public.append(label)
        if row["port"] in RISKY_PORTS:
            f.append(
                Finding(
                    "ports.risky",
                    "high",
                    f"{RISKY_PORTS[row['port']]} listens on all interfaces: {label}",
                    "Bind it to 127.0.0.1 or a private interface. Docker-published ports bypass UFW.",
                    [
                        "Set bind-address/listen to 127.0.0.1 in the service config",
                        "Docker: publish as 127.0.0.1:PORT:PORT",
                    ],
                )
            )
    f.append(
        Finding(
            "ports.public",
            "info",
            f"{len(public)} service(s) listen on public interfaces",
            ", ".join(public) or "none",
        )
    )
    return f


def check_reboot(src: Sources) -> list[Finding]:
    if src.reboot_required:
        return [
            Finding(
                "updates.reboot",
                "low",
                "A reboot is required to finish applying updates",
                "Kernel or libc updates are not active until reboot.",
                ["cat /var/run/reboot-required.pkgs", "sudo reboot  # in a maintenance window"],
            )
        ]
    return []


CHECKS = [
    check_ssh,
    check_firewall,
    check_fail2ban,
    check_unattended,
    check_accounts,
    check_ports,
    check_reboot,
]


def audit(src: Sources) -> list[Finding]:
    findings: list[Finding] = []
    for check in CHECKS:
        findings.extend(check(src))
    order = {s: i for i, s in enumerate(reversed(SEVERITIES))}
    findings.sort(key=lambda x: order[x.severity])
    return findings


# --------------------------------------------------------------------------- collection


def _run(cmd: list[str]) -> str | None:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    return res.stdout


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def collect_live() -> Sources:
    src = Sources()
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        src.notes.append("Not running as root: sshd -T, ufw and fail2ban results may be missing.")
    src.sshd_t = _run(["sshd", "-T"]) or _run(["/usr/sbin/sshd", "-T"])
    src.ufw_status = _run(["ufw", "status", "verbose"])
    src.fail2ban_status = _run(["fail2ban-client", "status"])
    src.auto_upgrades = _read("/etc/apt/apt.conf.d/20auto-upgrades")
    src.group = _read("/etc/group")
    src.passwd = _read("/etc/passwd")
    src.ss_listen = _run(["ss", "-tulpnH"])
    src.reboot_required = _read("/var/run/reboot-required")
    return src


def collect_from_dir(directory: Path) -> Sources:
    def read(name: str) -> str | None:
        p = directory / name
        if not p.is_file():
            return None
        text = p.read_text(encoding="utf-8", errors="replace")
        return text if text.strip() else None

    return Sources(
        sshd_t=read("sshd_T.txt"),
        ufw_status=read("ufw_status.txt"),
        fail2ban_status=read("fail2ban_status.txt"),
        auto_upgrades=read("auto_upgrades.txt"),
        group=read("group.txt"),
        passwd=read("passwd.txt"),
        ss_listen=read("ss_listen.txt"),
        reboot_required=read("reboot_required.txt"),
    )


# --------------------------------------------------------------------------- output


def render_text(findings: list[Finding], notes: list[str]) -> str:
    lines = ["Server hardening audit (read-only)", ""]
    for n in notes:
        lines.append(f"NOTE: {n}")
    if notes:
        lines.append("")
    for x in findings:
        lines.append(f"[{x.severity.upper():6}] {x.title}  ({x.check})")
        if x.detail:
            lines.append(f"         {x.detail}")
        for cmd in x.fix:
            lines.append(f"         fix: {cmd}")
    counts = {s: sum(1 for x in findings if x.severity == s) for s in SEVERITIES}
    lines.append("")
    lines.append(
        "Summary: " + ", ".join(f"{counts[s]} {s}" for s in reversed(SEVERITIES)) + ". Nothing was changed."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Read-only hardening audit for Ubuntu/Debian servers. Changes nothing.",
        epilog="Exit codes: 0 no findings at/above --fail-on, 1 findings, 2 error.",
    )
    p.add_argument(
        "--from-dir",
        type=Path,
        help="audit captured outputs in this directory instead of the local machine",
    )
    p.add_argument("--json", action="store_true", help="print JSON instead of text")
    p.add_argument(
        "--fail-on",
        choices=SEVERITIES[1:] + ["none"],
        default="medium",
        help="lowest severity that makes the exit code 1 (default: medium)",
    )
    p.add_argument(
        "--print-collect-commands",
        action="store_true",
        help="print the commands that produce the --from-dir files and exit",
    )
    args = p.parse_args(argv)

    if args.print_collect_commands:
        print("# Run on the server, then copy the directory back and use --from-dir")
        print("mkdir -p audit && cd audit")
        for name, cmd in CAPTURE_FILES.items():
            print(f"{cmd} > {name}")
        return 0

    if args.from_dir:
        if not args.from_dir.is_dir():
            print(f"error: {args.from_dir} is not a directory", file=sys.stderr)
            return 2
        src = collect_from_dir(args.from_dir)
    else:
        if sys.platform != "linux":
            print("error: live mode needs Linux; use --from-dir", file=sys.stderr)
            return 2
        src = collect_live()

    findings = audit(src)
    if args.json:
        print(json.dumps({"findings": [asdict(x) for x in findings], "notes": src.notes}, indent=2))
    else:
        print(render_text(findings, src.notes))

    if args.fail_on == "none":
        return 0
    threshold = SEVERITIES.index(args.fail_on)
    return 1 if any(SEVERITIES.index(x.severity) >= threshold for x in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
