# TEST ONLY — d8a exact Source bindings

Application Source: d8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7.
Application Tree: 6021a1f5017b92c3e5b785db1dd6bfcbb190d7d5.
Exact predecessor: dca318de578a9b9601ad036e1b176bc5ab702029.
Authority: WECHAT_PERSONAL_D8A_PRIMARY_SUCCESSOR_V1.

This separate tooling commit rebinds only fixed application/native/receipt/
credential paths, exact identity and its scope fixture. Minimal Provision V2,
all INSERT/DELETE SQL, row canonicalization, cleanup ownership checks, owner
privilege drop, fixed executables and runtime identity remain unchanged.
No application, Skill artifact/runtime lock, Production or old contract change.
Zero WeChat/Provider/Image budget. New Scope must be unique and later revoked.
This source readiness is not a claim of live install or completed acceptance.
