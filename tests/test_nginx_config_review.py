import json

from conftest import FIXTURES, load_script, run_script

SKILL = "nginx-config-review"
SCRIPT = "nginx_lint.py"
mod = load_script(SKILL, SCRIPT)
F = FIXTURES / SKILL


def lint_text(text, tmp_path):
    p = tmp_path / "nginx.conf"
    p.write_text(text)
    return mod.lint(mod.Loader(None, "").load(str(p)))


def test_bad_config_findings():
    findings = mod.lint(mod.Loader(None, "").load(str(F / "bad" / "nginx.conf")))
    by_check = {f.check: f for f in findings}
    for check in (
        "tls.protocols",
        "alias_traversal",
        "open_proxy",
        "tls.ciphers",
        "headers.hsts",
        "autoindex",
        "headers.inheritance",
        "server_tokens",
        "hidden_files",
        "http_redirect",
    ):
        assert check in by_check, check
    assert by_check["tls.protocols"].severity == "high"
    assert "x-content-type-options" in by_check["headers.inheritance"].title
    # findings point at the included file and line
    assert by_check["alias_traversal"].file.endswith("sites-enabled/example.conf")
    assert by_check["alias_traversal"].line == 14


def test_good_config_is_clean():
    findings = mod.lint(mod.Loader(None, "").load(str(F / "good" / "nginx.conf")))
    assert findings == []


def test_dump_mode_matches_file_mode():
    files, main = mod.split_dump((F / "nginx-T-dump.txt").read_text())
    assert main == "/etc/nginx/nginx.conf"
    assert "/etc/nginx/sites-enabled/example.conf" in files
    tree = mod.Loader(files, "/etc/nginx").load(main)
    dump_checks = sorted(f.check for f in mod.lint(tree))
    disk_checks = sorted(f.check for f in mod.lint(mod.Loader(None, "").load(str(F / "bad" / "nginx.conf"))))
    assert dump_checks == disk_checks


def test_tokenizer_quotes_comments_and_variables(tmp_path):
    text = (
        "http { server_tokens off; # comment ; {\n"
        "  server { listen 443 ssl; server_name a.test;\n"
        "    add_header Content-Security-Policy \"frame-ancestors 'self'; default-src 'self'\" always;\n"
        '    location / { return 200 "${host} ok"; } } }\n'
    )
    tree = mod.parse(text, "x.conf")
    server = tree[0].find("server")[0]
    csp = server.find("add_header")[0]
    assert csp.args[1].startswith("frame-ancestors")
    findings = lint_text(text, tmp_path)
    assert "headers.frame" not in {f.check for f in findings}


def test_parse_errors(tmp_path):
    for bad in ("http { server { }", "http { listen 80 }", "}"):
        try:
            mod.parse(bad, "x.conf")
        except mod.ParseError:
            continue
        raise AssertionError(f"no ParseError for {bad!r}")


def test_proxy_pass_fixed_upstream_with_request_uri_is_not_open_proxy(tmp_path):
    text = (
        "http { server_tokens off; server { listen 80; location / { proxy_pass http://app$request_uri; } } }"
    )
    assert "open_proxy" not in {f.check for f in lint_text(text, tmp_path)}
    text2 = "http { server { listen 80; location / { proxy_pass http://$host; } } }"
    assert "open_proxy" in {f.check for f in lint_text(text2, tmp_path)}


def test_regex_location_alias_is_not_flagged(tmp_path):
    text = "http { server { listen 80; location ~ ^/img/(.+)$ { alias /data/img/$1; } } }"
    assert "alias_traversal" not in {f.check for f in lint_text(text, tmp_path)}


def test_cli(tmp_path):
    r = run_script(SKILL, SCRIPT, str(F / "bad" / "nginx.conf"), "--json")
    assert r.returncode == 1
    assert json.loads(r.stdout)["findings"]
    assert run_script(SKILL, SCRIPT, str(F / "good" / "nginx.conf")).returncode == 0
    assert run_script(SKILL, SCRIPT, "--dump", str(F / "nginx-T-dump.txt")).returncode == 1
    assert run_script(SKILL, SCRIPT, "--help").returncode == 0
    broken = tmp_path / "broken.conf"
    broken.write_text("http {")
    assert run_script(SKILL, SCRIPT, str(broken)).returncode == 2
    notdump = tmp_path / "notdump.txt"
    notdump.write_text("hello")
    assert run_script(SKILL, SCRIPT, "--dump", str(notdump)).returncode == 2
