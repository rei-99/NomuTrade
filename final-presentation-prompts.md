# Final Presentation — Kimi Code Prompts

**Project:** Nomura Tech Graduate Program 2026 — Next-Generation Trading Platform with Straight-Through Processing (STP)
**Deliverables covered:** MVP platform (5 modules) · Operational workflow documentation · Final team presentation

**How to use:** Fill in the `[brackets]`, run **Prompt 1** first and review `presentation/script.md`, then run **Prompt 2**. Both prompts instruct Kimi Code to analyze your actual repo (code, docs, git history, CI/CD config) before writing anything, so the output stays grounded in what you really built.

---

## Prompt 1 — Presentation Script & Flow

```
You are helping me prepare the FINAL TEAM PRESENTATION for the Nomura Tech
Graduate Program 2026 project: a Next-Generation Trading Platform with
Straight-Through Processing (STP), built in 3 weeks by a cross-functional team
of technology and corporate graduates.

Your task: analyze this repository, then produce a complete presentation script
with flow, timing, and speaker notes.

## Project context (from the official brief — treat as ground truth)
- MVP trading platform with STP; five core modules: Order Execution, Portfolio
  Management, Reporting & Charting, Technical Analytics, Paper Trading
- Tech stack: Python, GenAI, Cloud (AWS/Azure), Docker, Terraform, GitLab CI/CD,
  Git, data visualisation
- Cross-functional split: technology analysts built the platform; corporate
  analysts mapped operational workflows (trade settlement, risk management,
  system access), compliance, and business requirements
- The brief REQUIRES the final presentation to cover: (1) platform features,
  (2) operational processes, (3) future roadmap recommendations
- Program objectives we must demonstrate: practical GenAI/Agile/DevOps
  application, trading-process understanding, cross-functional collaboration,
  scalability & operational readiness
- Support model during the project: daily facilitator touchpoints, structured
  feedback after each milestone, SME access
- Demo data: simulation data provided by the program (data.zip)

## Step 1 — Ground yourself in the actual repo (BEFORE writing anything)
- Read README, docs/, and all design/workflow documents (including the
  operational workflow documentation: settlement, risk, system access)
- Map the five modules to what is ACTUALLY implemented (routes/services/UI);
  flag any module that is partial or mocked so we present it honestly
- Identify where GenAI is used in the codebase and what it concretely does
- Review git/GitLab history: `git log --oneline --graph --all | head -100`,
  branch names, merge requests — evidence of how we collaborated
- Check CI/CD config (.gitlab-ci.yml), Dockerfile, Terraform files — concrete
  DevOps proof points
- Check tests and reporting/visualisation code
- Pick 2–3 real "war stories" from the code/history (a tricky bug, a redesign,
  a tech↔corporate integration pain point, a GenAI attempt that failed first)
  — real examples only, never invented events or numbers

## Step 2 — Configuration (adjust the brackets before running)
- Total time: [15] min presentation + [5] min Q&A
- Audience: [program facilitators, business stakeholders, fellow graduates —
  mixed business/technical]
- Language: [English]
- Presenters: [5] — a mix of technology and corporate analysts; split sections
  by expertise and script every handover
- Demo: [live demo on the simulation data, with a recorded-video fallback]

## Step 3 — Narrative arc (roughly 45% the platform, 55% the journey)
1. Hook (1 min) — open with the four stakeholder voices from the brief:
   Rohan's one-click-trading vision, the client's "don't make me learn a new
   system", Nora's "why rebuild what we already have?", Roy's DevSecOps
   ambition. Frame the whole presentation as how we answered all four.
2. What we built (4 min) — one architecture diagram; the five modules mapped
   to real user needs (especially the client's dashboard ask); where GenAI
   concretely helps; 60–90-sec demo framing
3. Operational processes (2 min) — how the platform supports the settlement,
   risk, and system-access workflows the corporate side mapped; the end-to-end
   STP story where business and tech workflows integrate
4. How we worked (4 min) — the Agile rhythm we adapted to 3 weeks; daily
   facilitator touchpoints and milestone feedback sessions and WHAT WE CHANGED
   because of them; GitLab flow (branches, MRs, reviews); CI/CD + Docker +
   Terraform as concrete DevOps practice; how tech and corporate analysts
   collaborated day to day (requirement handoffs, joint prioritisation, one
   real misalignment and how we fixed it)
5. What worked / what didn't (3 min) — honest retrospective; every claim
   backed by a concrete repo/history example; include at least one GenAI
   reality check and one cross-functional friction point
6. What we learned (2 min) — technical (Python, GenAI, cloud/DevOps), domain
   (trade lifecycle, STP, risk/compliance), and professional (project
   management, cross-functional communication) — tie each to a specific moment
7. Roadmap & close (1–2 min) — future recommendations that answer the four
   stakeholders again: scalability and security/SRE (Roy), adoption and change
   management (client), maintainability vs. the old batch system (Nora),
   cost/benefit for CFO buy-in (Rohan)
8. Q&A prep — 10 likely questions with concise answers, including hostile ones
   from each persona's viewpoint (ROI? why not keep the existing batch system?
   security? what happens when GenAI is wrong?)

## Output
- Minute-by-minute flow table: section | slide # | presenter | time | purpose
- Full speaker notes per section in natural spoken language (not
  bullet-reading), including handover lines between presenters
- Demo checklist + fallback plan
- Save everything as `presentation/script.md`
```

