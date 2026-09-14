from contextlib import contextmanager
import hashlib
import json
from pathlib import Path

import pytest

from scripts import migrate


def migration_item(path: Path) -> dict:
    return {
        "version": path.name[:3],
        "name": path.name[4:],
        "migration": path.name,
        "path": path,
        **migrate.migration_checksums(path),
    }


def write_migration(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    return path


def test_lf_crlf_and_isolated_cr_have_the_same_canonical_checksum(tmp_path):
    lf = write_migration(tmp_path / "lf.sql", b"SELECT 1;\nSELECT 2;\n")
    crlf = write_migration(tmp_path / "crlf.sql", b"SELECT 1;\r\nSELECT 2;\r\n")
    cr = write_migration(tmp_path / "cr.sql", b"SELECT 1;\rSELECT 2;\r")

    checksums = {migrate.checksum(path) for path in (lf, crlf, cr)}

    assert len(checksums) == 1


def test_legacy_crlf_checksum_is_compatible_with_lf_checkout(tmp_path):
    path = write_migration(tmp_path / "001_example.sql", b"SELECT 1;\n")
    checksums = migrate.migration_checksums(path)

    assert checksums["current_raw_checksum"] == checksums["canonical_checksum"]
    assert migrate.compatibility_status(checksums["legacy_crlf_checksum"], checksums) == migrate.LEGACY_LINE_ENDING_COMPATIBLE


@pytest.mark.parametrize(
    ("baseline_content", "changed"),
    [
        (b"SELECT 1;\n", b"SELECT  1;\n"),
        (b"SELECT 1;\n", b"SELECT1;\n"),
        (b"-- original comment\nSELECT 1;\n", b"-- changed comment\nSELECT 1;\n"),
        (b"SELECT '1';\n", b"SELECT '2';\n"),
    ],
    ids=("extra-space", "missing-space", "comment-change", "string-change"),
)
def test_non_line_ending_changes_remain_checksum_mismatches(tmp_path, baseline_content, changed):
    baseline = write_migration(tmp_path / "baseline.sql", baseline_content)
    stored = migrate.migration_checksums(baseline)["legacy_crlf_checksum"]
    candidate = write_migration(tmp_path / "candidate.sql", changed)

    assert migrate.compatibility_status(stored, migrate.migration_checksums(candidate)) == migrate.CHECKSUM_MISMATCH


@pytest.mark.parametrize(
    "names",
    [
        ("001_first.sql", "003_third.sql"),
        ("001_first.sql", "001_duplicate.sql"),
    ],
    ids=("gap", "duplicate-version"),
)
def test_migration_file_order_anomalies_block(tmp_path, names):
    for name in names:
        write_migration(tmp_path / name, b"SELECT 1;\n")

    with pytest.raises(RuntimeError, match="迁移文件顺序异常"):
        migrate.migration_files(tmp_path)


def test_missing_migration_files_blocks(tmp_path):
    with pytest.raises(RuntimeError, match="未找到任何迁移文件"):
        migrate.migration_files(tmp_path)


def test_unknown_database_migration_blocks_status(monkeypatch, tmp_path, capsys):
    path = write_migration(tmp_path / "001_first.sql", b"SELECT 1;\n")
    item = migration_item(path)
    applied = {
        "001": {"version": "001", "name": item["name"], "checksum": item["current_raw_checksum"], "applied_at": "now"},
        "999": {"version": "999", "name": "unknown.sql", "checksum": "unknown", "applied_at": "now"},
    }
    monkeypatch.setattr(migrate, "state", lambda _store: ([item], applied))

    assert migrate.status(object()) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["unknown_history_versions"] == ["999"]
    assert result["unknown_migrations"][0]["compatibility_status"] == migrate.UNKNOWN


def test_unknown_database_migration_blocks_up(monkeypatch, tmp_path):
    path = write_migration(tmp_path / "001_first.sql", b"SELECT 1;\n")
    item = migration_item(path)
    applied = {
        "001": {"version": "001", "name": item["name"], "checksum": item["current_raw_checksum"], "applied_at": "now"},
        "999": {"version": "999", "name": "unknown.sql", "checksum": "unknown", "applied_at": "now"},
    }
    monkeypatch.setattr(migrate, "state", lambda _store: ([item], applied))

    with pytest.raises(RuntimeError, match="未知的迁移版本"):
        migrate.up(object())


def test_new_code_migration_is_reported_pending(tmp_path):
    path = write_migration(tmp_path / "001_first.sql", b"SELECT 1;\n")

    record = migrate.migration_records([migration_item(path)], {})[0]

    assert record["status"] == "pending"
    assert record["compatibility_status"] == migrate.PENDING


def test_legacy_line_ending_match_does_not_reexecute_migration(monkeypatch, tmp_path, capsys):
    path = write_migration(tmp_path / "001_first.sql", b"SELECT 1;\n")
    item = migration_item(path)
    applied = {
        "001": {
            "version": "001",
            "name": item["name"],
            "checksum": item["legacy_crlf_checksum"],
            "applied_at": "now",
        }
    }
    monkeypatch.setattr(migrate, "state", lambda _store: ([item], applied))

    assert migrate.up(object()) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "ok", "applied": []}


