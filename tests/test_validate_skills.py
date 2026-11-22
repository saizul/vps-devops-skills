import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT / "tools"))
import validate_skills as v  # noqa: E402

GOOD_SKILL = """---
name: demo-skill
description: Demo checks. Use when testing the validator.
license: MIT
metadata:
  author: Test
  version: "0.1.0"
---

# Demo

## When to use
- tests

## When not to use
- prod

## Workflow
1. Run `python3 scripts/demo.py` and read [references/more.md](references/more.md).

## Output
Text.
"""


def make_repo(tmp_path: Path, skill_md: str = GOOD_SKILL, name: str = "demo-skill", listed=None) -> Path:
    root = tmp_path / "repo"
    skill = root / "skills" / name
    (skill / "scripts").mkdir(parents=True)
    (skill / "references").mkdir()
    (skill / "SKILL.md").write_text(skill_md)
    (skill / "references" / "more.md").write_text("# more\n")
    (skill / "scripts" / "demo.py").write_text("#!/usr/bin/env python3\nimport argparse\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_demo.py").write_text('SCRIPT = "demo.py"\n')
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "demo", "version": "0.1.0", "author": {"name": "T"}})
    )
    listed = [f"./skills/{name}"] if listed is None else listed
    (root / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps(
            {
                "name": "demo-market",
                "owner": {"name": "T"},
                "plugins": [{"name": "demo", "source": "./", "skills": listed}],
            }
        )
    )
    return root


def test_this_repository_is_valid():
    assert v.validate(ROOT) == []


def test_good_repo(tmp_path):
    assert v.validate(make_repo(tmp_path)) == []


@pytest.mark.parametrize(
    "old,new,message",
    [
        ("name: demo-skill", "name: Demo_Skill", "lowercase letters"),
        ("name: demo-skill", "name: other-skill", "does not match folder"),
        ("name: demo-skill", "name: demo--skill", "lowercase letters"),
        (
            "description: Demo checks. Use when testing the validator.",
            "description: Demo checks.",
            "Use when",
        ),
        (
            "description: Demo checks. Use when testing the validator.",
            "description: " + "x" * 1030 + " use when",
            "max 1024",
        ),
        (
            "description: Demo checks. Use when testing the validator.",
            "description: Use when <b>tags</b>",
            "XML",
        ),
        ("license: MIT", "license: MIT\ncolour: blue", "unknown frontmatter key 'colour'"),
        ("## When not to use", "## Something else", "missing section '## When not to use'"),
        (
            "[references/more.md](references/more.md)",
            "[references/gone.md](references/gone.md)",
            "references/gone.md",
        ),
        (
            "`python3 scripts/demo.py`",
            "`python3 scripts/demo.py` and `scripts/missing.sh`",
            "scripts/missing.sh",
        ),
    ],
)
def test_skill_problems(tmp_path, old, new, message):
    md = GOOD_SKILL.replace(old, new)
    assert md != GOOD_SKILL
    problems = v.validate(make_repo(tmp_path, md))
    assert any(message in p for p in problems), problems


def test_missing_frontmatter(tmp_path):
    problems = v.validate(make_repo(tmp_path, "# no frontmatter\n"))
    assert any("frontmatter" in p for p in problems)


def test_too_long(tmp_path):
    md = GOOD_SKILL + "\nline" * 400
    assert any("max 400" in p for p in v.validate(make_repo(tmp_path, md)))


def test_untested_script(tmp_path):
    root = make_repo(tmp_path)
    (root / "tests" / "test_demo.py").write_text("# nothing\n")
    assert any("no test" in p for p in v.validate(root))


def test_script_not_mentioned_in_skill(tmp_path):
    root = make_repo(tmp_path)
    (root / "skills" / "demo-skill" / "scripts" / "extra.py").write_text(
        "#!/usr/bin/env python3\nimport argparse\n"
    )
    (root / "tests" / "test_extra.py").write_text('"extra.py"\n')
    assert any("does not mention scripts/extra.py" in p for p in v.validate(root))


def test_marketplace_must_list_every_skill(tmp_path):
    problems = v.validate(make_repo(tmp_path, listed=[]))
    assert any("not listed" in p for p in problems)
    problems = v.validate(make_repo(tmp_path / "b", listed=["./skills/demo-skill", "./skills/ghost"]))
    assert any("ghost" in p for p in problems)


def test_marketplace_problems(tmp_path):
    root = make_repo(tmp_path)
    mp = root / ".claude-plugin" / "marketplace.json"
    mp.write_text("{not json")
    assert any("invalid JSON" in p for p in v.validate(root))
    mp.write_text(json.dumps({"name": "x", "plugins": []}))
    problems = v.validate(root)
    assert any("owner" in p for p in problems) and any("non-empty" in p for p in problems)
    mp.write_text(
        json.dumps(
            {
                "name": "x",
                "owner": {"name": "T"},
                "plugins": [
                    {"name": "other", "source": "../up", "version": "1", "skills": ["./skills/demo-skill"]}
                ],
            }
        )
    )
    problems = v.validate(root)
    assert any("must not contain '..'" in p for p in problems)
    assert any("version" in p for p in problems)


def test_plugin_name_mismatch(tmp_path):
    root = make_repo(tmp_path)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "different"}))
    assert any("differs from plugin.json" in p for p in v.validate(root))


def test_template_skill_passes(tmp_path):
    root = make_repo(tmp_path)
    shutil.rmtree(root / "skills" / "demo-skill")
    shutil.copytree(ROOT / "templates" / "skill", root / "skills" / "your-skill-name")
    (root / "tests" / "test_demo.py").write_text('"your_script.py"\n')
    mp = root / ".claude-plugin" / "marketplace.json"
    data = json.loads(mp.read_text())
    data["plugins"][0]["skills"] = ["./skills/your-skill-name"]
    mp.write_text(json.dumps(data))
    assert v.validate(root) == []


def test_template_script_runs(tmp_path):
    f = tmp_path / "in.txt"
    f.write_text("TODO: x")
    script = ROOT / "templates" / "skill" / "scripts" / "your_script.py"
    r = subprocess.run([sys.executable, str(script), str(f)], capture_output=True, text=True, check=False)
    assert r.returncode == 1 and "TODO" in r.stdout


def test_frontmatter_parser_block_scalars():
    data, body, errors = v.parse_frontmatter(
        "---\nname: a\ndescription: >\n  line one\n  line two\n---\nbody\n"
    )
    assert errors == [] and data["description"] == "line one line two" and body == "body\n"
    data, _, _ = v.parse_frontmatter("---\nname: 'it''s'\ndescription: \"q\"\n---\n")
    assert data["name"] == "it's" and data["description"] == "q"


def test_cli(tmp_path):
    script = ROOT / "tools" / "validate_skills.py"
    ok = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, check=False)
    assert ok.returncode == 0 and ok.stdout.startswith("OK: 9 skills")
    root = make_repo(tmp_path, listed=[])
    bad = subprocess.run(
        [sys.executable, str(script), "--root", str(root), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad.returncode == 1 and json.loads(bad.stdout)["ok"] is False
    missing = subprocess.run(
        [sys.executable, str(script), "--root", str(tmp_path / "nope")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode == 2
