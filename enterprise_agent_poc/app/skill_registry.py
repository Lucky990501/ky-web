from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import stat
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from app.agent_catalog import CATALOG
from app.store import POCStore


SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_ENTRIES = 500
ALLOWED_ROOT_ENTRIES = {"SKILL.md", "agents", "references", "scripts", "assets", "README.md", "LICENSE", "LICENSE.md"}
ROOT_FILES = {"SKILL.md", "README.md", "LICENSE", "LICENSE.md"}


class SkillRegistryError(ValueError):
    pass


class NativeSkillArchive:
    @classmethod
    def inspect(cls, content: bytes, expected_slug: str) -> dict:
        if not SLUG_RE.fullmatch(expected_slug):
            raise SkillRegistryError("Skill slug 只能使用小写字母、数字和连字符。")
        if not content or len(content) > MAX_ARCHIVE_BYTES:
            raise SkillRegistryError("Skill ZIP 为空或超过 20MB。")
        try:
            archive = zipfile.ZipFile(io.BytesIO(content))
        except zipfile.BadZipFile as exc:
            raise SkillRegistryError("Skill 包不是有效 ZIP。") from exc
        infos = archive.infolist()
        if not infos or len(infos) > MAX_ENTRIES:
            raise SkillRegistryError("Skill ZIP 文件数量无效。")
        total = 0
        files: list[str] = []
        skill_md: zipfile.ZipInfo | None = None
        seen: set[str] = set()
        for info in infos:
            raw = info.filename
            if "\\" in raw:
                raise SkillRegistryError("Skill ZIP 路径必须使用正斜杠。")
            path = PurePosixPath(raw)
            parts = path.parts
            if path.is_absolute() or not parts or any(part in {"", ".", ".."} for part in parts):
                raise SkillRegistryError("Skill ZIP 包含不安全路径。")
            if len(raw) > 240 or any(":" in part or part.endswith((" ", ".")) or any(ord(char) < 32 for char in part) for part in parts):
                raise SkillRegistryError("Skill ZIP 包含不兼容的文件名。")
            if parts[0] != expected_slug:
                raise SkillRegistryError(f"Skill ZIP 根目录必须是 {expected_slug}/。")
            unix_mode = info.external_attr >> 16
            if stat.S_ISLNK(unix_mode):
                raise SkillRegistryError("Skill ZIP 不允许符号链接。")
            file_type = stat.S_IFMT(unix_mode)
            if file_type and not (stat.S_ISREG(unix_mode) or stat.S_ISDIR(unix_mode)):
                raise SkillRegistryError("Skill ZIP 不允许特殊文件。")
            if info.flag_bits & 0x1:
                raise SkillRegistryError("Skill ZIP 不允许加密条目。")
            total += info.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                raise SkillRegistryError("Skill ZIP 解压后超过 50MB。")
            canonical = path.as_posix().rstrip("/")
            canonical_key = canonical.casefold()
            if canonical_key in seen and not info.is_dir():
                raise SkillRegistryError("Skill ZIP 包含重复文件。")
            seen.add(canonical_key)
            if len(parts) > 1 and parts[1] not in ALLOWED_ROOT_ENTRIES:
                raise SkillRegistryError(f"Skill ZIP 包含不支持的根目录项：{parts[1]}。")
            if len(parts) > 2 and parts[1] in ROOT_FILES:
                raise SkillRegistryError(f"Skill ZIP 文件 {parts[1]} 不能作为目录。")
            if not info.is_dir():
                files.append(canonical)
            if canonical == f"{expected_slug}/SKILL.md" and not info.is_dir():
                skill_md = info
        if skill_md is None:
            raise SkillRegistryError("Skill ZIP 缺少根目录下的 SKILL.md。")
        try:
            skill_text = archive.read(skill_md).decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise SkillRegistryError("SKILL.md 必须是 UTF-8 文本。") from exc
        if not skill_text.strip() or len(skill_text) > 1_000_000:
            raise SkillRegistryError("SKILL.md 为空或过大。")
        return {
            "slug": expected_slug,
            "file_count": len(files),
            "files": sorted(files),
            "uncompressed_bytes": total,
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    @classmethod
    def extract(cls, content: bytes, expected_slug: str, destination: Path) -> dict:
        inspection = cls.inspect(content, expected_slug)
        archive = zipfile.ZipFile(io.BytesIO(content))
        destination.mkdir(parents=True, exist_ok=False)
        for info in archive.infolist():
            relative = PurePosixPath(info.filename).relative_to(expected_slug)
            if not relative.parts:
                continue
            target = destination.joinpath(*relative.parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))
            unix_mode = (info.external_attr >> 16) & 0o777
            if unix_mode:
                target.chmod(unix_mode)
        return inspection


