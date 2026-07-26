# 19 — Testing Strategy

> Part of the STP platform design set — overview: [DESIGN.md](../../DESIGN.md) · index: [README.md](README.md)
> Source: former DESIGN.md §12. Decisions, IDs and requirement text unchanged.

## Purpose

Define the test levels that verify the design against the SRS: each of the 23 SRS acceptance criteria is implemented as at least one automated test or scripted demo step, and the SRS traceability table (SRS §8) doubles as the test index.

## SRS requirements covered

- **NFR-MNT-006** — automated tests with a coverage gate (70% core, enforced in CI — see [18](18-devops-deployment.md)).
- **NFR-PER-001…005** — performance thresholds adopted as test targets (TBD-07).
- **NFR-SEC-002 / NFR-SEC-008** — deny-by-default and tamper-evidence security tests.
- **AC-001 … AC-018** (all 23 SRS acceptance criteria) — mapped per the table and statement below.
- Contract testing per the API design (former DESIGN.md §9): OpenAPI conformance, error envelope, permission declarations on all routes (AC-018).

## Components

Test levels (from the source design):

| Level | Scope | Maps to |
|---|---|---|
| Unit | validators, indicator math, P&L/valuation, SoD matrix, hash chain | NFR-MNT-006 |
| Contract | OpenAPI conformance; error envelope; permission declarations on all routes | AC-018 |
| Integration | order→STP with replayed ticks; JIT expiry; CyberArk sandbox checkout; approval chains | AC-001…AC-015 |
| End-to-end | UI-driven: login, dashboard, ticket, paper trading, audit search | AC-006, AC-016 |
| Performance | k6/locust: 500 ms validation, 2 s market-order E2E, 3 s dashboard, 200 concurrent users | NFR-PER-001…005 |
| Security | deny-by-default sweep, fail-closed dependency tests, hash-chain tamper test | NFR-SEC-002/008, AC-014 |

Tooling notes from the design: k6/locust for performance; contract tests validate responses against the merged OpenAPI spec in CI (each module ships an OpenAPI fragment, former DESIGN.md §9); every merge runs tests and scans before deploy (see 18).

## Flows

The flows under test are the end-to-end sequence diagrams owned by the module docs:

- Order → execution → STP settlement (AC-001, AC-002) — [02](02-order-execution-stp.md).
- Access request → approval → time-bound grant (AC-010, AC-011) — [08](08-access-request-workflow.md).
- Privileged credential checkout (AC-013, AC-014) — [11](11-privileged-access-cyberark.md).
- Break-glass (AC-015) — [12](12-break-glass.md).
- Pipeline quality gates — CI/CD flowchart in [18](18-devops-deployment.md).

## Data entities used

- Replayed ticks from `data.zip` drive integration tests (see [01 — Market-Data Service](01-market-data.md)).
- CyberArk **sandbox** for checkout integration tests (see 11).
- Test targets: validators, indicator math, P&L/valuation, SoD matrix, hash chain (unit scope).

## API endpoints used

- **Contract tests** — validate responses against the merged OpenAPI spec in CI; assert the standard error envelope with `traceId`; enumerate all routes asserting none lack a permission declaration (AC-018).
- **End-to-end tests** — UI-driven over the same `/api/v1` surface and the authenticated WebSocket.

## Error / edge cases

- **Fail-closed dependency tests** — dependencies down (directory, CyberArk, SMTP, feed) must fail closed / degrade as designed (security level; see 11, 14, 17).
- **Hash-chain tamper test** — proves tamper evidence of the audit store (NFR-SEC-008, AC-014; see 13).
- **Deny-by-default sweep** — proves no route is reachable without its permission (NFR-SEC-002, AC-018; see 09).

## Acceptance criteria mapping

This document *is* the acceptance-criteria mapping: the level → AC table above, plus the source design's closing statement — each of the 23 SRS acceptance criteria is implemented as at least one automated test or scripted demo step; the traceability table (SRS §8) doubles as the test index. Requirements → design traceability is in DESIGN.md §6.