---

## Prompt 2 — Slides

```
You are building the SLIDES for our Nomura Tech Graduate Program final team
presentation (MVP trading platform with STP; cross-functional team of
technology + corporate graduates).

First read `presentation/script.md` (if it exists), then scan the repository
(README, docs/, source, CI/CD config, git history) so every slide is grounded
in fact — no invented numbers, features, or claims. Corporate analysts will
refine the deck afterwards, so keep everything easy to edit.

## Configuration (adjust the brackets)
- Format: [PowerPoint .pptx]
- Language: [English]
- Length: [16–18] slides total including title and Q&A, plus an appendix
- Style: clean, professional, appropriate for a financial-industry audience
  (conservative palette, one accent colour); minimal text (short phrases,
  max ~6 bullets per slide); diagrams over paragraphs; consistent diagram and
  icon style throughout

## Required slides (align order with the script)
1. Title — project name, program name, team members, date
2. Agenda
3. The ask — the four stakeholder voices (Rohan / client / Nora / Roy) as four
   short quotes
4. Goals & success criteria — from the brief
5. Platform overview — the five modules at a glance
6. Architecture — ONE clear diagram (client → STP flow → modules → infra)
7. GenAI in the platform — what it concretely does, with one real example
8. Demo — a single slide framing the live demo path (uses simulation data)
9. Operational processes — settlement / risk / system-access workflows and how
   the platform supports them
10. How we worked — 3-week Agile timeline with facilitator touchpoints and
    milestone feedback marked
11. Engineering practices — GitLab flow diagram, CI/CD pipeline, Docker,
    Terraform
12. Cross-functional collaboration — how tech & corporate worked together
    (handoffs, joint decisions, one real misalignment + the fix)
13. Challenges & solutions — 2–3 real ones, structured problem → approach → fix
14. What worked / what didn't — honest two-column retrospective
15. Key learnings — technical / domain / professional, tied to concrete moments
16. Metrics — only honest data (MRs, commits, pipeline runs, test coverage,
    modules delivered)
17. Future roadmap — recommendations answering each stakeholder (scalability,
    DevSecOps/SRE, adoption & change management, cost/benefit)
18. Thank you / Q&A
Appendix: 2–3 technical deep-dive backup slides (e.g., STP trade-flow sequence
diagram, data model, pipeline configuration) for Q&A

## Execution
- Generate the deck and all diagrams, then verify the file actually opens
- Put the speaker notes from the script into the notes section of each slide
- Native shapes and text boxes only — no text baked into images; render
  diagrams as editable shapes or SVG, and keep the diagram sources under
  `presentation/`
```

---

## Tips

- **Order matters:** Prompt 1 produces the script; Prompt 2 reads it. Reviewing the script before generating slides catches factual drift early.
- **The stakeholder-voices framing is your differentiator** — most teams will demo features; opening and closing with Rohan / the client / Nora / Roy shows you understood the *business* problem, which is exactly what the program grades.
- **Be honest about partial modules** — Prompt 1 deliberately flags mocked or partial functionality. Facilitators know what 3 weeks allows; honesty + a credible roadmap beats overclaiming.
- **If the deck language should differ** (e.g., bilingual speaker notes), change the Language bracket in both prompts before running.
- **Corporate analysts own the final polish** per the brief — Prompt 2 keeps everything editable so they can restyle without regenerating.
