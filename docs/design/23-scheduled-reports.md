# 23 — Scheduled Reports

> Part of the STP platform design set — overview: [DESIGN.md](../../DESIGN.md) · index: [README.md](README.md)
> New document (post-split addition, same template). Resolves **TBD-13** (report scheduling scope, SRS §9): scheduled reports **are** in MVP scope, as in-app generation on a per-user schedule; no existing decision, ID or requirement text was changed. Extends [04 — Reporting & Charting](04-reporting-charting.md) (on-demand generation) and reuses its builders/renderers unchanged.

## Purpose

Let any report-entitled user (REPORT_VIEW) register a recurring schedule — *this portfolio, this report type, this format, daily or weekly* — and have the platform generate the report automatically, with the result landing in the same report history and notification flow as an on-demand request. Schedules are driven by the **simulation clock** ([01 — Market-Data Service](01-market-data.md), D-10), so a "daily" report fires once per simulated day (≈ 78 s at the default replay speed) and the feature is demonstrable live; without a running replay the wall clock is the fallback.

## SRS requirements covered

- **TBD-13** — report scheduling scope — **RESOLVED**: scheduled reports are in MVP. Scope decided: per-user schedules over {portfolio, type, format, frequency ∈ DAILY|WEEKLY}, generated in-app (files in `backend/var/reports`, history in `GET /reports`, in-app notification via the `notify` outbox stream). **Email delivery is out of MVP** — the notification seam ([14 — Notifications](14-notifications.md)) already carries the "report ready" event in-app, and SMTP stays a mocked adapter.
- **FR-RPT-003** — report generation (extended, not changed): the scheduled path produces the same `Report` rows, files, audit events and notifications as the on-demand endpoint.

## Components

- **`ReportSchedule` entity** (`app/core/models.py`) — one row per schedule: `schedule_id`, `user_id` (owner — only they see/manage it), `portfolio_id`, `type` (HOLDINGS|TRANSACTIONS|PERFORMANCE), `format` (PDF|CSV), `frequency` (DAILY|WEEKLY), `next_run_at`, `last_run_at` (nullable), `active` (bool), `created_at`. New table only — `create_all` picks it up; no migration concern.
- **Shared generation helper** — `_generate_report(db, *, report_type, portfolio, start, end, report_format, actor_id, schedule=None)`: the core of `POST /reports` (row → builder → file → `REPORT_GENERATED` audit → `notify` outbox) refactored into one function used by both the endpoint and the scheduler. The endpoint's behavior is unchanged; the scheduled call passes the schedule so the audit payload carries `schedule_id` and the notification body names the schedule. The caller commits.
- **Scheduler worker** — `report_scheduler` (`app/modules/reports`), registered via the module's `get_workers`. Every **10 wall-seconds** (sleep-first loop, per the JIT-sweep idiom) it runs `process_due_schedules(sessionmaker)`: compute `now = get_sim_now() or utcnow()`, select schedules with `active AND next_run_at <= now`, and for each, in its own transaction: generate the report for the period `[next_run_at − frequency, next_run_at]`, set `last_run_at = next_run_at`, advance `next_run_at += frequency`. **Catch-up is capped at one run per sweep per schedule** — a lapsed schedule re-fires on successive sweeps until caught up, never generating a backlog in one pass. DB units are shielded (aiosqlite-cancellation idiom); a failing schedule is rolled back, logged and skipped — the worker never dies on a bad schedule.
- **CRUD API** — `GET/POST /report-schedules`, `DELETE /report-schedules/{id}` (details below).
- **UI** — a "Report schedules" panel on the Reports page (create form + list + delete), polled at 15 s, with a hint that schedules run on simulation time.

## Flows

1. User submits the schedule form → `POST /report-schedules` validates type/format/frequency (request schema), checks portfolio access exactly as `POST /reports` does (owner or PORTFOLIO_VIEW_ALL), enforces the ≤ 10 active-schedules cap, and inserts the row with `next_run_at = (get_sim_now() or utcnow()) + frequency`. **No retroactive backfill**: nothing is generated at creation time.
2. Each sweep, the scheduler finds the schedule due, generates the report for the trailing frequency window (e.g. a DAILY schedule due at sim-time *t* covers `[t − 1 day, t]`), and advances `next_run_at` by exactly one frequency step.
3. The generated `Report` row is indistinguishable from an on-demand one: it appears in `GET /reports` history (requested_by = the schedule owner), is downloadable via `GET /reports/{id}/download`, writes a `REPORT_GENERATED` audit event, and publishes a `notify` outbox event (category REPORT) whose body mentions the schedule — so the owner gets the usual in-app "report ready" notification.
4. `DELETE /report-schedules/{id}` **hard-deletes** the row (owner only; 404 otherwise). Already-generated reports are unaffected.

## Data entities used

- `ReportSchedule` (new, above).
- `Report` — unchanged; scheduled runs create ordinary rows.
- `Portfolio` — read for the access check and the builder inputs.
- Simulation clock — `app.modules.marketdata.registry.get_sim_now()`; `None` before the first tick, hence the `or utcnow()` fallback (same pattern as the valuation projector).

## API endpoints used

All under `/api/v1`, all gated `REPORT_VIEW`, standard error envelope:

- `GET /report-schedules` → my schedules, newest first: `{items: [...], next_cursor: null}` (a user's schedules are few; no cursor pagination).
- `POST /report-schedules` (201) — body `{portfolio_id, type, format, frequency}`; same portfolio access check as `POST /reports` (403 when neither owner nor PORTFOLIO_VIEW_ALL); **422 BUSINESS_RULE_VIOLATION** when the user already has 10 active schedules. Returns the created schedule.
- `DELETE /report-schedules/{id}` → mine only, 404 otherwise; hard delete.

## Error / edge cases

- **No replay running** — `get_sim_now()` is `None`; schedules run on wall-clock time (fallback), so the feature works identically with the generated feed.
- **Naive vs aware datetimes** — SQLite returns naive datetimes; all loaded values go through `as_utc()` before arithmetic/comparison (project pitfall register).
- **Portfolio deleted while a schedule exists** — the sweep deactivates the schedule (`active = false`) instead of failing forever; it stops appearing as due and can be deleted by the owner.
- **Bad schedule row (unknown frequency/type)** — impossible via the API (schema-validated Literals); defensively, the per-schedule try/except rolls back, logs and continues with the next schedule.
- **Long downtime / far-past `next_run_at`** — one generation per sweep per schedule; the schedule catches up over successive sweeps. No backlog flood, no burst of notifications.
- **Cap** — at most 10 active schedules per user (decision: keeps the sweep bounded and the demo tidy); excess → 422. Deleted schedules free a slot immediately (hard delete).
- **Worker lifecycle** — sleep-first loop (no DB traffic at t=0); the sweep runs shielded so cancellation cannot wedge an aiosqlite connection mid-write; the worker logs and retries the next interval on any sweep-level failure.

## Acceptance criteria mapping

- **TBD-13** — covered by the schedule tests in `backend/tests/test_experience.py`: CRUD round-trip (create/list/delete), ownership isolation (another user's schedule → 404), portfolio access check (403 without ownership or PORTFOLIO_VIEW_ALL), the 11th active schedule → 422, and deterministic due-processing (function-level `process_due_schedules` call: DONE report with the exact trailing period, file on disk, `next_run_at` advanced by exactly one frequency step, notify outbox row for the owner; a second immediate run is a no-op).
- Per [19 — Testing Strategy](19-testing-strategy.md), the scheduled path reuses the FR-RPT-003 generation code, so the existing report tests keep covering the builders/renderers unchanged.
