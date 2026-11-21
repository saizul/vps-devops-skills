from conftest import load_script, run_script

SKILL = "backup-setup"
SCRIPT = "make_restic_units.py"
mod = load_script(SKILL, SCRIPT)


def test_generates_all_files(tmp_path):
    r = run_script(
        SKILL,
        SCRIPT,
        "--repo",
        "s3:s3.eu-central-1.amazonaws.com/my-bucket/web1",
        "--path",
        "/etc",
        "--path",
        "/var/www",
        "--mysql",
        "--out",
        str(tmp_path),
    )
    assert r.returncode == 0, r.stderr
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == [
        "restic-system.env.example",
        "restic-system.excludes",
        "restic-system.service",
        "restic-system.sh",
        "restic-system.timer",
    ]
    script = (tmp_path / "restic-system.sh").read_text()
    assert script.startswith("#!/bin/sh")
    assert "mysqldump --all-databases --single-transaction" in script
    assert "/var/backups/db" in script  # dump dir is backed up
    assert "--keep-daily 7 --keep-weekly 4 --keep-monthly 6" in script
    assert "restic check --read-data-subset=5%" in script
    env = (tmp_path / "restic-system.env.example").read_text()
    assert "AWS_ACCESS_KEY_ID=\n" in env and "AWS_SECRET_ACCESS_KEY=\n" in env
    timer = (tmp_path / "restic-system.timer").read_text()
    assert "OnCalendar=*-*-* 03:15:00" in timer and "Persistent=true" in timer
    service = (tmp_path / "restic-system.service").read_text()
    assert "Type=oneshot" in service
    assert "EnvironmentFile=/etc/restic/restic-system.env" in service
    assert "restic init" in r.stdout and "restore drill" in r.stdout


def test_refuses_to_overwrite_without_force(tmp_path):
    args = ["--repo", "sftp:backup@host:/srv/restic", "--out", str(tmp_path)]
    assert run_script(SKILL, SCRIPT, *args).returncode == 0
    again = run_script(SKILL, SCRIPT, *args)
    assert again.returncode == 2 and "already exist" in again.stderr
    assert run_script(SKILL, SCRIPT, *args, "--force").returncode == 0


def test_dry_run_writes_nothing(tmp_path):
    out = tmp_path / "out"
    r = run_script(
        SKILL, SCRIPT, "--repo", "rclone:gdrive:backups", "--postgres", "--dry-run", "--out", str(out)
    )
    assert r.returncode == 0
    assert not out.exists()
    assert "pg_dumpall" in r.stdout and "rclone config" in r.stdout


def test_validation_errors():
    assert run_script(SKILL, SCRIPT, "--repo", "ftp://nope").returncode == 2
    assert (
        run_script(SKILL, SCRIPT, "--repo", "/srv/restic", "--name", "Bad Name", "--dry-run").returncode == 2
    )
    assert (
        run_script(SKILL, SCRIPT, "--repo", "/srv/restic", "--path", "relative/dir", "--dry-run").returncode
        == 2
    )
    assert (
        run_script(
            SKILL, SCRIPT, "--repo", "/srv/restic", "--schedule", "daily; rm -rf /", "--dry-run"
        ).returncode
        == 2
    )
    assert run_script(SKILL, SCRIPT, "--help").returncode == 0


def test_paths_are_shell_quoted(tmp_path):
    r = run_script(SKILL, SCRIPT, "--repo", "/srv/restic", "--path", "/srv/my files", "--dry-run")
    assert "'/srv/my files'" in r.stdout


def test_repo_env_vars():
    assert mod.repo_env_vars("b2:bucket:path") == ["B2_ACCOUNT_ID", "B2_ACCOUNT_KEY"]
    assert mod.repo_env_vars("/mnt/backup") == []
