from __future__ import annotations

import shutil
from pathlib import Path


class SkillDeployment:
    def __init__(self, registry_root: Path) -> None:
        self.registry_root = registry_root

    def deploy(self, manifest: dict[str, str], target_skills_dir: Path) -> None:
        target_skills_dir.mkdir(parents=True, exist_ok=True)
        registry_root = self.registry_root.resolve()
        if any(not name or not version or any(value in name or value in version for value in ("/", "\\", "..")) for name, version in manifest.items()):
            raise ValueError("Skill manifest 包含不安全路径。")
        expected = set(manifest)
        for child in target_skills_dir.iterdir():
            if child.name not in expected:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        for skill_name, version in manifest.items():
            source = (registry_root / skill_name / version).resolve()
            if not source.is_relative_to(registry_root):
                raise ValueError("Skill manifest 超出 Registry 根目录。")
            if not (source / "SKILL.md").is_file():
                raise FileNotFoundError(f"Skill Registry 中缺少 {skill_name}@{version}。")
            target = target_skills_dir / skill_name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target)
