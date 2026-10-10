# Production Candidate Packaging & UX Integration V1

This successor starts at accepted integration Source
`9b4b65d0932a5c05966d13b1b872d97279ff460c`. Only the eight-file UI change from
`ffb744ac3c13acd1d166e02395e7847484bfdf4f` is selected, not its branch history.
One test-only synchronization fix waits at most two seconds for asynchronous
profile save/route/focus completion. The original immediate assertion failed
while the modal was still present; bounded observation proved the unchanged
product restored focus correctly. No assertion or product UI code is weakened.

The formal Git-source release inventory now includes `integrations/` and
`skill_sources/`. Build preflight checks the complete WeChat resource inventory,
Native ZIP/source identity, and the exact Python 3.11 dependency lock/requirements
closure. These are package inputs, not a Production runtime activation grant.
The twelve verified Linux wheels remain separate immutable dependency artifacts;
they are not secrets, generated Skill packages, or untracked Git source.

WX-05 generated-body preview and UI-05 profile focus behavior are integrated.
WX-05's default adapter stays `BACKEND_PENDING`; real `CREATE_DRAFT` is not
enabled. Tests inject only synthetic API/adapter fixtures and block external
network transport. No new server endpoint, Skill content, Prompt, Registry
binding, Schema015, Migration, Runtime, Image Provider, or dependency-retry
business change is authorized by this successor.

PRIMARY baseline and Production stay unchanged. Real Provider, WeChat, and Image
calls are zero. Isolated component/browser/archive PASS is not customer business
acceptance, Full pytest, Stage2, or Production approval. Final Source/Tree and
immutable artifact identities are recorded in external handoff evidence after
commit; historical 28e/d8a/77e5 approvals are not inherited.

Outstanding Production gates include the approved Selector/tooling successor,
new exact Application/Tooling approval and Recovery binding, the current real
Production exact predecessor, controlled Production dependency installation,
Skill interpreter/secret/action contracts, published Revision upgrade semantics,
and separately approved live business/Provider validation. No release switch,
service restart, migration or PRIMARY deployment is performed here.
