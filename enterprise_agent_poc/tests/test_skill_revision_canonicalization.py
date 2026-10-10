"""Real unchanged 13-file fixture; no Skill execution/Settings/Provider imports."""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import random
import subprocess
import unittest
from unittest.mock import patch

from app import wechat_skill as adapter
from app.skill_revision_identity import (
    CANONICALIZATION_VERSION, METADATA_FIELDS, RevisionIdentityError,
    canonical_files, canonical_path, canonical_revision, parse_revision,
    verify_revision_identity,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / 'tests/fixtures/wechat_revision_linux_order.v1.json').read_bytes())
DECLARATION = ROOT / 'integrations/wechat-html-draft.revision.v1.json'


class ObservedFile:
    """Model the *real* platform Path comparator; bytes come from real Source."""
    def __init__(self, name, platform):
        self.name = name
        self.actual = adapter.SOURCE / name
        self.order_key = (PureWindowsPath if platform == 'windows' else PurePosixPath)(name)
    def __lt__(self, other): return self.order_key < other.order_key
    def is_symlink(self): return self.actual.is_symlink()
    def is_file(self): return self.actual.is_file()
    def read_bytes(self): return self.actual.read_bytes()
    def relative_to(self, root): return PurePosixPath(self.name)


class ObservedSource:
    def __init__(self, names, platform): self.names, self.platform = names, platform
    def rglob(self, pattern): return [ObservedFile(name, self.platform) for name in self.names]


def legacy_verifier(source):
    """Execute exact old Git blob functions, never a rewritten imitation.

    Linux archive harness may supply the local shared Git object directory;
    commit/module SHA is fixed in the checked-in fixture, no network/fetch.
    """
    git_dir = os.environ.get('CANONICAL_TEST_LEGACY_GIT_DIR')
    args = ['git', *(['--git-dir', git_dir] if git_dir else []), 'show',
            FIXTURE['legacy_source'] + ':enterprise_agent_poc/app/wechat_skill.py']
    raw = subprocess.check_output(args, cwd=ROOT.parent)
    if hashlib.sha256(raw).hexdigest() != FIXTURE['legacy_module_sha256']:
        raise AssertionError('LEGACY_FIXTURE_SOURCE_MISMATCH')
    names = {'source_files', 'native_package', 'revision_contract', 'verify_revision_contract'}
    nodes = [node for node in ast.parse(raw).body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {key: getattr(adapter, key) for key in ('SLUG', 'VERSION', 'NAME', 'ORIGINAL_SHA', 'LOCK', 'AGENT_SLUG', 'digest', 'WechatSkillError')}
    namespace.update(Path=Path, json=json, SOURCE=source, __file__=adapter.__file__)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'pinned-4ca-wechat-identity.py', 'exec'), namespace)
    return namespace


