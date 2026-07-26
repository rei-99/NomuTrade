# 13 — Audit Logging

> Part of the STP platform design set — overview: [DESIGN.md](../../DESIGN.md) · index: [README.md](README.md)
> Source: former DESIGN.md §5.6 plus the hash-chain/audit physical notes of §8.2 and the audit path of §4.2. Decisions, IDs and requirement text unchanged.

## Purpose

Provide a tamper-evident, append-only audit trail (NFR-SEC-008): every security-relevant action is recorded with a hash chain, the chain is verified by a scheduled job, and the store cannot be mutated through the application or the API.

## SRS requirements covered

- **NFR-SEC-008** — tamper-evident audit via hash chaining.
- **FR-AUD-001 E1** — audit write failure on a security-critical action fails the call (fail closed).
- **FR-AUD-003** — search and export; export writes its own audit event.
- **NFR-PER-006** — audit search performance (indexing).
- **FR-CPAM-003 E1** — checkout audit is synchronous (see [11](11-privileged-access-cyberark.md)).

## Components

- **Append-only store** — `AuditEvent` with `payload_hash = SHA-256(canonical payload ‖ prev_hash)`; chain verified by a scheduled integrity job. No UPDATE/DELETE grants on the table for the application role; no API exposes mutation.
- **Audit writer** — single choke point (cross-cutting, see [17 — Security Design](17-security-design.md)); synchronous for security-critical events, async otherwise; attaches `correlation_id` propagated from the request's trace ID.
- **Search/export** — indexed on (actor, type, resource, severity, ts); date range mandatory; cursor pagination; export writes its own audit event (FR-AUD-003).

## Flows

No dedicated flow diagram exists in the source design. The audit path is deliberately **synchronous for security-critical actions** (checkout, break-glass, authorization denials): the API call writes its audit record in the same request and fails closed if the write fails (FR-AUD-001 E1, FR-CPAM-003 E1). Lower-value events flow via `audit.all` asynchronously to the audit persistence consumer (stream table, DESIGN.md §4.2). The audit writer's position in the request pipeline is shown in the cross-cutting flowchart in [09 — RBAC & Authorization](09-rbac-authorization.md).

## Data entities used

- `AuditEvent` — fields: `event_id`, `ts`, `actor_id`, `event_type`, `resource_type`, `resource_id`, `severity`, `source_ip`, `correlation_id`, `payload_hash`, `prev_hash` (see [16 — Data Design](16-data-design.md)).
- Physical notes (from former §8.2): monthly partitioned (~50 k audit events/day, SRS 6.3); btree on `(actor_id, ts)` and `(event_type, ts)` for audit search (NFR-PER-006); application DB role has INSERT/SELECT only — no UPDATE/DELETE/TRUNCATE (NFR-SEC-008).

## API endpoints used

- Search/export endpoints follow the standard conventions (base `/api/v1`, JSON, error envelope with `traceId`; former DESIGN.md §9) with two module-specific rules: **date range mandatory**, cursor pagination; paths per the module's OpenAPI fragment.
- **Export writes its own audit event** (FR-AUD-003).
- No API exposes mutation of audit records.

## Error / edge cases

- **Audit write failure on a security-critical action** — the request fails closed (FR-AUD-001 E1).
- **Tampering** — detected by the scheduled chain-verification job over `payload_hash`/`prev_hash` (security test: hash-chain tamper test, see 19).
- **Mutation attempts** — blocked at the DB level: application role has INSERT/SELECT only (NFR-SEC-008).

## Acceptance criteria mapping

- **AC-014** — hash-chain tamper test in the security test pass (see [19 — Testing Strategy](19-testing-strategy.md), Security row: NFR-SEC-002/008, AC-014).
- **AC-016** — audit search is in the UI-driven end-to-end scope (see 19, End-to-end row).
