import json

from conftest import FIXTURES, load_script, run_script

SKILL = "incident-triage"
SCRIPT = "triage.py"
mod = load_script(SKILL, SCRIPT)
SICK = FIXTURES / SKILL / "sick"
HEALTHY = FIXTURES / SKILL / "healthy"


def sick_args():
    return [
        "--root",
        str(SICK),
        "--df-file",
        str(SICK / "df.txt"),
        "--df-inodes-file",
        str(SICK / "df-inodes.txt"),
        "--kernel-log-file",
        str(SICK / "kernel.log"),
        "--failed-units-file",
        str(SICK / "failed.txt"),
        "--ps-file",
        str(SICK / "ps.txt"),
    ]


def test_sick_server_json():
    r = run_script(SKILL, SCRIPT, *sick_args(), "--json")
    assert r.returncode == 1
    findings = json.loads(r.stdout)["findings"]
    titles = [f["title"] for f in findings]
    assert any(t.startswith("Load 9.80") and "2 CPU" in t for t in titles)
    assert any("memory available" in t for t in titles)
    assert any("Swap 90% used" in t for t in titles)
    assert any(t.startswith("/ is 97% full") for t in titles)
    assert any("/var/lib/docker is 88% full" in t for t in titles)
    assert any("inodes 92%" in t for t in titles)
    assert any("mysql.service" in t and "certbot.service" in t for t in titles)
    assert not any("/run" in t for t in titles)  # tmpfs skipped
    assert findings[0]["severity"] == "high"


def test_oom_kills_counted_once_per_pid():
    victims = mod.parse_oom((SICK / "kernel.log").read_text())
    assert victims == {"mysqld": 1, "php-fpm8.3": 1}


def test_healthy_server():
    r = run_script(SKILL, SCRIPT, "--root", str(HEALTHY), "--df-file", str(HEALTHY / "df.txt"))
    assert r.returncode == 0
    assert "No resource problem detected" in r.stdout


def test_parsers():
    assert mod.count_cpus("processor : 0\nprocessor : 1\nprocessor : 2\n") == 3
    assert mod.count_cpus(None) == 1
    mem = mod.parse_meminfo("MemTotal: 100 kB\nMemAvailable: 7 kB\n")
    assert mem == {"MemTotal": 100, "MemAvailable": 7}
    rows = mod.parse_df("Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/vda1 10 9 1 90% /\n")
    assert rows == [{"fs": "/dev/vda1", "type": "", "use_pct": 90, "mount": "/"}]


def test_low_ram_without_swap_is_info():
    snap = mod.Snapshot(meminfo="MemTotal: 2000000 kB\nMemAvailable: 1500000 kB\nSwapTotal: 0 kB\n")
    findings = mod.analyse(snap)
    assert [f.severity for f in findings] == ["info"]


def test_cli_help_and_non_linux_guard():
    assert run_script(SKILL, SCRIPT, "--help").returncode == 0
