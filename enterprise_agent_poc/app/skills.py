from __future__ import annotations

import shutil
from pathlib import Path


class SkillDeployment:
    def __init__(self, registry_root: Path) -> None:
        self.registry_root = registry_root

    def deploy(self, manifest: dict[str, str], target_skills_dir: Path) -> None:
        target_skills_dir.mkdir(parents=True, exist_ok=True)
        expected = set(manifest)
        for child in target_skills_dir.iterdir():
            if child.name not in expected:
                shutil.rmtree(child)
        for skill_name, version in manifest.items():
            source = self.registry_root / skill_name / version
            if not (source / "SKILL.md").is_file():
                raise FileNotFoundError(f"Skill Registry 中缺少 {skill_name}@{version}。")
            target = target_skills_dir / skill_name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target)
