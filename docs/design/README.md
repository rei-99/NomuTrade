# Design Document Set — Index

Module-level design documents for the Next-Generation Trading Platform with STP, split out of [DESIGN.md](../../DESIGN.md) (DSN-STP-2026-001 v1.0). DESIGN.md remains the architecture overview (system context, module map, event pipeline, technology selection, decisions D-01…D-06 and D-10…D-16) and carries traceability, open items and the delivery plan; the documents below hold the module-level detail (former DESIGN.md §§5–12). No decisions, IDs or requirement text were changed in the split; `[P]` still marks proposals resolving SRS `[TBD]` points.

Each document follows the same template: Purpose · SRS requirements covered · Components · Flows · Data entities used · API endpoints used · Error / edge cases · Acceptance criteria mapping.

## Trading & market data

| Doc | Source (former DESIGN.md) | Summary |
|---|---|---|
| [01 — Market-Data Service](01-market-data.md) | §5.1 | Simulation dataset load, tick replay with simulation clock (D-10/D-11), latest-price registry, staleness guard |
| [02 — Order Execution & STP](02-order-execution-stp.md) | §5.2, §7.1 | Order ticket, pre-trade validation, matching engine, STP settlement; order state machine |
| [03 — Portfolio Management & Valuation](03-portfolio-management.md) | §5.3 (+§9 WebSocket) | Valuation projector, KPIs, read APIs, ≤5 s WebSocket push |
| [04 — Reporting & Charting](04-reporting-charting.md) | §5.4 (reporting half) | Dashboard aggregation, OHLC series API, PDF/CSV report generator |
| [05 — Technical Analytics](05-technical-analytics.md) | §5.4 (analytics half) | Indicators (SMA/EMA/RSI/MACD/Bollinger), ECharts overlays, news/sentiment endpoints (D-15) |
| [06 — Paper Trading](06-paper-trading.md) | §5.2 (FR-PTR parts) | Paper = same pipeline; `PAPER` portfolio type, isolation and marking (AC-008) |
| [07 — GenAI Assistant](07-genai-assistant.md) | §5.8 | Advisory-only assistant; read-only tool whitelist incl. news/sentiment; suggestions via standard ticket (stretch) |

## Access governance & privileged access

| Doc | Source (former DESIGN.md) | Summary |
|---|---|---|
| [08 — Access Request Workflow](08-access-request-workflow.md) | §5.5 (IAM parts), §7.2 | Directory sync + SSO provisioning; request → multi-level approval → time-bound grant |
| [09 — RBAC & Authorization](09-rbac-authorization.md) | §5.5 (resolver, SoD), §6, §9 | Effective-permission resolver, authN/Z middleware, SoD matrix, route permission declarations (AC-018) |
| [10 — JIT Access](10-jit-access.md) | §5.5 (JIT parts) | Time-bound grants, 30 s expiry sweep, request-time window validation (AC-011) |
| [11 — Privileged Access (CyberArk)](11-privileged-access-cyberark.md) | §5.5 (CPAM), §7.3 | PVWA checkout/check-in, CPM rotation, memory-only credentials, fail closed (AC-013/014) |
| [12 — Break-Glass](12-break-glass.md) | §5.5 (BG), §7.4 | Emergency grant ≤ 4 h, high-severity audit, 24 h review SLA (AC-015) |

## Platform services

| Doc | Source (former DESIGN.md) | Summary |
|---|---|---|
| [13 — Audit Logging](13-audit-logging.md) | §5.6, §8.2 (hash-chain parts), §4.2 (audit path) | Append-only hash-chained `AuditEvent`, sync/async paths, search/export |
| [14 — Notifications](14-notifications.md) | §5.7 | Event-driven in-app + email delivery, reminders, non-suppressible security categories |
| [15 — Admin & Governance](15-admin-governance.md) | §5.9 | Governance dashboard, dependency health probes, who-has-what export (Could) |
| [22 — Real-time WebSocket Push](22-websocket-push.md) | — (implements former §9 `/ws`) | Authenticated `WS /api/v1/ws`: tick broadcast + per-user notification/execution hints (NFR-PER-004); REST stays source of truth |
| [23 — Scheduled Reports](23-scheduled-reports.md) | — (resolves TBD-13) | Per-user daily/weekly report schedules, sim-clock driven; `report_scheduler` worker reuses the on-demand generation path |
| [29 — Dynamic Portfolio Budget](29-dynamic-portfolio-budget.md) | — (research/design, not implemented) | Risk-Adjusted Capital Allocation: budget = tier base × target-vol factor, capped; phased quote → default → living-limit plan |

## Cross-cutting

| Doc | Source (former DESIGN.md) | Summary |
|---|---|---|
| [16 — Data Design](16-data-design.md) | §8 | ER model (from SRS 6.1/6.2 + news tables, D-14) and physical notes: partitioning, immutability, outbox, Redis keys |
| [17 — Security Design](17-security-design.md) | §10, §6 | NFR-SEC-001…010 measures, authN/Z middleware, audit writer, secret provider, error model |
| [18 — DevOps & Deployment](18-devops-deployment.md) | §11 | D-06 single-VM Docker deployment, Terraform, GitLab CI/CD pipeline |
| [19 — Testing Strategy](19-testing-strategy.md) | §12 | Test levels mapped to ACs/NFRs; 23 acceptance criteria as tests or scripted demos |
| [20 — Trading Workspace UI](20-trading-workspace-ui.md) | — | Research synthesis (TradingView/IBKR/Kite) + design language for the terminal UI |
| [21 — Product-owner feedback](21-product-owner-feedback.md) | — | Interview analysis: two-click confirm, bonds, STOP/STOP_LIMIT, order restrictions, day-change KPIs, news provider seam |
| [24 — Advanced Orders](24-advanced-orders.md) | — (extends 02, resolves more of TBD-18) | Time-in-force (DAY/GTC/IOC), TRAILING_STOP with persisted water-mark, bond coupon/maturity fields + YTM/duration analytics |
| [25 — UX round 1](25-ux-round-1.md) | — | EN/JA i18n, 4 personas, bond/equity split, workspace layout v2, price-follow |
| [26 — Role views & login](26-role-views-and-login.md) | — | Persona-faithful tabs/homes from role duties; PBKDF2 password login with lockout |
| [27 — GenAI agent](27-genai-agent.md) | FR-AI | OpenAI-compatible seam + startup self-check with mock fallback; LLM news prose; RAG help over project docs; advisory trade review |
| [28 — Agent workflow](28-agent-workflow.md) | FR-AI | LangGraph state graph (clarify→confirm→draft); conversation memory from interaction history + pending-action state; fuzzy symbols; pronoun resolution |
