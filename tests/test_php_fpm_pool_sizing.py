import json

import pytest

from conftest import FIXTURES, load_script, run_script

SKILL = "php-fpm-pool-sizing"
SCRIPT = "fpm_sizing.py"
mod = load_script(SKILL, SCRIPT)
F = FIXTURES / SKILL


def test_parse_rss_skips_header_blank_and_zero():
    assert mod.parse_rss("RSS\n 102400\n\n0\n51200 php-fpm: pool www\n") == [100.0, 50.0]
    with pytest.raises(mod.SizingError):
        mod.parse_rss("RSS\n")
    with pytest.raises(mod.SizingError):
        mod.parse_rss("12MB\n")


def test_percentile_nearest_rank():
    values = [float(v) for v in range(1, 11)]
    assert mod.percentile(values, 90) == 9
    assert mod.percentile(values, 100) == 10
    assert mod.percentile([5.0], 90) == 5


def test_size_pool_dynamic():
    result = mod.size_pool(4096, 1024, 60, cpus=2)
    s = result["settings"]
    assert s["pm.max_children"] == 51  # floor(3072 / 60)
    assert (s["pm.min_spare_servers"], s["pm.start_servers"], s["pm.max_spare_servers"]) == (4, 6, 8)
    assert s["pm.max_requests"] == 500
    assert result["worst_case_mb"] <= result["budget_mb"]


def test_size_pool_small_server_clamps_spares_and_splits_pools():
    s = mod.size_pool(1024, 512, 100, pools=2, cpus=8)["settings"]
    assert s["pm.max_children"] == 2
    assert s["pm.min_spare_servers"] <= s["pm.start_servers"] <= s["pm.max_spare_servers"] <= 2


def test_size_pool_modes():
    assert "pm.start_servers" not in mod.size_pool(2048, 512, 50, pm="static")["settings"]
    assert mod.size_pool(2048, 512, 50, pm="ondemand")["settings"]["pm.process_idle_timeout"] == "10s"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ((1024, 2048, 50), "leaves no memory"),
        ((1024, 512, 600), "one child needs"),
        ((1024, -1, 50), "must not be negative"),
        ((0, 0, 50), "must be positive"),
    ],
)
def test_size_pool_errors(args, message):
    with pytest.raises(mod.SizingError, match=message):
        mod.size_pool(*args)


def test_parse_pool_conf_and_check_current():
    current = mod.parse_pool_conf((F / "www.conf").read_text(encoding="utf-8"))
    assert current["pm.max_children"] == "50" and "pm.max_requests" not in current
    findings = mod.check_current(current, 80, 2000)
    assert any("can use 4000 MiB" in f for f in findings)
    assert any("pm.max_requests" in f for f in findings)
    assert mod.check_current({"pm.max_children": "20", "pm.max_requests": "500"}, 80, 2000) == []
    assert "no pm.max_children" in mod.check_current({}, 80, 2000)[0]


def test_meminfo(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:        4025360 kB\nMemFree: 1 kB\n", encoding="utf-8")
    assert round(mod.meminfo_total_mb(str(meminfo))) == 3931
    with pytest.raises(mod.SizingError):
        mod.meminfo_total_mb(str(tmp_path / "missing"))


def test_cli_measured_overcommit_exit_1():
    res = run_script(
        SKILL,
        SCRIPT,
        "--rss-file",
        str(F / "rss.txt"),
        "--ram-mb",
        "4096",
        "--reserve-mb",
        "1536",
        "--pool-conf",
        str(F / "www.conf"),
        "--json",
    )
    assert res.returncode == 1, res.stderr
    data = json.loads(res.stdout)
    assert data["measured"]["samples"] == 24
    assert data["child_mb"] >= data["measured"]["mean_mb"]
    assert data["current"]["pm.max_children"] == "50"
    assert any(f.startswith("current pm.max_children") for f in data["findings"])


def test_cli_text_and_stdin():
    res = run_script(
        SKILL, SCRIPT, "--rss-file", "-", "--ram-mb", "2048", "--reserve-mb", "512", stdin="51200\n"
    )
    assert res.returncode == 0, res.stderr
    assert "pm.max_children = 30" in res.stdout
    assert "Measured 1 children" in res.stdout


def test_cli_errors_and_help():
    assert run_script(SKILL, SCRIPT, "--help").returncode == 0
    assert (
        run_script(SKILL, SCRIPT, "--child-mb", "60", "--ram-mb", "512", "--reserve-mb", "600").returncode
        == 2
    )
    assert (
        run_script(SKILL, SCRIPT, "--child-mb", "60", "--ram-mb", "1024", "--percentile", "10").returncode
        == 2
    )
    assert run_script(SKILL, SCRIPT, "--ram-mb", "1024").returncode == 2  # no child source
