

## 1. Rohan Singh — Head of Product Development

*Context: wants single-click trading, fast processing, KPIs, reports, charts,
"speak the language of our users". Primary owner of product-scope TBDs.*

**Theme: MVP scope and definition of success**

1. "Single click" — literally one click from where? Watchlist, chart, or order ticket with one confirmation? What confirmation, if any, is acceptable before an order is live? *(Drives the order-ticket UX, FR-ORD-001.)*
  In fact, it would be better if after the trader did the "single click", a panel/window containing details pops out for confirmation where the trader could do the second click.

**Theme: products and orders** — *resolves TBD-17, TBD-18*

4. Which asset classes do your clients actually trade day to day? Is an equities-only MVP credible to you? *(TBD-17 — design currently assumes equities only.)*
Answer: We have implemented the equity part in the project, and Bonds is also a must.
5. Beyond MARKET and LIMIT, which order types do traders refuse to live without — stop, stop-limit, iceberg? *(TBD-18.)*
Answer: yes, all the order types that you could imagine 
6. Are there order restrictions we must enforce up front (restricted lists, per-desk limits, max notional per order)? *(Extends FR-ORD-002 pre-trade validation rules.)*
Answer: yes


**Theme: reports, KPIs and the GenAI roadmap**

7. Which three KPIs do you personally check first each morning? What would you want on the dashboard that Excel can't give you today? *(FR-PFM-003, FR-RPT-001.)*
    Answer: you could research yourself to see what is the pain point of a trader.
8. After the program, what would "phase 2" look like in your ideal world — live market connectivity, more products, more Answer: analytics? *(Shapes the future-roadmap slide in the final presentation, deliverable 3.)*
    Answer: For now, the system takes mock news data, it would be better if the system could link to outer system now or at least illustrate the potential for future improvement as road map.



---

## Consolidated TBD coverage map

Every SRS open question is assigned to exactly one interview above:

| TBD | Topic | Interview |
|---|---|---|
| TBD-01 | Approval chains | §6 Corporate/SME session |
| TBD-02 | JIT duration policy | §4 Roy |
| TBD-03 | Break-glass policy | §4 Roy |
| TBD-04 | CyberArk environment | §4 Roy |
| TBD-05 | Directory and SSO | §4 Roy |
| TBD-06 | Simulation dataset | §3 Nora |
| TBD-07 | Performance targets | §3 Nora |
| TBD-08 | Audit integrity & retention | §4 Roy |
| TBD-09 | Notification policy | §6 Corporate/SME session |
| TBD-10 | Database engine | §4 Roy (confirm PostgreSQL with cloud choice) |
| TBD-11 | Cloud provider | §4 Roy |
| TBD-12 | MFA / step-up auth | §4 Roy |
| TBD-13 | Report scheduling scope | §1 Rohan |
| TBD-14 | Paper-trading realism | §3 Nora (validate slippage model) |
| TBD-15 | SoD conflict matrix | §4 Roy |
| TBD-16 | Display defaults | §2 Clients |
| TBD-17 | Instrument scope | §1 Rohan |
| TBD-18 | Order types | §1 Rohan |

**After the interviews:** update SRS §9 with the decisions, flip the `[P]`
proposals in DESIGN.md §7 to confirmed or changed, and re-check any design
assumption that an interview invalidated (most likely candidates: approval
chains, SoD matrix, dataset quirks, cloud provider).

---

*Owner: business-analysis pairing (technology + corporate analysts). Changes via merge request.*
