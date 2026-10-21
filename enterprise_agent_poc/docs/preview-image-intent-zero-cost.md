# Preview image natural-language planning

Baseline: `a2fcf83cfd8d39c1c93a9116ceeebc494ccbcc4c`.

Scope: independent Preview API/MCP only. No model, credentials, price,
authorization, credit, historical Task, image file or budget change.

## Contract

- New Tasks persist one server-owned output plan in the existing submission
  record. Admission, execution and MCP use that plan; the browser passes a
  digest, never a caller-authored plan. Repeated submission IDs remain idempotent.
- Natural-language output sizes map to square 2048x2048, landscape 3840x2160
  or portrait 2160x3840, with quality high. Explicit ratios/dimensions win over
  scene defaults. Unsupported exact dimensions map to the nearest standard
  with a note; no resizing of saved originals occurs.
  Brochure layouts are an explicit Preview exception: the already verified
  1536x1024 (3:2) enum is the nearest existing landscape option to A4. This is
  not an exact A4, print-ready or DPI guarantee. User-explicit geometry wins.
- Counts are 1–5. Reference counts and slot ordinals are distinct from totals.
  An unspecified group defaults to three; an unspecified trifold defaults to
  two landscape spreads, three panels each, not print-ready PDF/artwork.
- Continuations resolve only the authenticated user's current image conversation.
  The latest related Task and selected/last successful Generation supply the
  brief, reference bytes and size. Reference copies are separately bound to the
  new Task, preserving the old file. New topics do not inherit a reference.
- Recovery creates a new linked Task and requests only missing slots with known
  failed HTTP or explicit non-dispatch evidence. Successful slots and unknown
  outcomes are not replayed. Existing request and fee records are untouched.
- History responses distinguish new edits, series additions and recovery.
- Corrective wording overrides conflicting old count/layout instructions while
  keeping the current conversation's verified creative brief. A missing,
  previously unplanned reverse side is a new linked output, not a retry of the
  successful front. Known failed planned sides retain the bounded recovery guard.
- Brochure front/back carry dedicated three-panel layout modes and separate
  brand zones. Only the original transparent tenant Logo is composited there;
  source image dimensions and historical assets remain unchanged.

## Verification limits

`tests/test_preview_image_intent.py` and the adjacent execution tests cover
sizes, counts, current-project edits, partial recovery, unknown results,
ownership, original preservation, idempotence and mocked HTTP against the real
PlatformMCP service. `tests/preview_image_intent.cjs` runs the actual Preview
frontend script in isolated Chrome: no size/quality selector, no confirmation
dialog, conversation/ordered references/digest delivered to submission.

All are explicitly **Fixture** results. No paid image generation, live customer
Task creation or public-browser business acceptance is claimed. Actual Provider
visual fidelity and uncommon ambiguous language still require customer review.
Any separately authorized paid acceptance is recorded in its own deployment
evidence, not relabeled as a zero-cost test.