def test_real_sql_change_blocks_up_even_when_line_endings_are_compatible(monkeypatch, tmp_path):
    baseline = write_migration(tmp_path / "baseline.sql", b"SELECT 'original';\n")
    stored = migrate.migration_checksums(baseline)["legacy_crlf_checksum"]
    candidate = write_migration(tmp_path / "001_first.sql", b"SELECT 'changed';\n")
    item = migration_item(candidate)
    applied = {"001": {"version": "001", "name": item["name"], "checksum": stored, "applied_at": "now"}}
    monkeypatch.setattr(migrate, "state", lambda _store: ([item], applied))

    with pytest.raises(RuntimeError, match="checksum 不匹配"):
        migrate.up(object())


def test_new_migration_records_canonical_checksum(monkeypatch, tmp_path, capsys):
    path = write_migration(tmp_path / "001_first.sql", b"SELECT 1;\r\n")
    item = migration_item(path)
    monkeypatch.setattr(migrate, "state", lambda _store: ([item], {}))

    class Connection:
        def __init__(self):
            self.calls = []

        def execute(self, statement, params=None):
            self.calls.append((statement, params))

    class Store:
        def __init__(self):
            self.connection_instance = Connection()

        @contextmanager
        def connection(self):
            yield self.connection_instance

    store = Store()

    assert migrate.up(store) == 0
    capsys.readouterr()
    insert = next(call for call in store.connection_instance.calls if call[0].startswith("INSERT INTO schema_migrations"))
    assert insert[1][2] == item["canonical_checksum"]
    assert insert[1][2] != item["current_raw_checksum"]


def test_repository_migrations_match_legacy_production_crlf_checksums():
    expected = {
        "001": "8872cf35cb070c885fc9fc486e75e49a014ddc2f61a088ceca981a50208cb4d7",
        "002": "b2106089644f8fce35fedf0ad220aedbafc9020b70da3ab7d9f4edd215d17b91",
        "003": "79c9840b4d6d46353b94bb3aed3b09f546fe7fdc15c9bd04acecd475d968fcfd",
        "004": "79c03968ae3f50384cdeb28b8d3b7f17fa435933a00fde8bffc8dc28cbf86a74",
        "005": "40486c0e59f337de0faaead1d57008026ac81f11c7d354e36143185a6eb89732",
        "006": "ae1db2a4428aa38762fef98d189f23295fd1241a3d57a6ca756d1c92285cb0e7",
        "007": "d051a826539acbd4e39f326bfde982f60bbf78e9944c7895a6e59e9e7e099abb",
    }

    checksums = {path.name[:3]: migrate.migration_checksums(path) for path in migrate.migration_files()}
    # Historical production checksums stay strict when expand-only migrations
    # are appended. New migrations have no historical production checksum yet.
    assert expected.keys() <= checksums.keys()
    actual = {version: checksums[version]["legacy_crlf_checksum"] for version in expected}

    assert actual == expected
    assert {
        migrate.compatibility_status(expected[version], checksums[version])
        for version in expected
    } == {migrate.LEGACY_LINE_ENDING_COMPATIBLE}


def test_migrations_001_through_011_bytes_remain_unchanged():
    expected = {
        "001": "ab4c74a873073feab7cc9a264a74984be3b135e3d8aef75d343bcd8f2e1c94ce",
        "002": "afafa855c74ff6f2a1c1e31765ce7396e5b5c35950bbb1b36ec2095c0efdce6c",
        "003": "62369e85286131cc0884ec0710bc012345051af263313b9cba2c9c35b81a9480",
        "004": "5b15bf5e86b4508bd03170867fc2dc3027c98004bf3b2252ffba1720b252a802",
        "005": "31564e792fa4134b7077a777158d13d586a7484933adb42c69cc2f6f9ce3da19",
        "006": "b33fc2c9bd1140a0efdf2d05fc10cb829e87661f044edf3de54c267dcf8dd0b2",
        "007": "4ea2b5cc53fb266fb9031741b74e7375c34e118ac13e2c102854fad539cb29e0",
        "008": "bd0c153e249036f57e62c749f59b04ba21ad43f7db492895b2a0600a0ca68add",
        "009": "97aafc386db3cdc4c31de149466f55bb508415a3987e6892351802c1fa138884",
        "010": "2be29da24a3aa20c0e302ce7dc505b28c2d0ffae25d80c4458f7223acaa04b10",
        "011": "e2c433fdde86f4e49b37538c6917c8ea0e7ad6017e8451e7e544583da0014cde",
    }
    actual = {
        path.name[:3]: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in migrate.migration_files()[:11]
    }
    assert actual == expected
