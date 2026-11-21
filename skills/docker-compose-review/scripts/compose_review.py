#!/usr/bin/env python3
"""Review a Docker Compose file for production readiness and security.

Checks every service for: unpinned image tags, missing healthcheck, restart
policy and memory limit, log rotation, ports published on all interfaces
(Docker bypasses UFW), literal secrets in environment, docker.sock and host
root mounts, privileged mode, dangerous capabilities and host networking.

Input: a compose file. JSON works with the standard library; YAML needs PyYAML
(``apt install python3-yaml``). Without PyYAML, convert first:
``docker compose config --format json > compose.json``.
Secret values are never printed, only the variable names.

Exit codes: 0 = no findings at or above --fail-on, 1 = findings, 2 = error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass

SEVERITIES = ["info", "low", "medium", "high"]

SENSITIVE_PORTS = {
    "2375": "Docker API",
    "3306": "MySQL/MariaDB",
    "5432": "PostgreSQL",
    "6379": "Redis",
    "9200": "Elasticsearch",
    "11211": "Memcached",
    "27017": "MongoDB",
    "5672": "RabbitMQ",
    "15672": "RabbitMQ management",
    "8086": "InfluxDB",
    "9090": "Prometheus",
}
PUBLIC_OK_PORTS = {"80", "443", "443/udp"}
SECRET_KEY_RE = re.compile(
    r"(PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|PRIVATE_?KEY|ACCESS_?KEY|CREDENTIALS?|AUTH)",
    re.I,
)
SAFE_SECRET_SUFFIX_RE = re.compile(r"_(FILE|PATH|URL_FILE)$", re.I)
DANGEROUS_CAPS = {"ALL": "high", "SYS_ADMIN": "high", "SYS_PTRACE": "medium", "NET_ADMIN": "medium"}
HOST_ROOT_MOUNTS = {"/", "/etc", "/root", "/home", "/var/lib/docker", "/proc", "/sys"}


@dataclass
class Finding:
    service: str
    check: str
    severity: str
    title: str
    fix: str = ""


# --------------------------------------------------------------------------- loading


def load_compose(path: str) -> dict:
    if path == "-":
        text = sys.stdin.read()
    else:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    stripped = text.lstrip()
    if path.endswith(".json") or stripped.startswith("{"):
        return json.loads(text)
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError(
            "YAML input needs PyYAML (apt install python3-yaml). Or convert first: "
            "docker compose config --format json > compose.json"
        ) from exc
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("compose file is not a mapping")
    return data


# --------------------------------------------------------------------------- helpers


def parse_port(entry) -> dict:
    """Normalise short ('127.0.0.1:8080:80/tcp') and long ({published, target}) syntax."""
    if isinstance(entry, dict):
        return {
            "host_ip": str(entry.get("host_ip", "") or ""),
            "published": str(entry.get("published", "") or ""),
            "target": str(entry.get("target", "")),
            "protocol": str(entry.get("protocol", "tcp") or "tcp"),
        }
    s = str(entry)
    proto = "tcp"
    if "/" in s:
        s, proto = s.rsplit("/", 1)
    host_ip = ""
    m = re.match(r"^\[([^\]]+)\]:(.*)$", s)  # [::1]:8080:80
    if m:
        host_ip, s = m.group(1), m.group(2)
    parts = s.split(":")
    if len(parts) == 3:
        host_ip, published, target = parts
    elif len(parts) == 2:
        published, target = parts
    else:
        published, target = "", parts[0]  # container port only: random host port
    return {"host_ip": host_ip, "published": published, "target": target, "protocol": proto}


def env_items(env) -> list[tuple[str, str | None]]:
    if isinstance(env, dict):
        return [(str(k), None if v is None else str(v)) for k, v in env.items()]
    items = []
    for e in env or []:
        k, sep, v = str(e).partition("=")
        items.append((k, v if sep else None))
    return items


def is_literal_secret(value: str | None) -> bool:
    if value is None or value == "":
        return False
    if re.fullmatch(r"\$\{?[A-Za-z_][A-Za-z0-9_]*(:?[-?][^}]*)?\}?", value):
        return False  # ${VAR} / $VAR / ${VAR:-default} interpolation
    if value.startswith("/run/secrets/"):
        return False
    if value.lower() in ("true", "false", "yes", "no", "0", "1"):
        return False
    return True


def volume_source(vol) -> tuple[str, str]:
    """Return (source, mode/flags) for short or long volume syntax."""
    if isinstance(vol, dict):
        ro = "ro" if vol.get("read_only") else ""
        return str(vol.get("source", "")), ro
    parts = str(vol).split(":")
    if len(parts) == 1:
        return "", ""  # anonymous volume
    return parts[0], parts[2] if len(parts) > 2 else ""


def image_is_unpinned(image: str) -> str | None:
    if "@sha256:" in image:
        return None
    last = image.rsplit("/", 1)[-1]
    if ":" not in last:
        return "no tag (means :latest)"
    tag = last.rsplit(":", 1)[1]
    if tag in ("latest", "stable", "main", "master", "edge", "nightly"):
        return f"floating tag :{tag}"
    if "$" in tag:
        return None
    return None


# --------------------------------------------------------------------------- review


def review(data: dict) -> list[Finding]:
    f: list[Finding] = []
    services = data.get("services") or {}
    if not isinstance(services, dict) or not services:
        return [Finding("-", "services", "high", "No services found in the compose file")]
    if "version" in data:
        f.append(
            Finding(
                "-",
                "version",
                "info",
                "Top-level 'version' is obsolete and ignored by Compose v2",
                "Delete the version: line",
            )
        )
    for name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        f.extend(review_service(str(name), svc))
    return f


def review_service(name: str, svc: dict) -> list[Finding]:
    f: list[Finding] = []
    deploy = svc.get("deploy") or {}

    image = svc.get("image")
    if image and not svc.get("build"):
        problem = image_is_unpinned(str(image))
        if problem:
            f.append(
                Finding(
                    name,
                    "image.tag",
                    "medium",
                    f"Image {image} is not pinned ({problem})",
                    "Pin a version tag (postgres:16.4) or a digest (image@sha256:...)",
                )
            )

    hc = svc.get("healthcheck")
    if not hc or (isinstance(hc, dict) and hc.get("disable")):
        f.append(
            Finding(
                name,
                "healthcheck",
                "low",
                "No healthcheck in compose (fine only if the image defines HEALTHCHECK)",
                'healthcheck: {test: ["CMD", "curl", "-fsS", "http://localhost/health"], '
                "interval: 30s, timeout: 5s, retries: 3}",
            )
        )

    if not svc.get("restart") and not deploy.get("restart_policy"):
        f.append(
            Finding(
                name,
                "restart",
                "low",
                "No restart policy (container stays down after a crash or reboot)",
                "restart: unless-stopped",
            )
        )

    limits = (deploy.get("resources") or {}).get("limits") or {}
    if not svc.get("mem_limit") and not limits.get("memory"):
        f.append(
            Finding(
                name,
                "limits.memory",
                "low",
                "No memory limit (one leak can trigger the host OOM killer)",
                "mem_limit: 512m  # or deploy.resources.limits.memory",
            )
        )

    logging = svc.get("logging") or {}
    driver = logging.get("driver", "json-file")
    if driver in ("json-file", "") and not (logging.get("options") or {}).get("max-size"):
        f.append(
            Finding(
                name,
                "logging",
                "low",
                "json-file logs without max-size grow until the disk is full",
                'logging: {driver: json-file, options: {max-size: "10m", max-file: "3"}}  '
                "(or set it once in /etc/docker/daemon.json)",
            )
        )

    if svc.get("privileged"):
        f.append(
            Finding(
                name,
                "privileged",
                "high",
                "privileged: true gives the container full control of the host",
                "Remove privileged; add only the specific cap_add/devices needed",
            )
        )

    for cap in svc.get("cap_add") or []:
        cap_name = str(cap).upper()
        if cap_name.startswith("CAP_"):
            cap_name = cap_name[4:]
        if cap_name in DANGEROUS_CAPS:
            f.append(
                Finding(
                    name,
                    "cap_add",
                    DANGEROUS_CAPS[cap_name],
                    f"cap_add {cap_name} weakens container isolation",
                    "Drop it unless strictly required",
                )
            )

    if svc.get("network_mode") == "host":
        f.append(
            Finding(
                name,
                "network_mode",
                "medium",
                "network_mode: host exposes every port the app opens on the host",
                "Use a bridge network and publish only the needed ports on 127.0.0.1",
            )
        )
    if svc.get("pid") == "host":
        f.append(Finding(name, "pid", "high", "pid: host lets the container see host processes"))

    for entry in svc.get("ports") or []:
        p = parse_port(entry)
        if p["host_ip"] and p["host_ip"] not in ("0.0.0.0", "::"):
            continue
        published = p["published"] or "(random)"
        key = p["published"] if p["protocol"] == "tcp" else f"{p['published']}/{p['protocol']}"
        if key in PUBLIC_OK_PORTS:
            continue
        target = p["target"].split("-")[0]
        if target in SENSITIVE_PORTS or p["published"] in SENSITIVE_PORTS:
            label = SENSITIVE_PORTS.get(target) or SENSITIVE_PORTS.get(p["published"])
            f.append(
                Finding(
                    name,
                    "ports.sensitive",
                    "high",
                    f"{label} port {published}->{p['target']} is published on all interfaces "
                    "(Docker bypasses UFW)",
                    f'ports: ["127.0.0.1:{p["published"] or p["target"]}:{p["target"]}"]  '
                    "or remove ports and use the internal network",
                )
            )
        else:
            f.append(
                Finding(
                    name,
                    "ports.public",
                    "medium",
                    f"Port {published}->{p['target']} is published on all interfaces (Docker bypasses UFW)",
                    "Bind to localhost behind a reverse proxy: "
                    f'"127.0.0.1:{p["published"] or p["target"]}:{p["target"]}"',
                )
            )

    for key, value in env_items(svc.get("environment")):
        if SECRET_KEY_RE.search(key) and not SAFE_SECRET_SUFFIX_RE.search(key):
            if is_literal_secret(value):
                f.append(
                    Finding(
                        name,
                        "secrets.environment",
                        "high",
                        f"environment {key} has a literal value in the compose file (value hidden)",
                        f"Use {key}: ${{{key}}} with an untracked .env file, or Compose secrets "
                        f"and {key}_FILE: /run/secrets/...",
                    )
                )

    for vol in svc.get("volumes") or []:
        src, mode = volume_source(vol)
        if src.rstrip("/").endswith("docker.sock"):
            f.append(
                Finding(
                    name,
                    "volumes.docker_sock",
                    "high",
                    f"Mounts the Docker socket{' (read-only does not help)' if 'ro' in mode else ''}: "
                    "root on the host if the container is compromised",
                    "Use a socket proxy (e.g. tecnativa/docker-socket-proxy) with minimal API access",
                )
            )
        elif (src.rstrip("/") or "/") in HOST_ROOT_MOUNTS and src.startswith("/"):
            f.append(
                Finding(
                    name,
                    "volumes.host_root",
                    "high" if "ro" not in mode else "medium",
                    f"Mounts sensitive host path {src}{' read-only' if 'ro' in mode else ''}",
                    "Mount only the specific subdirectory the service needs",
                )
            )
    return f


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Review a docker-compose file for security and production readiness.",
        epilog="Exit codes: 0 no findings at/above --fail-on, 1 findings, 2 error.",
    )
    p.add_argument("file", help="compose.yaml / docker-compose.yml / JSON, or - for stdin")
    p.add_argument("--json", action="store_true", help="print JSON")
    p.add_argument(
        "--fail-on",
        choices=SEVERITIES[1:] + ["none"],
        default="medium",
        help="lowest severity that makes the exit code 1 (default: medium)",
    )
    args = p.parse_args(argv)
    try:
        data = load_compose(args.file)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # yaml.YAMLError without importing yaml at top level
        print(f"error: could not parse {args.file}: {exc}", file=sys.stderr)
        return 2

    findings = review(data)
    order = {s: i for i, s in enumerate(reversed(SEVERITIES))}
    findings.sort(key=lambda x: (order[x.severity], x.service))
    if args.json:
        print(json.dumps({"findings": [asdict(x) for x in findings]}, indent=2))
    else:
        for x in findings:
            print(f"[{x.severity.upper():6}] {x.service}: {x.title}")
            if x.fix:
                print(f"         fix: {x.fix}")
        counts = {s: sum(1 for x in findings if x.severity == s) for s in SEVERITIES}
        print("Summary: " + ", ".join(f"{counts[s]} {s}" for s in reversed(SEVERITIES)))
    if args.fail_on == "none":
        return 0
    threshold = SEVERITIES.index(args.fail_on)
    return 1 if any(SEVERITIES.index(x.severity) >= threshold for x in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
