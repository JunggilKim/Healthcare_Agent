# Pending External Validation

This file records checks that require user-controlled credentials, billing, quota, capacity, or
local infrastructure state. They are not represented as completed.

## Google Cloud and Gemini

- Status: pending.
- Blocker: no approved Google Cloud project, Application Default Credentials, billing/quota, or
  explicit authorization to make paid model calls was supplied in the current implementation run.
- Completed independently: first-party `google-genai==2.17.0` enterprise client construction,
  API v1 selection, fixed model routing, structured schemas, retry/circuit/fallback behavior,
  application cache, usage metadata parsing, price estimation, trusted proposal validation, and
  opaque safety fallback.
- Required external proof later: model-access smoke test for `gemini-3.6-flash`,
  `gemini-3.5-flash-lite`, and `gemini-embedding-001`; paid compilation/review of the final curated
  corpus; quota and cost-guard validation.

## Docker Desktop

- Status: pending.
- Blocker: Docker Desktop's internal image storage was full while pulling the base image
  (`no space left on device`). Host storage had free capacity, but Docker reported reclaimable
  images belonging to the user; they were not deleted without approval.
- Completed independently: dependency bootstrap, native lint/typecheck/tests, production frontend
  build, Uvicorn API run, and Chromium E2E.
- Required external proof later: recover Docker storage, build the release image, run it with
  outbound networking disabled in Snapshot Mode, and record its digest.

