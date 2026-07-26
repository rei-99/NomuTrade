# 14 — Notifications

> Part of the STP platform design set — overview: [DESIGN.md](../../DESIGN.md) · index: [README.md](README.md)
> Source: former DESIGN.md §5.7. Decisions, IDs and requirement text unchanged.

## Purpose

Deliver event-driven user notifications in-app and by email, with scheduled reminders for grant expiry and stale approvals, and per-category preferences that can never silence security-critical events (FR-NTF-003 E1).

## SRS requirements covered

- **FR-NTF-003 E1** — security-critical notification categories are non-suppressible.
- **TBD-09** — reminder timings [P: 30 min privileged / 24 h standard].
- Consumes domain events per the event pipeline (DESIGN.md §4.2): `trading.executions`, `stp.lifecycle`, `access.events`.

## Components

- **Notification worker** — consumes domain events, resolves recipients, renders templates, delivers in-app (PostgreSQL + WebSocket) and email (SMTP, credentials from CyberArk). Retry with backoff; SMTP outage degrades to in-app only and shows on the health view.
- **Reminders** — scheduler scans grant expiries (30 min privileged / 24 h standard [P, TBD-09]) and stale approval tasks (>24 h).
- **Preferences** — per-category channel matrix; security-critical categories hard-coded non-suppressible (FR-NTF-003 E1).

## Flows

No dedicated flow diagram exists in the source design. The worker consumes `trading.executions`, `stp.lifecycle` and `access.events` (consumer table, DESIGN.md §4.2) and appears as participant `N` in the end-to-end sequences: order confirmations in [02](02-order-execution-stp.md), approval/grant/expiry notices in [08](08-access-request-workflow.md), break-glass real-time alerts in [12](12-break-glass.md). In-app push rides the authenticated WebSocket channel (see [03 — Portfolio Management](03-portfolio-management.md)).

## Data entities used

- `Notification` (category, channel, `payload` jsonb, status).
- Preferences: per-category channel matrix (per FR-NTF-003).
- SMTP credentials come from CyberArk via the secret provider (see [17 — Security Design](17-security-design.md)).

## API endpoints used

- **In-app delivery** — PostgreSQL-backed notification records pushed over the authenticated WebSocket `/ws`, topic `notifications` (former DESIGN.md §9; channel detail in 03).
- **Email delivery** — external SMTP relay (credentials from CyberArk).
- Any user-facing query/preference endpoints follow the standard conventions via the module's OpenAPI fragment (base `/api/v1`, JSON, cursor pagination, error envelope with `traceId`).

## Error / edge cases

- **SMTP outage** — degrade to in-app only; surfaced on the health view (see [15 — Admin & Governance](15-admin-governance.md)).
- **Delivery failure** — retry with backoff.
- **Suppression attempt on security-critical category** — not possible: such categories are hard-coded non-suppressible (FR-NTF-003 E1).

## Acceptance criteria mapping

- No dedicated AC ID is cited for FR-NTF in the source design; notifications are verified inside the integration flows that emit them (order→STP, approval chains, JIT expiry, checkout, break-glass — see [19 — Testing Strategy](19-testing-strategy.md), Integration row).
- Per the 23-AC statement in 19, each SRS acceptance criterion is implemented as at least one automated test or scripted demo step.
