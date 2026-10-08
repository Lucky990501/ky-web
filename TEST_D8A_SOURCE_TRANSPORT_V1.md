# TEST ONLY — fixed d8a source transport

Application d8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7 /
tree 6021a1f5017b92c3e5b785db1dd6bfcbb190d7d5; exact predecessor dca318d.

Existing owner-context export runs as lucky. Root snapshots Bundle bytes into
its protected native staging, verifies commit/tree/ancestry and seals a fixed
Root-owned bare Git source. Existing formal Binding reads this source, not a
lucky-owned Git config. No wildcard trust, SUDO_UID, arbitrary repo/path,
application edit, SQL change, new Runtime/Secret/Provision system.

The transport verifier only reads and validates; it grants no installation or
Production authority. Root trusted code and full staging maps are checked before
and after Binding. Scope/Receipt paths are separately versioned and exact.

Prepare driver module isolation is independently verified by the fixed
`test_prepare_module_isolation.py` checker. It loads frozen dca and d8a guards
under distinct module identities in both orders, leaves `sys.modules["common"]`
unchanged, and requires the actual formal Prepare to run in a separate fixed
Root subprocess. It grants no Scope, Provision, switch, or Production authority.

This successor binds its Test-only native context to
`/etc/enterprise-agent-test-successor-wechat-d8a-common-cache-v1`, its sole
provision receipt to `release-evidence/wechat-d8a-common-cache-v1/`
`provision-attempt-01/receipt.v2.json`, and its unique synthetic tenant to
`wechat-personal-center-common-cache-v1`. The predecessor context remains
immutable and this binding grants no wildcard tenant authority.
