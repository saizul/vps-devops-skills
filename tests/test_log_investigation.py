import json

import pytest

from conftest import FIXTURES, load_script, run_script

SKILL = "log-investigation"
SCRIPT = "access_log_summary.py"
mod = load_script(SKILL, SCRIPT)
LOG = FIXTURES / SKILL / "access.log"
GZ = FIXTURES / SKILL / "access.log.2.gz"


def summary(*args):
    r = run_script(SKILL, SCRIPT, *args, "--json")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_full_summary_with_gzip():
    s = summary(str(LOG), str(GZ))
    assert s["total"] == 41 and s["unparsed_lines"] == 1
    assert s["status_classes"] == {"2xx": 28, "4xx": 6, "5xx": 7}
    assert s["top_ips"][0] == ["203.0.113.10", 21]
    assert s["top_5xx_paths"][0] == ["/api/checkout", 6]
    assert s["probe_ips"] == [["192.0.2.66", 5]]
    assert s["slowest_paths"][0]["path"] == "/api/checkout"
    assert s["busiest_minutes"][0] == ["2026-11-21 10:00", 20]
    assert s["first"].startswith("2026-11-20T23:59")


def test_query_strings_grouped_unless_keep_query():
    assert ["/", 20] in summary(str(LOG))["top_paths"]
    assert ["/?utm=1", 20] in summary(str(LOG), "--keep-query")["top_paths"]


def test_filters():
    assert summary(str(LOG), "--status", "5xx")["total"] == 7
    assert summary(str(LOG), "--status", "404")["total"] == 6
    assert summary(str(LOG), "--last", "30m")["total"] == 2
    assert summary(str(LOG), "--ip", "192.0.2.66")["total"] == 5
    assert summary(str(LOG), "--path-prefix", "/api/")["total"] == 11
    s = summary(str(LOG), "--since", "2026-11-21T10:01:00+00:00", "--until", "2026-11-21T10:01:59+00:00")
    assert s["total"] == 10


def test_stdin_and_text_output():
    r = run_script(SKILL, SCRIPT, "-", stdin=LOG.read_text())
    assert r.returncode == 0
    assert "Paths returning 5xx" in r.stdout and "/api/checkout" in r.stdout


def test_max_5xx_pct_gate():
    assert run_script(SKILL, SCRIPT, str(LOG), "--max-5xx-pct", "5").returncode == 1
    assert run_script(SKILL, SCRIPT, str(LOG), "--max-5xx-pct", "50").returncode == 0


def test_parse_line_variants():
    e = mod.parse_line(
        '::1 - bob [21/Nov/2026:10:00:00 +0600] "GET /x?a=1 HTTP/2.0" 304 - "-" "ua \\"q\\"" 0.004 0.003'
    )
    assert e["ip"] == "::1" and e["status"] == 304 and e["bytes"] == 0
    assert e["request_time"] == 0.003
    common = mod.parse_line('1.2.3.4 - - [21/Nov/2026:10:00:00 +0000] "GET / HTTP/1.1" 200 12')
    assert common["ua"] == "-"
    bad_req = mod.parse_line('1.2.3.4 - - [21/Nov/2026:10:00:00 +0000] "\\x16\\x03\\x01" 400 0 "-" "-"')
    assert bad_req["status"] == 400
    assert mod.parse_line("garbage") is None


def test_errors_and_help(tmp_path):
    assert run_script(SKILL, SCRIPT, str(tmp_path / "nope.log")).returncode == 2
    assert run_script(SKILL, SCRIPT, "--help").returncode == 0
    assert run_script(SKILL, SCRIPT, str(LOG), "--last", "5x").returncode == 2


@pytest.mark.parametrize("text,seconds", [("15m", 900), ("2h", 7200), ("1d", 86400), ("30s", 30)])
def test_parse_duration(text, seconds):
    assert mod.parse_duration(text).total_seconds() == seconds
