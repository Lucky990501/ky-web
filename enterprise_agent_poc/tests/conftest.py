"""Isolated Git repositories are test fixtures, never commits in the project."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.fixture
def approved_bundle(tmp_path):
    target = tmp_path / "approved" / "skill_packages"
    shutil.copytree(Path(__file__).resolve().parents[1] / "skill_packages", target)
    return target


@pytest.fixture
def bundle_git_repo(tmp_path, approved_bundle):
    repo = tmp_path / "git-fixture"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for key, value in [("user.name", "Fixture"), ("user.email", "fixture@example.invalid"),
                       ("core.autocrlf", "false"), ("core.eol", "lf")]:
        subprocess.run(["git", "-C", str(repo), "config", key, value], check=True)
    root = repo / "enterprise_agent_poc"
    shutil.copytree(approved_bundle, root / "skill_packages")
    (root / "app").mkdir()
    (root / "app" / "main.py").write_text("# fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "approved fixture"], check=True)
    return repo
