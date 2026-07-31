# Presentation

- `STP-final-presentation.pptx` — the deck (16:9, 18 slides + 3 appendix). Edit text/diagrams directly in PowerPoint; everything is native shapes.
- `build_deck.py` — generator that produced the pptx; doubles as editable diagram source for bigger layout changes.
- `script.md` — authoritative content source (flow, speaker notes, metrics). Change content here first, then mirror it into the deck or the generator.
- Regenerate: `backend/.venv/Scripts/python presentation/build_deck.py` (needs python-pptx, venv-only — see the script docstring; it re-verifies the deck after writing).
- Rule: every slide must stay grounded in script.md/repo — no invented numbers, features, or claims.
