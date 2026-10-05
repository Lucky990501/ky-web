# Chat Image Upload + Reference Image Generation V1

## Status

`REFERENCE_IMAGE_TEST_SMOKE_BLOCKED` — implementation and isolated deterministic/UI checks are complete; a real Primary Test Image Edit call has **not** been made. This new Source is not covered by the currently approved, identity-bound Test contract. Do not treat this report as release approval.

## Implementation

- Changed files: `app/chat_image_uploads.py`, `app/main.py`, `app/product_store.py`, `app/product_service.py`, `app/security.py`, `app/service.py`, `app/runtime/codex_provider.py`, `app/platform_mcp/server.py`, `app/platform_mcp/service.py`, `app/static/workbench.js`, `app/static/workbench.css`, `migrations/postgres/015_chat_image_attachments.sql`, `tests/test_chat_reference_image.py`, `tests/workbench_routes.test.cjs`, `tests/chat_reference_image_ui.playwright.cjs`, and this report.
- Upload UI: the image Agent composer accepts picker, drag/drop, and clipboard image paste. It shows a thumbnail, filename, Uploading/Ready/Failed status, retry/remove controls, and blocks submission during upload or failure. Attachment controls use accessible labels and 44px targets; 375px layout has no horizontal overflow. Other Agent composers are unchanged.
- Input contract: `POST /api/v1/agents/image-agent/runs` accepts the existing `message` plus optional `attachments: [{"type":"image","id":"..."}]`. A task claims its current upload atomically and only once. A subsequent text-only turn has no reference image. The private `POST/GET/DELETE /api/v1/chat-images` API does not expose storage keys. History maps the claimed image back to its exact user message.
- Routing: the existing `image_generation` tool remains the only image tool. No task-bound image uses `/v1/images/generations`; one current task-bound image uses `/v1/images/edits`. The signed, tenant-bound task scope is carried in a model-inaccessible MCP header, and the gateway reads only the upload bound to that task. `MAX_REFERENCE_IMAGES = 1` in product behavior and API validation.
- Upload validation: JPEG, PNG, WebP only; declared MIME must match validated actual image format and the file must decode. V1 limits: **10 MiB**, **4096 × 4096** pixels. Existing private object storage is reused; no new storage system or public Provider URL is introduced.
- Edit multipart: `image` contains controlled original bytes, with `prompt`, `model=gpt-image-2.5-sunburst-c`, `n=1`, `size=1024x1024`, and `output_format=jpeg`. The HTTP client generates the boundary. The dedicated image credential is read through the existing setting; no credential is included in the browser or report.
- Output: `data[0].b64_json` is decoded and verified. Actual JPEG/PNG/WebP determines MIME and storage extension; no requested JPEG assumption is made. `requested_size` and actual width/height are stored separately. No crop/resize was added. The old image proxy and existing result-image view/download controls were not changed.
- Failure paths covered in deterministic tests: unsupported/mismatched/corrupt/oversize upload, unauthorized or reused attachment, missing storage object, image API 4xx/5xx, timeout, malformed base64 and unsupported actual output. One request is made per tool invocation; no automatic retry was added.

## Verification

| Check | Result |
| --- | --- |
| Reference-image Python tests + existing image-generation provider tests | 37 passed, 2 dependency deprecation warnings |
| Existing history + task-finalization tests | 64 passed |
| Frontend route/unit suite | 87 passed, 0 failed |
| Browser fixture A–F | PASS: choose/remove/send/history/output/plain text |
| Browser input modes | PASS: picker, drop, paste |
| Browser responsive check | PASS: 1440px and 375px; no 375px horizontal overflow; result view/download remain accessible |
| Real Primary Test Image Edit Smoke | **NOT RUN — contract approval required** |
| Provider calls in this feature verification | Image Edit 0; Text 0; Retry 0 |
| Production | **UNCHANGED**; no deploy, credential change or release switch |

Browser evidence is available in local screenshots `chat-reference-image-upload-1440.png` and `chat-reference-image-result-375.png` in the Codex visualizations workspace. The browser test uses mocked APIs, not a real Provider. The historical Ubuntu WSL environment was used only for focused local tests, not Candidate/Stage 2/Full pytest; an unrelated Product Auth suite is known to fail there on an NTFS Skill source-lock file-mode mismatch and is not claimed as a pass.

## Primary Test gate

`docs/test-release-authority-reference.json` and `deploy/RELEASE_TEST_ENVIRONMENT.md` bind the approved Primary Test contract to Source `77e5eb0eaaa36eb3cd1f158a35f605eaa3052b71`, baseline `20260929-77e5eb0`, and the sealed `test-release-contract.v1.json`. They explicitly state that a later Source is **not automatically approved** and needs a separately approved, versioned identity-bound Test contract. This feature Source is later and has no such approval in scope. Consequently, the allowed one-call budget does not authorize installing this Source or invoking the Test credential through an unapproved contract. No real call was made.

Migration `015_chat_image_attachments.sql` is new. Existing release/compatibility declarations are pinned to migration 014 and were deliberately **not** rewritten under this task. A future authorized release-validation cycle must approve both the new Source/Test identity and the migration/forward-compatibility contract before claiming release readiness. Do not modify the sealed Test or Production contracts to force a pass.
