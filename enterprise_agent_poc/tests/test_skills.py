from __future__ import annotations

from app.skills import SkillDeployment


def test_only_profile_skills_are_deployed(tmp_path):
    registry = tmp_path / "registry"
    for name in ("poster-design", "copywriting"):
        source = registry / name / "1.0.0"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(f"# {name}", encoding="utf-8")
    target = tmp_path / "codex-home" / "skills"
    deployer = SkillDeployment(registry)
    deployer.deploy({"poster-design": "1.0.0"}, target)
    assert (target / "poster-design" / "SKILL.md").exists()
    assert not (target / "copywriting").exists()
