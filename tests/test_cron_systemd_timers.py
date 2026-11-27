import json

import pytest

from conftest import FIXTURES, load_script, run_script

SKILL = "cron-systemd-timers"
SCRIPT = "cron_to_timer.py"
mod = load_script(SKILL, SCRIPT)
F = FIXTURES / SKILL


@pytest.mark.parametrize(
    "cron,expected",
    [
        ("* * * * *", ["*-*-* *:*:00"]),
        ("*/15 * * * *", ["*-*-* *:00/15:00"]),
        ("0 */6 * * *", ["*-*-* 00/6:00:00"]),
        ("30 2 * * 1-5", ["Mon..Fri *-*-* 02:30:00"]),
        ("0 9 * * mon,wed,fri", ["Mon,Wed,Fri *-*-* 09:00:00"]),
        ("0 0 * * 0", ["Sun *-*-* 00:00:00"]),
        ("0 0 * * 7", ["Sun *-*-* 00:00:00"]),
        ("0 0 * * 6,0", ["Sat,Sun *-*-* 00:00:00"]),
        ("15 3 1 * *", ["*-*-01 03:15:00"]),
        ("0 0 1 1 *", ["*-01-01 00:00:00"]),
        ("0 3 * jan,jul *", ["*-01,07-* 03:00:00"]),
        ("5 4 */2 * *", ["*-*-01/2 04:05:00"]),
        ("0 8-10 * * *", ["*-*-* 08,09,10:00:00"]),
        ("10-40/10 * * * *", ["*-*-* *:10,20,30,40:00"]),
        ("0 4 1,15 * 0", ["*-*-01,15 04:00:00", "Sun *-*-* 04:00:00"]),
        ("0 4 */2 * 1", ["Mon *-*-01/2 04:00:00"]),  # '*' field => AND, like cronie
        ("@daily", ["*-*-* 00:00:00"]),
        ("@hourly", ["*-*-* *:00:00"]),
        ("@weekly", ["Sun *-*-* 00:00:00"]),
        ("@monthly", ["*-*-01 00:00:00"]),
        ("@yearly", ["*-01-01 00:00:00"]),
    ],
)
def test_on_calendar(cron, expected):
    assert mod.to_on_calendar(cron)[0] == expected


def test_reboot():
    cal, boot, warnings = mod.to_on_calendar("@reboot")
    assert cal == [] and boot and warnings


@pytest.mark.parametrize(
    "bad",
    [
        "61 * * * *",
        "* 24 * * *",
        "* * 0 * *",
        "* * * 13 *",
        "* * * * 8",
        "*/0 * * * *",
        "5-1 * * * *",
        "* * * *",
        "@sometimes",
        "x * * * *",
        "1,,2 * * * *",
    ],
)
def test_invalid(bad):
    with pytest.raises(mod.CronError):
        mod.to_on_calendar(bad)


def test_command_handling():
    exec_cmd, w = mod.cron_command_to_exec("date +\\%F >> /var/log/stamp.log")
    assert exec_cmd == '/bin/sh -c "date +%%F >> /var/log/stamp.log"'
    exec_cmd, w = mod.cron_command_to_exec("echo hi % stdin text")
    assert "stdin" not in exec_cmd and any("unescaped %" in x for x in w)
    exec_cmd, _ = mod.cron_command_to_exec("cd $HOME && ./run")
    assert "$$HOME" in exec_cmd
    exec_cmd, w = mod.cron_command_to_exec("/usr/local/bin/job --flag=1")
    assert exec_cmd == "/usr/local/bin/job --flag=1" and w == []
    _, w = mod.cron_command_to_exec("job")
    assert any("absolute path" in x for x in w)


def test_user_crontab_file():
    r = run_script(SKILL, SCRIPT, "--file", str(F / "user.crontab"), "--json")
    assert r.returncode == 0, r.stderr
    units = {u["name"]: u for u in json.loads(r.stdout)["units"]}
    assert set(units) == {"sync-feeds", "report", "rotate", "cleanup", "warm-cache", "date"}
    assert 'Environment="PATH=/usr/local/bin:/usr/bin:/bin"' in units["sync-feeds"]["service"]
    assert "MAILTO" not in units["sync-feeds"]["service"]
    assert len(units["rotate"]["on_calendar"]) == 2
    assert "OnBootSec=1min" in units["warm-cache"]["timer"]
    assert "WantedBy=timers.target" in units["report"]["timer"]


def test_system_crontab_user_column_and_errors():
    r = run_script(SKILL, SCRIPT, "--file", str(F / "cron.d-example"), "--system", "--json")
    assert r.returncode == 1  # the '61' line is reported
    data = json.loads(r.stdout)
    units = {u["name"]: u for u in data["units"]}
    assert "User=www-data" in units["artisan"]["service"]
    assert "User=" not in units["logrotate"]["service"]  # root needs no User=
    assert units["logrotate"]["on_calendar"] == ["Sun *-01,07-* 03:00:00"]
    assert any("61" in e for e in data["errors"])