class CanonicalizationTests(unittest.TestCase):
    def setUp(self):
        self.raw = DECLARATION.read_bytes()
        self.declaration = json.loads(self.raw)
        self.entries = self.declaration['files']
        self.names = [entry['path'] for entry in self.entries]
        self.canonical = canonical_revision(self.declaration)

    def assert_permutation(self, entries, platform='linux'):
        value = copy.deepcopy(self.declaration); value['files'] = entries
        self.assertEqual(self.canonical, canonical_revision(value))
        verify_revision_identity(self.declaration, value)
        observed = adapter.revision_contract(ObservedSource([entry['path'] for entry in entries], platform))
        verify_revision_identity(self.declaration, observed)
        self.assertEqual(observed['artifact_sha256'], FIXTURE['artifact_sha256'])

    def test_01_windows_path_order(self):
        self.assert_permutation(sorted(self.entries, key=lambda entry: PureWindowsPath(entry['path'])), 'windows')

    def test_02_linux_lexical_order(self):
        entries = sorted(self.entries, key=lambda entry: PurePosixPath(entry['path']))
        self.assertEqual([entry['path'] for entry in entries], FIXTURE['linux_file_order'])
        self.assert_permutation(entries)

    def test_03_reverse_order(self): self.assert_permutation(list(reversed(self.entries)))

    def test_04_deterministic_random_permutations(self):
        rng = random.Random(20261007)
        for _ in range(32):
            entries = copy.deepcopy(self.entries); rng.shuffle(entries)
            self.assert_permutation(entries)

    def test_05_skill_first(self):
        self.assert_permutation(sorted(self.entries, key=lambda entry: entry['path'] != 'SKILL.md'))

    def test_06_skill_last(self):
        self.assert_permutation(sorted(self.entries, key=lambda entry: entry['path'] == 'SKILL.md'))

    def test_07_seal_metadata_before_file_inventory(self):
        value = {field: self.declaration[field] for field in METADATA_FIELDS}
        value['files'] = list(reversed(self.entries))
        self.assertEqual(self.canonical, canonical_revision(parse_revision(json.dumps(value).encode())))

    def test_08_seal_metadata_after_file_inventory(self):
        value = dict(files=list(reversed(self.entries)))
        value.update({field: self.declaration[field] for field in reversed(METADATA_FIELDS)})
        self.assertEqual(self.canonical, canonical_revision(parse_revision(json.dumps(value).encode())))

    def test_09_pinned_old_source_linux_reject_new_pass(self):
        linux = ObservedSource(FIXTURE['linux_file_order'], 'linux')
        old = legacy_verifier(linux)
        self.assertEqual(old['revision_contract']()['files'][0]['path'], 'SKILL.md')
        with self.assertRaisesRegex(adapter.WechatSkillError, 'REVISION_IDENTITY_BLOCKED'):
            old['verify_revision_contract']()
        observed = adapter.revision_contract(linux)
        with patch.object(adapter, 'revision_contract', return_value=observed):
            self.assertEqual(adapter.verify_revision_contract(), self.declaration)

    def test_10_old_windows_evidence_still_passes(self):
        old = legacy_verifier(ObservedSource(self.names, 'windows'))
        self.assertEqual(old['verify_revision_contract'](), self.declaration)
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(), FIXTURE['declaration_sha256'])

    def mismatch(self, change):
        observed = copy.deepcopy(self.declaration); change(observed)
        with self.assertRaises(RevisionIdentityError): verify_revision_identity(self.declaration, observed)
        with patch.object(adapter, 'revision_contract', return_value=observed):
            with self.assertRaisesRegex(adapter.WechatSkillError, 'REVISION_IDENTITY_BLOCKED'):
                adapter.verify_revision_contract()

    def test_11_changed_hash(self): self.mismatch(lambda value: value['files'][0].update(sha256='0'*64))
    def test_12_missing_file(self): self.mismatch(lambda value: value['files'].pop())
    def test_13_extra_file(self):
        self.mismatch(lambda value: value['files'].append(dict(path='extra.txt', sha256='1'*64, git_mode='100644')))
    def test_14_changed_path(self): self.mismatch(lambda value: value['files'][0].update(path='agents/renamed.yaml'))
    def test_15_changed_case(self): self.mismatch(lambda value: value['files'][0].update(path='Agents/openai.yaml'))
    def test_16_changed_seal_metadata(self): self.mismatch(lambda value: value.update(upload_gate='RELAXED'))
    def test_17_changed_artifact_sha(self): self.mismatch(lambda value: value.update(artifact_sha256='0'*64))
    def test_18_changed_dependency_pin(self): self.mismatch(lambda value: value.update(dependencies_sha256='0'*64))
    def test_19_changed_git_mode(self): self.mismatch(lambda value: value['files'][0].update(git_mode='100755'))
    def test_20_metadata_lists_not_made_unordered(self): self.mismatch(lambda value: value['forbidden_actions'].reverse())

    def test_21_artifact_and_all_thirteen_hashes_unchanged(self):
        artifact = ROOT / 'integrations/artifacts/wechat-html-draft-1.0.0.zip'
        self.assertEqual(hashlib.sha256(artifact.read_bytes()).hexdigest(), FIXTURE['artifact_sha256'])
        self.assertEqual(adapter.native_package(), artifact.read_bytes())
        self.assertEqual(len(self.entries), 13)
        self.assertEqual(set(adapter.source_files()), set(self.names))
        for entry in self.entries:
            self.assertEqual(hashlib.sha256((adapter.SOURCE / entry['path']).read_bytes()).hexdigest(), entry['sha256'])

    def test_22_original_runtime_lock_pin_unchanged(self):
        descriptor = json.loads((ROOT / 'integrations/wechat-python-runtime.v1.json').read_bytes())
        raw = (ROOT / descriptor['lock_file']).read_bytes()
        self.assertEqual(descriptor['lock_sha256'], FIXTURE['runtime_lock_sha256'])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), FIXTURE['runtime_lock_sha256'])
        self.assertNotEqual(hashlib.sha256(raw+b' ').hexdigest(), FIXTURE['runtime_lock_sha256'])

    def test_23_no_dot_or_absolute_aliases(self):
        for name in ('./SKILL.md','a/../SKILL.md','a//file.md','/SKILL.md','C:/SKILL.md','a/./file.md',r'..\SKILL.md',r'\\host\SKILL.md','a/','a/NUL\x00.md'):
            with self.subTest(name=name), self.assertRaises(RevisionIdentityError): canonical_path(name)

    def test_24_windows_relative_separators_canonicalized(self):
        self.assertEqual(canonical_path(r'agents\openai.yaml'), 'agents/openai.yaml')
        value = copy.deepcopy(self.declaration)
        for entry in value['files']: entry['path'] = entry['path'].replace('/', '\\')
        verify_revision_identity(self.declaration, value)

    def test_25_duplicate_paths_not_collapsed(self):
        entries = copy.deepcopy(self.entries); entries.append(dict(entries[0]))
        with self.assertRaises(RevisionIdentityError): canonical_files(entries)
        entries[-1]['path'] = entries[-1]['path'].replace('/', '\\')
        with self.assertRaises(RevisionIdentityError): canonical_files(entries)

    def test_26_unicode_exact_no_case_or_composition_fold(self):
        self.assertEqual(canonical_path('资料/é.md'), '资料/é.md')
        self.assertNotEqual(canonical_path('é.md'), canonical_path('e\u0301.md'))
        self.assertNotEqual(canonical_path('SKILL.md'), canonical_path('skill.md'))
        entries = [dict(path=name,sha256='1'*64,git_mode='100644') for name in ('资料.md','é.md','A.md','a.md')]
        self.assertEqual([entry['path'] for entry in canonical_files(entries)], sorted((e['path'] for e in entries), key=lambda name: name.encode('utf-8')))
        with self.assertRaises(RevisionIdentityError): canonical_path('\ud800.md')

    def test_27_duplicate_json_and_unknown_fields_rejected(self):
        duplicate = self.raw.rstrip()[:-1] + b',"version":"1.0.0"}'
        with self.assertRaises(RevisionIdentityError): parse_revision(duplicate)
        value = copy.deepcopy(self.declaration); value['unknown'] = 'not ignored'
        with self.assertRaises(RevisionIdentityError): canonical_revision(value)

    def test_28_entry_schema_and_hash_are_strict(self):
        for extra in (dict(sha256='f'*63), dict(sha256='F'*64), dict(git_mode='0777'), dict(path='../other'), dict(extra=True)):
            entries = copy.deepcopy(self.entries); entries[0].update(extra)
            with self.assertRaises(RevisionIdentityError): canonical_files(entries)

    def test_29_no_mutation_and_legacy_representation_returned(self):
        before = copy.deepcopy(self.declaration); canonical_revision(self.declaration)
        self.assertEqual(self.declaration, before)
        self.assertEqual(adapter.verify_revision_contract(), before)
        self.assertEqual(DECLARATION.read_bytes(), self.raw)
        self.assertEqual(CANONICALIZATION_VERSION, 'native-revision-v1-order-independent')

    def test_30_real_current_platform_source_verification(self):
        # On Linux this exercises actual PosixPath/rglob with no Path mock.
        self.assertEqual(adapter.verify_revision_contract(), self.declaration)
        self.assertEqual([entry['path'] for entry in adapter.revision_contract()['files']], FIXTURE['linux_file_order'])


if __name__ == '__main__': unittest.main()
