import json

import pytest

from conftest import FIXTURES, load_script, run_script

SKILL = "docker-compose-review"
SCRIPT = "compose_review.py"
mod = load_script(SKILL, SCRIPT)
F = FIXTURES / SKILL


def by_service(findings):
    out = {}
    for f in findings:
        out.setdefault(f.service, set()).add(f.check)
    return out


def test_bad_compose_json():
    data = json.loads((F / "bad-compose.json").read_text())
    s = by_service(mod.review(data))
    assert {"image.tag", "ports.public", "volumes.docker_sock"} <= s["web"]
    assert {"image.tag", "ports.sensitive", "secrets.environment"} <= s["db"]
    assert "ports.sensitive" in s["redis"]  # long port syntax
    assert {
        "privileged",
        "network_mode",
        "cap_add",
        "secrets.environment",
        "volumes.host_root",
    } <= s["agent"]
    assert "version" in s["-"]


def test_secret_values_are_never_printed():
    r = run_script(SKILL, SCRIPT, str(F / "bad-compose.json"))
    assert "example-not-a-real-password" not in r.stdout
    assert "placeholder-token-value" not in r.stdout
    assert "POSTGRES_PASSWORD" in r.stdout


def test_good_compose_yaml_is_clean():
    pytest.importorskip("yaml")
    data = mod.load_compose(str(F / "good-compose.yml"))
    assert mod.review(data) == []


def test_yaml_and_json_give_same_result():
    pytest.importorskip("yaml")
    a = mod.review(mod.load_compose(str(F / "bad-compose.yml")))
    b = mod.review(mod.load_compose(str(F / "bad-compose.json")))
    assert sorted((x.service, x.check) for x in a) == sorted((x.service, x.check) for x in b)


@pytest.mark.parametrize(
    "entry,expected",
    [
        ("8080", {"host_ip": "", "published": "", "target": "8080", "protocol": "tcp"}),
        ("8080:80", {"host_ip": "", "published": "8080", "target": "80", "protocol": "tcp"}),
        (
            "127.0.0.1:8080:80/udp",
            {"host_ip": "127.0.0.1", "published": "8080", "target": "80", "protocol": "udp"},
        ),
        ("[::1]:5432:5432", {"host_ip": "::1", "published": "5432", "target": "5432", "protocol": "tcp"}),
        (
            {"target": 80, "published": "8080", "host_ip": "0.0.0.0"},
            {"host_ip": "0.0.0.0", "published": "8080", "target": "80", "protocol": "tcp"},
        ),
    ],
)
def test_parse_port(entry, expected):
    assert mod.parse_port(entry) == expected


@pytest.mark.parametrize(
    "value,literal",
    [
        ("${DB_PASSWORD}", False),
        ("$DB_PASSWORD", False),
        ("${DB_PASSWORD:-}", False),
        ("${DB_PASSWORD:?required}", False),
        ("/run/secrets/db", False),
        ("", False),
        (None, False),
        ("hunter2-but-fake", True),
    ],
)
def test_is_literal_secret(value, literal):
    assert mod.is_literal_secret(value) is literal


@pytest.mark.parametrize(
    "image,pinned",
    [
        ("nginx", False),
        ("nginx:latest", False),
        ("registry.example.com:5000/app", False),
        ("registry.example.com:5000/app:1.2.3", True),
        ("postgres:16.4-alpine", True),
        ("caddy@sha256:" + "a" * 64, True),
        ("app:${TAG}", True),
    ],
)
def test_image_pinning(image, pinned):
    assert (mod.image_is_unpinned(image) is None) is pinned


def test_localhost_ports_and_web_ports_are_fine():
    svc = {
        "image": "x:1",
        "restart": "always",
        "healthcheck": {"test": ["CMD", "true"]},
        "mem_limit": "128m",
        "logging": {"driver": "local"},
        "ports": ["127.0.0.1:5432:5432", "80:80", "443:443"],
    }
    assert mod.review({"services": {"x": svc}}) == []


def test_cli_exit_codes(tmp_path):
    assert run_script(SKILL, SCRIPT, str(F / "bad-compose.json")).returncode == 1
    assert run_script(SKILL, SCRIPT, str(F / "bad-compose.json"), "--fail-on", "none").returncode == 0
    r = run_script(SKILL, SCRIPT, "-", "--json", stdin=(F / "bad-compose.json").read_text())
    assert r.returncode == 1 and json.loads(r.stdout)["findings"]
    assert run_script(SKILL, SCRIPT, "--help").returncode == 0
    assert run_script(SKILL, SCRIPT, str(tmp_path / "missing.yml")).returncode == 2
    empty = tmp_path / "c.json"
    empty.write_text("{}")
    assert run_script(SKILL, SCRIPT, str(empty)).returncode == 1
