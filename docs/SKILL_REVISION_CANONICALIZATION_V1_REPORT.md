# Cross-Platform Skill Revision Identity Canonicalization V1

## Decision / scope

`SKILL_REVISION_CANONICALIZATION_READY` — successor Source and deterministic
Windows/Linux parity verification, **not fresh PRIMARY acceptance**.

Parent Source: `4ca21336bdd17a507d819457c2fc41c5e410846b`.
Parent Tree: `945dc8bf115ed11f619d514647a813696f4e6291`.
Branch: `codex/skill-revision-canonicalization-v1`.
Clean managed worktree:
`C:/Users/猪猪/.codex/worktrees/skill-revision-canonicalization-v1/ky_web`.
The original Windows workspace and its dirty reports/configuration are preserved.
Final CANONICAL_SOURCE/TREE are recorded in the post-commit handoff and new
external `canonical-source-seal.json`, not circularly inserted into this commit.

PRIMARY/Production changes=0; real Provider/Image/WeChat calls=0/0/0.
No PRIMARY connection, switch, admin/Agent creation, Revision registration,
venv/package installation, Binding activation, actual PREPARE or deployment.
Local tests use disposable synthetic SQLite, runtime/HTTP/process doubles;
there is no new live execution or authority grant.

## Verified root cause

`wechat_skill.source_files` used `sorted(source.rglob('*'))`: it did sort, but
sorted platform-specific **Path objects**, not canonical relative strings.
Windows Path comparison ignores case, placing `SKILL.md` after lower-case
directories; PosixPath comparison is case-sensitive, placing it first.
`verify_revision_contract` then compared Python dictionaries whose `files` values
are **position-sensitive lists**. Identical path/hash/mode inventory therefore
failed only because list order differed.

The unchanged ZIP writer already sorts names by `name.encode('utf-8')`; ZIP
identity does not depend on this Path comparison. No ZIP reorder/repack is needed.

The external sealed declaration is the Native Revision JSON containing metadata
and a `files` inventory. It is **not a special file entry** whose list position
should be authority. The last entry is ordinary `SKILL.md`. JSON metadata key
position was not the content drift; the file list comparison was the defect.
The new parser separates these two semantic parts explicitly.

## Canonicalization rule — normative

Verifier input rule: `CANONICALIZATION_VERSION = native-revision-v1-order-independent`.
Persisted identity contract: **unchanged Native Revision v1**.

1. Collector derives relative paths with `relative_to(source).as_posix()`.
   No absolute-path substring removal, inode/locale/ZIP order authority.
2. Canonical relative path uses `/`. Windows relative separators may normalize
   to `/`; absolute, drive/UNC, dot/dot-dot, repeated separator, trailing
   separator, unsafe/forbidden project paths and invalid UTF-8 are rejected.
3. Unicode follows the existing bundled-path identity rule: preserve exact
   original Unicode code points. **No NFC/NFKC/composition or case folding**.
   Composed/decomposed spellings and case changes remain distinct identities.
4. Sort file entries by strict UTF-8 bytewise canonical relative path. Never
   sort Path objects or trust filesystem/ZIP natural enumeration order.
5. Every file entry is exactly `path`, lower-case 64-hex `sha256`, `git_mode`
   (`100644`/`100755`). Duplicate canonical paths, malformed entries, missing
   and extra files are not collapsed or silently discarded.
6. Seal metadata is read by the following fixed field sequence, independently
   of its physical JSON position:

   `slug`, `name`, `version`, `registry_revision_field`, `source_type`,
   `original_zip_sha256`, `source_root`, `artifact_sha256`, `entrypoint`,
   `windows_helper`, `dependencies_sha256`, `actions`, `permissions`,
   `upload_gate`, `forbidden_actions`, `proposed_binding`.

7. Nested metadata map keys use UTF-8 bytewise ordering; metadata **list order,
   types and values remain exact**. Only the file inventory is unordered.
   Unknown fields, duplicate JSON keys and non-finite/unencodable metadata fail
   closed. No metadata, checksum, mode or permission is omitted from comparison.
8. Canonical comparison bytes use compact strict UTF-8 JSON of separate
   metadata/file parts. These are **internal comparison bytes**, not a new
   Registry checksum or a replacement hash in an old receipt.

## Backward compatibility / no identity bump required