class SkillRegistry:
    def __init__(self, store: POCStore, data_root: Path, bundled_root: Path) -> None:
        self._store = store
        self.data_root = data_root
        self.bundled_root = bundled_root
        self.packages_root = data_root / "packages"
        self.drafts_root = data_root / "drafts"
        self.published_root = data_root / "published"

    def initialize(self) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)
        schema = self._postgres_schema() if self._store.is_postgres else self._sqlite_schema()
        with self._store.connection() as conn:
            if self._store.is_postgres:
                conn.execute(schema)
            else:
                conn.executescript(schema)
        self._seed_bundled()

    @staticmethod
    def _postgres_schema() -> str:
        return (Path(__file__).resolve().parents[1] / "migrations" / "postgres" / "005_skill_registry_v1.sql").read_text(encoding="utf-8")

    @staticmethod
    def _sqlite_schema() -> str:
        return """
        CREATE TABLE IF NOT EXISTS skills (id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', created_by TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS skill_versions (id TEXT PRIMARY KEY, skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE, version TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('draft','published','deprecated')), checksum TEXT NOT NULL, created_by TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, published_at TEXT, deprecated_at TEXT, UNIQUE(skill_id,version), UNIQUE(skill_id,id));
        CREATE TABLE IF NOT EXISTS skill_packages (id TEXT PRIMARY KEY, skill_version_id TEXT NOT NULL UNIQUE REFERENCES skill_versions(id) ON DELETE CASCADE, storage_path TEXT NOT NULL, sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS agent_skill_bindings (agent_id TEXT NOT NULL REFERENCES agent_templates(id) ON DELETE CASCADE, skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE, skill_version_id TEXT NOT NULL REFERENCES skill_versions(id), updated_by TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(agent_id,skill_id), FOREIGN KEY(skill_id,skill_version_id) REFERENCES skill_versions(skill_id,id));
        CREATE TABLE IF NOT EXISTS platform_admins (user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, granted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE INDEX IF NOT EXISTS idx_skill_versions_skill_status ON skill_versions(skill_id,status);
        CREATE INDEX IF NOT EXISTS idx_agent_skill_bindings_agent ON agent_skill_bindings(agent_id);
        """

    @staticmethod
    def _zip_directory(slug: str, source: Path) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(item for item in source.rglob("*") if item.is_file()):
                relative = path.relative_to(source).as_posix()
                info = zipfile.ZipInfo(f"{slug}/{relative}", date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes())
        return buffer.getvalue()

    def _seed_bundled(self) -> None:
        for slug_dir in sorted(path for path in self.bundled_root.iterdir() if path.is_dir()):
            for version_dir in sorted(path for path in slug_dir.iterdir() if path.is_dir()):
                if not (version_dir / "SKILL.md").is_file() or not VERSION_RE.fullmatch(version_dir.name):
                    continue
                content = self._zip_directory(slug_dir.name, version_dir)
                self._upsert_import(slug_dir.name, version_dir.name, slug_dir.name, "Bundled native Codex Skill", content, None, published=True)
        for agent in CATALOG.values():
            for slug, version in agent.skill_manifest.items():
                self.bind_agent(agent.id, slug, version, None)

    def import_archive(self, slug: str, version: str, name: str, description: str, content: bytes, user_id: str) -> dict:
        if len(slug) > 80 or len(version) > 64:
            raise SkillRegistryError("Skill slug 或 version 过长。")
        if not VERSION_RE.fullmatch(version):
            raise SkillRegistryError("Skill version 必须是 SemVer，例如 1.0.0。")
        if not name.strip() or len(name.strip()) > 120 or len(description.strip()) > 500:
            raise SkillRegistryError("Skill 名称为空或元数据过长。")
        return self._public_version(self._upsert_import(slug, version, name.strip(), description.strip(), content, user_id, published=False))

    @staticmethod
    def _public_version(item: dict) -> dict:
        return {key: value for key, value in item.items() if key != "storage_path"}

    def _upsert_import(self, slug: str, version: str, name: str, description: str, content: bytes, user_id: str | None, *, published: bool) -> dict:
        inspection = NativeSkillArchive.inspect(content, slug)
        with self._store.connection() as conn:
            skill = conn.execute("SELECT id FROM skills WHERE slug=?", (slug,)).fetchone()
            skill_id = skill["id"] if skill else str(uuid.uuid4())
            existing = conn.execute("SELECT id,status,checksum FROM skill_versions WHERE skill_id=? AND version=?", (skill_id, version)).fetchone() if skill else None
            if existing and existing["status"] != "draft":
                if published and existing["checksum"] == inspection["sha256"]:
                    self._ensure_published_files(slug, version, content)
                    return self.version(existing["id"])
                raise SkillRegistryError("已发布或已废弃版本不可覆盖；请导入新版本。")
            version_id = existing["id"] if existing else str(uuid.uuid4())
        package_dir = self.packages_root / version_id
        draft_dir = self.drafts_root / slug / version
        package_dir.mkdir(parents=True, exist_ok=True)
        package_path = package_dir / f"{inspection['sha256']}.zip"
        package_path.write_bytes(content)
        temporary = draft_dir.with_name(f"{draft_dir.name}.tmp-{uuid.uuid4().hex}")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        NativeSkillArchive.extract(content, slug, temporary)
        if draft_dir.exists():
            shutil.rmtree(draft_dir)
        temporary.replace(draft_dir)
        with self._store.connection() as conn:
            conn.execute("INSERT INTO skills(id,slug,name,description,created_by) VALUES (?,?,?,?,?) ON CONFLICT(slug) DO UPDATE SET name=excluded.name,description=excluded.description", (skill_id, slug, name, description, user_id))
            status = "published" if published else "draft"
            if existing:
                conn.execute("UPDATE skill_versions SET checksum=?,created_by=? WHERE id=? AND status='draft'", (inspection["sha256"], user_id, version_id))
                conn.execute("UPDATE skill_packages SET storage_path=?,sha256=?,size_bytes=? WHERE skill_version_id=?", (str(package_path), inspection["sha256"], len(content), version_id))
            else:
                conn.execute("INSERT INTO skill_versions(id,skill_id,version,status,checksum,created_by,published_at) VALUES (?,?,?,?,?,?,CASE WHEN ?='published' THEN CURRENT_TIMESTAMP ELSE NULL END)", (version_id, skill_id, version, status, inspection["sha256"], user_id, status))
                conn.execute("INSERT INTO skill_packages(id,skill_version_id,storage_path,sha256,size_bytes) VALUES (?,?,?,?,?)", (str(uuid.uuid4()), version_id, str(package_path), inspection["sha256"], len(content)))
        if published:
            self._ensure_published_files(slug, version, content)
        return {**self.version(version_id), "inspection": inspection}

    def _ensure_published_files(self, slug: str, version: str, content: bytes) -> Path:
        target = self.published_root / slug / version
        if target.exists():
            return target
        temporary = target.with_name(f"{target.name}.tmp-{uuid.uuid4().hex}")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        NativeSkillArchive.extract(content, slug, temporary)
        temporary.replace(target)
        return target

    def version(self, version_id: str) -> dict:
        with self._store.connection() as conn:
            row = conn.execute("SELECT v.*,s.slug,s.name,p.storage_path,p.size_bytes FROM skill_versions v JOIN skills s ON s.id=v.skill_id JOIN skill_packages p ON p.skill_version_id=v.id WHERE v.id=?", (version_id,)).fetchone()
        if not row:
            raise LookupError("Skill version 不存在。")
        return dict(row)

    def test_version(self, version_id: str) -> dict:
        item = self.version(version_id)
        content = Path(item["storage_path"]).read_bytes()
        inspection = NativeSkillArchive.inspect(content, item["slug"])
        if inspection["sha256"] != item["checksum"]:
            raise SkillRegistryError("Skill package checksum 不匹配。")
        return {"status": "passed", **inspection}

    def publish(self, version_id: str, user_id: str) -> dict:
        item = self.version(version_id)
        if item["status"] == "deprecated":
            raise SkillRegistryError("已废弃版本不能重新发布。")
        if item["status"] == "published":
            return self._public_version(item)
        content = Path(item["storage_path"]).read_bytes()
        self.test_version(version_id)
        self._ensure_published_files(item["slug"], item["version"], content)
        with self._store.connection() as conn:
            conn.execute("UPDATE skill_versions SET status='published',published_at=CURRENT_TIMESTAMP WHERE id=? AND status='draft'", (version_id,))
        return self._public_version(self.version(version_id))

    def deprecate(self, version_id: str) -> dict:
        item = self.version(version_id)
        if item["status"] == "draft":
            raise SkillRegistryError("草稿版本不能直接废弃。")
        with self._store.connection() as conn:
            bound = conn.execute("SELECT 1 FROM agent_skill_bindings WHERE skill_version_id=? LIMIT 1", (version_id,)).fetchone()
            if bound:
                raise SkillRegistryError("该版本仍绑定 Agent，不能废弃。")
            conn.execute("UPDATE skill_versions SET status='deprecated',deprecated_at=CURRENT_TIMESTAMP WHERE id=?", (version_id,))
        return self._public_version(self.version(version_id))

    def bind_agent(self, agent_id: str, slug: str, version: str, user_id: str | None) -> dict:
        if agent_id not in CATALOG:
            raise LookupError("Agent Template 不存在。")
        with self._store.connection() as conn:
            row = conn.execute("SELECT s.id AS skill_id,v.id AS version_id FROM skills s JOIN skill_versions v ON v.skill_id=s.id WHERE s.slug=? AND v.version=? AND v.status='published'", (slug, version)).fetchone()
            if not row:
                raise SkillRegistryError("只能绑定已发布的 Skill version。")
            conn.execute("INSERT INTO agent_skill_bindings(agent_id,skill_id,skill_version_id,updated_by) VALUES (?,?,?,?) ON CONFLICT(agent_id,skill_id) DO UPDATE SET skill_version_id=excluded.skill_version_id,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP", (agent_id, row["skill_id"], row["version_id"], user_id))
            bindings = conn.execute("SELECT s.slug,v.version FROM agent_skill_bindings b JOIN skills s ON s.id=b.skill_id JOIN skill_versions v ON v.id=b.skill_version_id WHERE b.agent_id=? AND v.status='published' ORDER BY s.slug", (agent_id,)).fetchall()
            manifest = {item["slug"]: item["version"] for item in bindings}
            conn.execute("UPDATE agent_templates SET skill_manifest=? WHERE id=?", (json.dumps(manifest, sort_keys=True), agent_id))
        return {"agent_id": agent_id, "skill_manifest": manifest}

    def manifest_for_agent(self, agent_id: str) -> dict[str, str]:
        with self._store.connection() as conn:
            rows = conn.execute("SELECT s.slug,v.version FROM agent_skill_bindings b JOIN skills s ON s.id=b.skill_id JOIN skill_versions v ON v.id=b.skill_version_id WHERE b.agent_id=? AND v.status='published' ORDER BY s.slug", (agent_id,)).fetchall()
        return {row["slug"]: row["version"] for row in rows} or dict(CATALOG[agent_id].skill_manifest)

    def list_skills(self) -> list[dict]:
        with self._store.connection() as conn:
            skills = conn.execute("SELECT * FROM skills ORDER BY slug").fetchall()
            versions = conn.execute("SELECT id,skill_id,version,status,checksum,created_at,published_at,deprecated_at FROM skill_versions ORDER BY created_at DESC").fetchall()
            bindings = conn.execute("SELECT b.agent_id,s.slug,v.version FROM agent_skill_bindings b JOIN skills s ON s.id=b.skill_id JOIN skill_versions v ON v.id=b.skill_version_id ORDER BY b.agent_id,s.slug").fetchall()
        by_skill: dict[str, list[dict]] = {}
        for version in versions:
            by_skill.setdefault(version["skill_id"], []).append(dict(version))
        by_slug: dict[str, list[dict]] = {}
        for binding in bindings:
            by_slug.setdefault(binding["slug"], []).append(dict(binding))
        return [{**dict(skill), "versions": by_skill.get(skill["id"], []), "bindings": by_slug.get(skill["slug"], [])} for skill in skills]

    def is_platform_admin(self, user_id: str) -> bool:
        with self._store.connection() as conn:
            return bool(conn.execute("SELECT 1 FROM platform_admins WHERE user_id=?", (user_id,)).fetchone())

    def grant_platform_admin(self, user_id: str) -> None:
        with self._store.connection() as conn:
            conn.execute("INSERT OR IGNORE INTO platform_admins(user_id) VALUES (?)", (user_id,))
