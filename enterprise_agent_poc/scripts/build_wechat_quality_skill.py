"""Build only the new immutable Native package using the existing ZIP format."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.bundled_skills import deterministic_zip


def main():
    slug, version = "wechat-official-account-writing", "1.0.1"
    folder = ROOT / "skill_packages" / slug / version
    files = {path.relative_to(folder).as_posix(): (path.read_bytes(), "100644")
             for path in sorted(folder.rglob("*")) if path.is_file()}
    package = deterministic_zip(slug, version, files)
    target = folder.parent / (version + ".zip")
    if target.exists() and target.read_bytes() != package:
        raise SystemExit("IMMUTABLE_PACKAGE_ALREADY_EXISTS_WITH_OTHER_BYTES")
    target.write_bytes(package)
    print(json.dumps({"skill_slug": slug, "version": version,
        "source_identity": {"path": f"{slug}/{version}", "files": [
            {"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "git_mode": mode} for name, (raw, mode) in files.items()]},
        "artifact_sha256": hashlib.sha256(package).hexdigest(), "artifact_path": f"{slug}/{version}.zip",
        "bootstrap_default": [], "builder_policy": "native-zip-stored-v1", "legacy_artifact": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