There was no separate persisted hash of the old Python-list comparison result.
Existing identity evidence uses exact Artifact SHA, original declaration bytes,
Runtime descriptor/lock, exact Registry UUID/version and sealed Runtime binding.
Those values and algorithms are unchanged; only an incidental input-list
ordering assumption is removed. Therefore no v2 identity/schema/receipt migration
is introduced, and no old Source or historical seal is rewritten.

`verify_revision_contract()` returns the **original parsed declaration**, including
its legacy file-list order; it never returns the comparison view in its place.
An old receipt is not silently recalculated or overwritten. The unchanged e7
Runtime verifier still validates old Runtime binding/receipt/lock rules.

Pinned old 4ca verifier functions are loaded from the actual Git blob, SHA
`539fa93a3c85628803ad0d30759a9aad8c1d5f44480c04f8982acd2dd401a0c6`.
They still accept the original Windows-style declaration/evidence and reproduce
the Linux rejection. The new verifier accepts the same content under every
permutation. This is compatible correction of v1 comparison inputs, not a new
stored identity. If comparison bytes ever become a stored identity in future,
that change requires explicit versioning and old/new verifiers; this task does
not authorize it.

## Immutable identity evidence

| Evidence | Unchanged SHA256 |
| --- | --- |
| Native Skill ZIP | `4a140c878ae7057583089a4410cd1a23c5c95664988e6dd9700f75ed51d3b18c` |
| Runtime Lock | `3a5f482475c7cafe660df2d171f369a32832ac91e12e9b0ddffde5c5d6ac8abe` |
| Native Revision declaration bytes | `0329daa05bfaff79c158807364d88003d3c1fdc88ebdf245d8bfa11a6bbfd490` |
| Old e7 Runtime descriptor | `1b7d02d983867a815c898a94944bf3660597fd513d1bf0310608100f9b70e769` |

All **13 file path/hash/git-mode entries match** the unchanged declaration.
The stored ZIP is unchanged; deterministic in-memory reconstruction still
matches its bytes exactly. No binary is rewritten to adapt enumeration order.
Runtime Lock byte drift is still rejected by the original runtime tests.

## Changed files

- `app/skill_revision_identity.py`: strict canonical path/inventory/metadata
  view, duplicate-aware parser, semantic verifier.
- `app/wechat_skill.py`: ONLY `source_files` collection order and
  `verify_revision_contract` parsing/comparison; all other business/permission/
  binding/package-builder logic is AST-identical to 4ca.
- `tests/test_skill_revision_canonicalization.py` and
  `tests/fixtures/wechat_revision_linux_order.v1.json`: pinned old Source + real
  unchanged 13-file Linux-oriented fixture and positive/negative regression.
- `tests/test_skill_only_test_qualification.py`: existing static invariant now
  allows exactly those two identity functions; full remaining Skill module AST
  and all old Runtime/Provider/Secret byte invariants stay protected.
- `scripts/verify_skill_revision_canonicalization.py`,
  `scripts/seal_skill_revision_canonicalization.py`: targeted verifier and new
  post-commit Source evidence, no install/activation authority.
- `tests/runtime_identity_fixture.py`, one import in
  `tests/test_skill_python_runtime.py`, and runtime-only branch of
  `scripts/verify_wechat_skill.py`: standard-library-only scratch fixture with
  the same directory/containment rules; remove an unrelated business-program
  import before runtime-only unit tests. No runtime test body/probe/receipt gate
  or dependency version is changed.
- this report and appended follow-ups in the requested qualification/PRIMARY
  acceptance reports.

Qualification semantics, Controlled Action, Agent eligibility, Runtime lifecycle
and dependency code, Secret contract, Skill Source/ZIP/lock/old manifests and
Production tooling/configuration are byte-identical to parent 4ca. Tests and
verification entry points are explicitly distinct from those product boundaries.

## Regression results

Final targeted results; no Full pytest, Stage2, Candidate Build or PRIMARY:

| Suite | Windows | Linux local parity |
| --- | ---: | ---: |
| canonicalization / real inventory / pinned old verifier | 30 PASS | 30 PASS |
| Qualification / Controlled / Dispatch | 91 PASS | 91 PASS |
| Artifact / WeChat adaptation / permission | 66 PASS | not required / not executed |
| old Revision Runtime / Lock / receipt | 29 PASS | 29 PASS |
| ordinary eligibility / Runtime lifecycle | not executed | 93 PASS, 2 existing warnings |

