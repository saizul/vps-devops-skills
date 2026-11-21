"""Certificates are generated at test time with the openssl CLI (no keys in the repo)."""

import json
import shutil
import socket
import ssl
import subprocess
import threading

import pytest

from conftest import load_script, run_script

SKILL = "ssl-tls-check"
SCRIPT = "tls_check.py"
mod = load_script(SKILL, SCRIPT)

needs_openssl = pytest.mark.skipif(not shutil.which("openssl"), reason="openssl CLI not installed")


def make_cert(tmp_path, days, cn="localhost", sans="DNS:localhost,DNS:www.localhost", name="cert"):
    key, crt = tmp_path / f"{name}.key", tmp_path / f"{name}.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(crt),
            "-days",
            str(days),
            "-subj",
            f"/CN={cn}",
            "-addext",
            f"subjectAltName={sans}",
        ],
        check=True,
        capture_output=True,
    )
    return crt, key


@pytest.fixture
def tls_server(tmp_path):
    crt, key = make_cert(tmp_path, 90)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(crt), str(key))
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    srv.settimeout(0.2)
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except (TimeoutError, OSError):
                continue
            try:
                conn.settimeout(5)
                ctx.wrap_socket(conn, server_side=True).close()
            except (OSError, ssl.SSLError):
                conn.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    yield srv.getsockname()[1], crt
    stop.set()
    t.join(2)
    srv.close()


@needs_openssl
def test_cert_file_expiring_and_wrong_hostname(tmp_path):
    crt, _ = make_cert(tmp_path, 5)
    r = run_script(SKILL, SCRIPT, "--cert-file", str(crt), "--hostname", "example.com", "--json")
    assert r.returncode == 1
    res = json.loads(r.stdout)[0]
    assert res["sans"] == ["localhost", "www.localhost"]
    assert 4 < res["days_left"] <= 5
    messages = " ".join(f["message"] for f in res["findings"])
    assert "expires in" in messages and "not in the certificate SANs" in messages


@needs_openssl
def test_cert_file_healthy_and_wildcard(tmp_path):
    crt, _ = make_cert(tmp_path, 60, cn="example.test", sans="DNS:*.example.test,DNS:example.test")
    r = run_script(SKILL, SCRIPT, "--cert-file", str(crt), "--hostname", "www.example.test")
    assert r.returncode == 0, r.stdout
    assert "[OK  ]" in r.stdout


@needs_openssl
def test_cert_file_warn_threshold(tmp_path):
    crt, _ = make_cert(tmp_path, 15)
    res = json.loads(run_script(SKILL, SCRIPT, "--cert-file", str(crt), "--json").stdout)[0]
    assert [f["severity"] for f in res["findings"]] == ["medium"]
    assert run_script(SKILL, SCRIPT, "--cert-file", str(crt), "--warn-days", "10").returncode == 0


@needs_openssl
def test_chain_file_reports_each_cert(tmp_path):
    a, _ = make_cert(tmp_path, 30, name="a")
    b, _ = make_cert(tmp_path, 400, cn="Fake Root", sans="DNS:root.invalid", name="b")
    chain = tmp_path / "chain.pem"
    chain.write_text(a.read_text() + b.read_text())
    res = json.loads(run_script(SKILL, SCRIPT, "--cert-file", str(chain), "--json").stdout)
    assert len(res) == 2 and res[0]["target"].endswith("(leaf)")


@needs_openssl
def test_endpoint_verified_with_ca_file(tls_server):
    port, crt = tls_server
    r = run_script(SKILL, SCRIPT, f"localhost:{port}", "--ca-file", str(crt), "--json")
    assert r.returncode == 0, r.stdout
    res = json.loads(r.stdout)[0]
    assert res["protocol"] in ("TLSv1.2", "TLSv1.3")
    assert 89 < res["days_left"] <= 90


@needs_openssl
def test_endpoint_untrusted_cert_still_reports_expiry(tls_server):
    port, _ = tls_server
    r = run_script(SKILL, SCRIPT, f"127.0.0.1:{port}", "--json")
    assert r.returncode == 1
    res = json.loads(r.stdout)[0]
    assert "verification failed" in res["findings"][0]["message"]
    assert res["not_after"] and res["days_left"] > 80


def test_connection_refused_is_a_finding():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    r = run_script(SKILL, SCRIPT, f"127.0.0.1:{port}", "--timeout", "2")
    assert r.returncode == 1 and "TLS connection failed" in r.stdout


@pytest.mark.parametrize(
    "target,expected",
    [
        ("example.com", ("example.com", 443)),
        ("https://example.com/path", ("example.com", 443)),
        ("example.com:8443", ("example.com", 8443)),
        ("[2001:db8::1]:993", ("2001:db8::1", 993)),
    ],
)
def test_parse_target(target, expected):
    assert mod.parse_target(target, 443) == expected


def test_hostname_matches():
    assert mod.hostname_matches("www.example.com", "*.example.com")
    assert not mod.hostname_matches("a.b.example.com", "*.example.com")
    assert not mod.hostname_matches("example.com", "*.example.com")
    assert mod.hostname_matches("Example.COM.", "example.com")


def test_days_until():
    now = ssl.cert_time_to_seconds("Jan  1 00:00:00 2030 GMT")
    assert mod.days_until("Jan 11 00:00:00 2030 GMT", now) == 10


def test_cli_usage_errors(tmp_path):
    assert run_script(SKILL, SCRIPT).returncode == 2
    assert run_script(SKILL, SCRIPT, "--help").returncode == 0
    empty = tmp_path / "empty.pem"
    empty.write_text("not a cert")
    if shutil.which("openssl"):
        assert run_script(SKILL, SCRIPT, "--cert-file", str(empty)).returncode == 2
