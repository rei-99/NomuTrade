# 15 — Admin & Governance

> Part of the STP platform design set — overview: [DESIGN.md](../../DESIGN.md) · index: [README.md](README.md)
> Source: former DESIGN.md §5.9. Decisions, IDs and requirement text unchanged.

## Purpose

Give Operations, Security and Admins a single governance view over access grants, approvals, break-glass items and denials, plus a health view over the platform's external dependencies and STP exceptions.

## SRS requirements covered

- **FR-ADM-001** — governance dashboard (grants, approvals, expiries, break-glass, denial stats).
- **FR-ADM-002** — health view with dependency probes and STP exception listing.
- **FR-ADM-003** — who-has-what export (Could; extension point).
- **NFR-MNT-003** — metrics endpoint per service feeding the health view (see [18 — DevOps & Deployment](18-devops-deployment.md)).
- **FR-ORD-005 E1** — STP exceptions are surfaced here for Ops (source: [02 — Order Execution & STP](02-order-execution-stp.md)).

## Components

- **Governance dashboard** — aggregates active grants, pending approvals with age, expiring grants, break-glass items, denial stats (FR-ADM-001).
- **Health view** — runs periodic probes (directory, CyberArk, SMTP, feed, queue depths) and lists STP exceptions (FR-ADM-002); probes stored for trend display.
- **Who-has-what export** (FR-ADM-003, Could) — generated from grants + role data.

## Flows

No dedicated flow diagram exists in the source design. Probe targets are the external systems of the system-context diagram (DESIGN.md §3): LDAP/AD, CyberArk, SMTP, plus the market-data feed and queue depths. Service metrics endpoints feeding the health view are part of the deployment design (see 18; NFR-MNT-003).

## Data entities used

- `AccessGrant` (active/expiring), `ApprovalStep` (pending with age), `BreakGlassActivation` (review items), `AuditEvent` (denial stats), `SettlementInstruction` (`STP_EXCEPTION` rows), stored probe results.

## API endpoints used

- Aggregated governance-dashboard and health endpoints, and an export that produces a file — concrete paths per the module's OpenAPI fragment under the standard conventions (base `/api/v1`, JSON, error envelope with `traceId`; former DESIGN.md §9).

## Error / edge cases

- **Dependency probe failure** (directory, CyberArk, SMTP, feed, queue depths) — shown on the health view; SMTP outage also degrades notifications to in-app only (see [14 — Notifications](14-notifications.md)).
- **STP exceptions** — listed for Ops follow-up (FR-ORD-005 E1; retry/alert behavior in 02).
- **Scope** — who-has-what export (FR-ADM-003) is a Could item: attempted only after Must/Should items pass (delivery plan, DESIGN.md §8).

## Acceptance criteria mapping

- No dedicated AC ID is cited for FR-ADM in the source design; the governance and health views are verified as scripted demo steps, and probe behavior via the fail-closed dependency tests (see [19 — Testing Strategy](19-testing-strategy.md), Security row).
- Per the 23-AC statement in 19, each SRS acceptance criterion is implemented as at least one automated test or scripted demo step.