**309 unique cases; final 0 failed / 0 errors / 0 skips**. Cross-platform repeated
executions are not counted as additional unique cases. The 91 contain
27 Qualification, 33 Controlled, 31 Dispatch. The 66 already contain the original
17 retained workflow cases, which are not added again.

Windows canonical/Artifact/Runtime used existing Python3.12.14; MCP suites used
existing Python3.13.0/MCP1.30.0. Local WSL Ubuntu22.04 parity used existing
Python3.11.16, pytest8.4.2 and retained main venv; no dependency installation or
venv modification. Only explicitly requested targeted parity was run there.

The final Linux tests use a canonical Git Source archive, not mutable NTFS file
modes: code tree `d6536e496cd80410f43d7963957451d60c13b9cf`, tar SHA
`18f2495473e3df20adf3ad6bd946afaefd00eba4cf5253736a2364ce2d8e45e9`.
All final executable changes are represented there; later additions are reports.
The archive is Source parity evidence, **not a Skill repack or Candidate**.
Final Linux 93 took 169.02s. Warnings are existing Starlette/httpx and anyio
deprecations, not new failures.

A–H explicitly covered: Windows Path order, Linux lexical order, reverse,
32 seeded random permutations, `SKILL.md` first/last, metadata before/after files.
Negative tests still reject hash/missing/extra/path/case/mode/metadata/dependency/
Artifact drift, duplicates/unknown fields/path aliases; Runtime Lock mutation
remains blocked by the unchanged resolver. Actual Linux setup of all three
Qualification/Controlled/Dispatch classes reaches **91 tests with 0 setup
errors**, closing the prior ordering defect in this local Source proof.

Static compile: all nine changed Python files PASS on Windows; canonical/helper
and modified test/verifier modules also execute successfully on Linux.
`git diff --check` PASS. New Source seal verifies tracked clean Source and records
unchanged Native/declaration/lock/descriptor identities and all 13 entries.

## Investigation attempts and observations

- Initial legacy static invariant rejected the intentionally changed entire
  `wechat_skill.py` byte string. Replaced only this assertion with the exact
  two-function AST allowance; the remaining module is still fully protected.
- Linux /tmp Source directory did not survive between WSL invocations. Final
  extraction and tests were kept in the same invocation; no product/environment
  gate was relaxed or old worktree/venv changed.
- Extra Linux runtime-only launch initially imported the unrelated WeChat
  program and stopped at missing main-venv `css_inline`, before executing its
  tests. The stdlib-only test fixture/branch separation above closes the harness
  coupling. It does **not** claim real Skill imports/venv acceptance or install
  css-inline; all dedicated Runtime import/probe rejection gates remain intact.
- One Windows workflow rerun hit transient `os.replace` PermissionError in its
  synthetic state file. The unchanged Skill suite was rerun verbatim and all
  66 passed; no Skill content, file-operation logic or retry policy was patched.

These exploratory attempts are recorded, not hidden as skips or fake live PASS.
No debt is silently promoted to CLOSED/PRODUCTION_PROVEN. MAIN_RUNTIME_CSS_INLINE
absence is not a new requirement to install Skill dependencies into the main
venv; the dedicated e7 runtime acceptance remains a separate 06 obligation.

## 06 handoff

1. Fresh PRIMARY attestation and a versioned Test approval for exact
   CANONICAL_SOURCE/TREE; never reuse 4ca approval as the new identity.
2. Fetch the successor and verify the new canonical Source seal; original ZIP,
   13 hashes, Native declaration, e7 descriptor/lock/Runtime seal stay unchanged.
3. Generate/load successor qualification/dispatch evidence in new filenames,
   not overwrite old 4ca/b0/e7 history. Same qualified API/MCP/Worker Source
   topology and all existing action/qualification/authority rules still apply.
4. Resume the previously authorized PRIMARY acceptance from its ordering gate.
   Registry/Agent/admin/venv/PREPARE actions require that next round's explicit
   authority; none are performed or pre-approved here.
5. Record fresh native results, zero-call budgets and exact evidence; STOP on any
   content/metadata/lock/Source mismatch. Do not redesign Qualification or
   rebuild ZIP / alter dependencies to fit list order.

**PRIMARY_UNCHANGED · PRODUCTION_UNCHANGED · Provider/Image/WeChat=0/0/0. STOP.**
