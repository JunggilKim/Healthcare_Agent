# Submission Checklist

Unchecked boxes are real release gates, not decorative placeholders.

## Code and reproduction

- [ ] Repository access verified for judges.
- [ ] `v1.0.0-challenge` tag exists at the verified commit.
- [ ] Source archive was created from that tag.
- [ ] Docker offline demo passed on a second machine.
- [x] Python and frontend dependencies are locked.
- [x] `.env.example` is complete.
- [ ] Strict secret scan passes at the tagged commit.
- [ ] Complete S004/S008/S001 snapshot hashes and age pass.

## Challenge requirements

- [x] Role-separated agents are visible.
- [x] Inclusion/exclusion matching is shown.
- [x] One missing-information question and reevaluation are shown for S004.
- [x] Evidence-based pre-screening recommendation and proof replay are shown.
- [x] Public/synthetic-data restriction and medical disclaimer are visible.
- [x] Data sources, source-specific terms, and model assumptions are documented.
- [ ] S008 and S001 reviewed golden flows pass offline E2E.

## Research evidence

- [x] Baseline/ablation engineering-smoke tables are generated reproducibly.
- [ ] Mandatory Dataset A B0–B6 table passes annotation and held-out requirements.
- [ ] Required ablations pass on Dataset A.
- [ ] Key acceptance metrics reproduce from the tagged artifact.
- [x] Limitations and current claim scope are disclosed.
- [x] No unsupported superiority claim is made.

## Demo reliability

- [x] S004 snapshot flow and network-blocked E2E pass locally.
- [ ] Complete reviewed S008 snapshot flow passes.
- [ ] Complete reviewed S001 snapshot flow passes.
- [x] Failure toggle works in the independently verified S004 path.
- [ ] Three release rehearsals are recorded, including network-disabled.
- [ ] Presentation-day Cloud Run minimum instance is set to 1 and later reverted.
- [ ] Local backup laptop/container is verified.
- [x] 1440×900 layout and browser interaction were checked locally.
- [ ] Production smoke prints request IDs/latencies without a session token.

## Release evidence

- [ ] `uv run python scripts/verify_release.py --strict` exits 0 on a clean tree.
- [ ] Production URL, source commit, tag, image digest, snapshot hash, and metrics run ID are recorded.
- [ ] Pricing/model lifecycle acknowledgement is current.
- [ ] Final challenge presentation file is added without changing the verified source archive.
