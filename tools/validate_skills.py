#!/usr/bin/env python3
"""Validate every skill in skills/ and the Claude Code plugin manifests.

Checks:
  * SKILL.md exists and starts with YAML frontmatter
  * name: 1-64 chars, lowercase letters/digits/single hyphens, equals the folder name
  * description: 1-1024 chars, says when to use the skill ("Use when ...")
  * only known frontmatter keys (Agent Skills spec + common Claude Code keys)
  * required body sections exist; body is at most MAX_LINES lines
  * every relative link and every scripts/ references/ assets/ path mentioned exists
  * every script in scripts/ supports --help and is referenced by a test in tests/
  * .claude-plugin/marketplace.json and plugin.json are valid and list every skill

Usage: python tools/validate_skills.py [--root DIR] [--json]
Exit codes: 0 = valid, 1 = problems found, 2 = usage error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
ALLOWED_KEYS = {
    "name",
    "description",
    "license",
    "compatibility",
    "metadata",
    "allowed-tools",
    "when_to_use",
    "argument-hint",
    "disable-model-invocation",
    "user-invocable",
}
REQUIRED_SECTIONS = ["## When to use", "## When not to use", "## Workflow", "## Output"]
MAX_LINES = 400
MAX_DESCRIPTION = 1024
MAX_COMPATIBILITY = 500
LINK_RE = re.compile(r"\]\(([^)\s]+)\)")
PATH_MENTION_RE = re.compile(r"(?<![\w/.-])((?:scripts|references|assets)/[A-Za-z0-9_./-]+[A-Za-z0-9_])")


def parse_frontmatter(text: str) -> tuple[dict, str, list[str]]:
    """Parse the small YAML subset used in SKILL.md frontmatter.

    Supports ``key: value`` scalars (plain, single- or double-quoted, ``>``/``|``
    block scalars) and one level of nested ``key:`` maps (for ``metadata``).
    Returns (data, body, errors).
    """
    errors: list[str] = []
    if not text.startswith("---\n"):
        return {}, text, ["SKILL.md must start with '---' frontmatter"]
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text, ["frontmatter is not closed with '---'"]
    raw = text[4:end]
    body = text[end + 4 :].lstrip("\n")
    data: dict = {}
    lines = raw.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not m:
            errors.append(f"cannot parse frontmatter line: {line!r}")
            i += 1
            continue
        key, value = m.group(1), m.group(2).strip()
        i += 1
        if value in (">", "|", ">-", "|-"):
            block = []
            while i < len(lines) and (lines[i].startswith("  ") or not lines[i].strip()):
                block.append(lines[i].strip())
                i += 1
            joiner = " " if value.startswith(">") else "\n"
            data[key] = joiner.join(b for b in block if b).strip()
        elif value == "":
            nested = {}
            while i < len(lines) and lines[i].startswith("  "):
                nm = re.match(r"^\s+([A-Za-z0-9_.-]+):\s*(.*)$", lines[i])
                if nm:
                    nested[nm.group(1)] = _unquote(nm.group(2).strip())
                else:
                    errors.append(f"cannot parse nested line: {lines[i]!r}")
                i += 1
            data[key] = nested
        else:
            data[key] = _unquote(value)
    return data, body, errors


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        return inner.replace("''", "'") if value[0] == "'" else inner.replace('\\"', '"')
    return value


def validate_skill(skill_dir: Path, root: Path) -> list[str]:
    problems: list[str] = []
    rel = skill_dir.relative_to(root)
    md = skill_dir / "SKILL.md"
    if not md.is_file():
        return [f"{rel}: missing SKILL.md"]
    text = md.read_text(encoding="utf-8")
    data, body, errors = parse_frontmatter(text)
    problems += [f"{rel}/SKILL.md: {e}" for e in errors]

    name = data.get("name", "")
    if not isinstance(name, str) or not name:
        problems.append(f"{rel}/SKILL.md: missing 'name'")
    else:
        if len(name) > 64:
            problems.append(f"{rel}/SKILL.md: name longer than 64 characters")
        if not NAME_RE.match(name):
            problems.append(
                f"{rel}/SKILL.md: name '{name}' must be lowercase letters, digits and single hyphens"
            )
        if name != skill_dir.name:
            problems.append(f"{rel}/SKILL.md: name '{name}' does not match folder '{skill_dir.name}'")

    desc = data.get("description", "")
    if not isinstance(desc, str) or not desc.strip():
        problems.append(f"{rel}/SKILL.md: missing 'description'")
    else:
        if len(desc) > MAX_DESCRIPTION:
            problems.append(f"{rel}/SKILL.md: description is {len(desc)} chars (max {MAX_DESCRIPTION})")
        if "use when" not in desc.lower():
            problems.append(f"{rel}/SKILL.md: description should say when to use it ('Use when ...')")
        if re.search(r"<[^>]+>", desc):
            problems.append(f"{rel}/SKILL.md: description must not contain XML/HTML tags")

    comp = data.get("compatibility")
    if isinstance(comp, str) and len(comp) > MAX_COMPATIBILITY:
        problems.append(f"{rel}/SKILL.md: compatibility longer than {MAX_COMPATIBILITY} characters")
    for key in data:
        if key not in ALLOWED_KEYS:
            problems.append(f"{rel}/SKILL.md: unknown frontmatter key '{key}'")
    if "metadata" in data and not isinstance(data["metadata"], dict):
        problems.append(f"{rel}/SKILL.md: metadata must be a map")

    for section in REQUIRED_SECTIONS:
        if not re.search(rf"^{re.escape(section)}\b", body, re.M | re.I):
            problems.append(f"{rel}/SKILL.md: missing section '{section}'")
    n_lines = len(text.splitlines())
    if n_lines > MAX_LINES:
        problems.append(f"{rel}/SKILL.md: {n_lines} lines (max {MAX_LINES}); move detail to references/")

    # Referenced files exist (links and scripts/ references/ assets/ mentions).
    targets = set()
    for link in LINK_RE.findall(body):
        if re.match(r"^[a-z]+:", link) or link.startswith("#"):
            continue
        targets.add(link.split("#", 1)[0])
    targets.update(PATH_MENTION_RE.findall(body))
    for t in sorted(targets):
        if t and not (skill_dir / t).exists():
            problems.append(f"{rel}/SKILL.md: references missing file '{t}'")

    # Scripts: --help and a test that mentions them.
    tests_dir = root / "tests"
    test_text = ""
    if tests_dir.is_dir():
        test_text = "\n".join(p.read_text(encoding="utf-8") for p in tests_dir.rglob("test_*.py"))
    scripts = skill_dir / "scripts"
    if scripts.is_dir():
        for script in sorted(scripts.iterdir()):
            if script.suffix not in (".py", ".sh") or script.name.startswith("_"):
                continue
            src = script.read_text(encoding="utf-8")
            if "--help" not in src and "argparse" not in src:
                problems.append(f"{rel}/scripts/{script.name}: must support --help")
            if script.name not in test_text:
                problems.append(f"{rel}/scripts/{script.name}: no test in tests/ references it")
            if script.suffix == ".py" and not src.startswith("#!/usr/bin/env python3"):
                problems.append(f"{rel}/scripts/{script.name}: missing '#!/usr/bin/env python3'")
            if f"scripts/{script.name}" not in body:
                problems.append(f"{rel}/SKILL.md: does not mention scripts/{script.name}")
    return problems


def validate_plugin(root: Path, skill_dirs: list[Path]) -> list[str]:
    problems: list[str] = []
    mp_path = root / ".claude-plugin" / "marketplace.json"
    pj_path = root / ".claude-plugin" / "plugin.json"
    try:
        mp = json.loads(mp_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [".claude-plugin/marketplace.json: missing"]
    except json.JSONDecodeError as exc:
        return [f".claude-plugin/marketplace.json: invalid JSON: {exc}"]
    for key in ("name", "owner", "plugins"):
        if key not in mp:
            problems.append(f"marketplace.json: missing '{key}'")
    if not isinstance(mp.get("owner"), dict) or not mp.get("owner", {}).get("name"):
        problems.append("marketplace.json: owner.name is required")
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", str(mp.get("name", ""))) or ".." in str(
        mp.get("name", "")
    ):
        problems.append("marketplace.json: invalid marketplace name")
    plugins = mp.get("plugins") or []
    if not isinstance(plugins, list) or not plugins:
        return problems + ["marketplace.json: plugins must be a non-empty list"]

    pj = None
    if pj_path.exists():
        try:
            pj = json.loads(pj_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"plugin.json: invalid JSON: {exc}")
    listed: set[str] = set()
    names = [p.get("name") for p in plugins if isinstance(p, dict)]
    if len(names) != len(set(names)):
        problems.append("marketplace.json: duplicate plugin names")
    for i, entry in enumerate(plugins):
        if not isinstance(entry, dict) or "name" not in entry or "source" not in entry:
            problems.append(f"marketplace.json: plugins[{i}] needs 'name' and 'source'")
            continue
        src = entry["source"]
        if isinstance(src, str):
            if not (src.startswith("./") or src == "."):
                problems.append(f"marketplace.json: plugins[{i}].source must start with './'")
            if ".." in src:
                problems.append(f"marketplace.json: plugins[{i}].source must not contain '..'")
            plugin_root = (root / src).resolve()
            if not plugin_root.is_dir():
                problems.append(f"marketplace.json: plugins[{i}].source does not exist: {src}")
            if plugin_root == root.resolve() and pj is not None and pj.get("name") != entry["name"]:
                problems.append(
                    f"marketplace.json: plugins[{i}].name '{entry['name']}' differs from "
                    f"plugin.json name '{pj.get('name')}'"
                )
        else:
            plugin_root = None
        if pj is not None and "version" in entry and "version" in pj:
            problems.append(f"marketplace.json: plugins[{i}] sets version; keep it only in plugin.json")
        for sp in entry.get("skills") or []:
            if not sp.startswith("./"):
                problems.append(f"marketplace.json: skill path '{sp}' must start with './'")
            base = plugin_root or root
            if not (base / sp / "SKILL.md").is_file():
                problems.append(f"marketplace.json: skill path '{sp}' has no SKILL.md")
            listed.add(Path(sp).name)
    expected = {d.name for d in skill_dirs}
    for missing in sorted(expected - listed):
        problems.append(f"marketplace.json: skill '{missing}' is not listed in any plugin's skills")
    for extra in sorted(listed - expected):
        problems.append(f"marketplace.json: lists '{extra}' which is not in skills/")
    if pj is not None:
        if not pj.get("name") or not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", pj["name"]):
            problems.append("plugin.json: name is required and must be kebab-case")
        author = pj.get("author")
        if author is not None and (not isinstance(author, dict) or not author.get("name")):
            problems.append("plugin.json: author must be an object with a name")
    return problems


def validate(root: Path) -> list[str]:
    skills_root = root / "skills"
    if not skills_root.is_dir():
        return ["skills/ directory not found"]
    skill_dirs = sorted(p for p in skills_root.iterdir() if p.is_dir() and not p.name.startswith("."))
    if not skill_dirs:
        return ["skills/ contains no skills"]
    problems: list[str] = []
    for d in skill_dirs:
        problems += validate_skill(d, root)
    problems += validate_plugin(root, skill_dirs)
    return problems


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Validate SKILL.md files and plugin manifests.")
    p.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    p.add_argument("--json", action="store_true", help="print JSON")
    args = p.parse_args(argv)
    if not args.root.is_dir():
        print(f"error: {args.root} is not a directory", file=sys.stderr)
        return 2
    problems = validate(args.root)
    skills = (
        sorted(d.name for d in (args.root / "skills").iterdir() if d.is_dir())
        if (args.root / "skills").is_dir()
        else []
    )
    if args.json:
        print(json.dumps({"ok": not problems, "skills": skills, "problems": problems}, indent=2))
    elif problems:
        for prob in problems:
            print(f"FAIL {prob}")
        print(f"\n{len(problems)} problem(s) in {len(skills)} skill(s)")
    else:
        print(f"OK: {len(skills)} skills valid ({', '.join(skills)})")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