def test_options_and_out_dir(tmp_path):
    r = run_script(
        SKILL,
        SCRIPT,
        "--line",
        "0 2 * * * /usr/local/bin/backup",
        "--name",
        "nightly-backup",
        "--persistent",
        "--random-delay",
        "10min",
        "--user",
        "backup",
        "--on-failure",
        "notify@%n.service",
        "--out",
        str(tmp_path),
    )
    assert r.returncode == 0, r.stderr
    timer = (tmp_path / "nightly-backup.timer").read_text()
    service = (tmp_path / "nightly-backup.service").read_text()
    assert "Persistent=true" in timer and "RandomizedDelaySec=10min" in timer
    assert "User=backup" in service and "OnFailure=notify@%n.service" in service
    assert "systemd-analyze calendar" in r.stdout


def test_duplicate_names_get_suffix():
    text = "0 1 * * * /usr/local/bin/job a\n0 2 * * * /usr/local/bin/job b\n"
    r = run_script(SKILL, SCRIPT, "--file", "-", "--json", stdin=text)
    names = [u["name"] for u in json.loads(r.stdout)["units"]]
    assert names == ["job", "job-2"]


def test_cli_errors(tmp_path):
    assert run_script(SKILL, SCRIPT, "--help").returncode == 0
    assert run_script(SKILL, SCRIPT, "--file", str(tmp_path / "missing")).returncode == 2
    empty = tmp_path / "empty"
    empty.write_text("# nothing\n")
    assert run_script(SKILL, SCRIPT, "--file", str(empty)).returncode == 2


def convert(*args, stdin=None):
    res = run_script(SKILL, SCRIPT, *args, "--json", stdin=stdin)
    return res, json.loads(res.stdout) if res.stdout.strip() else None


def test_cron_tz_becomes_on_calendar_suffix():
    crontab = "CRON_TZ=Asia/Dhaka\n0 2 * * * /usr/local/bin/backup\n0 4 1,15 * 0 /usr/local/bin/report\n"
    res, data = convert("--file", "-", stdin=crontab)
    assert res.returncode == 0, res.stderr
    backup, report = data["units"]
    assert backup["on_calendar"] == ["*-*-* 02:00:00 Asia/Dhaka"]
    assert report["on_calendar"] == ["*-*-01,15 04:00:00 Asia/Dhaka", "Sun *-*-* 04:00:00 Asia/Dhaka"]
    assert "OnCalendar=*-*-* 02:00:00 Asia/Dhaka" in backup["timer"]
    assert "CRON_TZ" not in backup["service"]
    assert any("systemd 235" in w for w in backup["warnings"])


def test_cron_tz_applies_only_to_following_lines():
    crontab = "0 1 * * * /usr/local/bin/a\nCRON_TZ=UTC\n0 1 * * * /usr/local/bin/b\n"
    _, data = convert("--file", "-", stdin=crontab)
    assert [u["on_calendar"] for u in data["units"]] == [["*-*-* 01:00:00"], ["*-*-* 01:00:00 UTC"]]


def test_timezone_option_overrides_cron_tz():
    _, data = convert(
        "--file", "-", "--timezone", "Europe/London", stdin="CRON_TZ=Asia/Dhaka\n@daily /usr/local/bin/x\n"
    )
    unit = data["units"][0]
    assert unit["on_calendar"] == ["*-*-* 00:00:00 Europe/London"]
    assert any("overrides CRON_TZ=Asia/Dhaka" in w for w in unit["warnings"])


def test_timezone_with_reboot_has_no_calendar():
    _, data = convert("--line", "@reboot /usr/local/bin/x", "--timezone", "UTC")
    unit = data["units"][0]
    assert unit["on_calendar"] == [] and "OnBootSec=1min" in unit["timer"]
    assert not any("235" in w for w in unit["warnings"])


@pytest.mark.parametrize("tz", ["Asia/Dhaka", "America/Argentina/Buenos_Aires", "UTC", "Etc/GMT+6"])
def test_valid_time_zones(tz):
    res, data = convert("--line", "0 3 * * * /usr/local/bin/x", "--timezone", tz)
    assert res.returncode == 0 and data["units"][0]["on_calendar"] == [f"*-*-* 03:00:00 {tz}"]


@pytest.mark.parametrize("tz", ["Asia Dhaka", "+06:00", "Asia/Dhaka;rm"])
def test_invalid_time_zone_is_an_error(tz):
    res, data = convert("--line", "0 3 * * * /usr/local/bin/x", "--timezone", tz)
    assert res.returncode == 1
    assert data["units"] == [] and "time zone" in data["errors"][0]
