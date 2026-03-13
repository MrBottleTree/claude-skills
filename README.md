# claude-skills

A collection of custom skills for [Claude Code](https://claude.ai/claude-code).

## What are skills?

Skills are modular packages that extend Claude's capabilities with specialized workflows, domain knowledge, and bundled tools. Each skill lives in its own folder and is loaded automatically when relevant.

## Skills

### `study-buddy`

A deep-learning study companion for PDF-based course materials.

- Extracts slide content using Gemini Vision (with Claude as fallback)
- Stores each slide in its own file — no context limits from large decks
- Teaches with exhaustive detail: analogies, worked examples, diagrams, quizzes
- Supports `/study-buddy init`, `/study-buddy organize`, and `/study-buddy teach`

**Dependencies:** `pip install pymupdf Pillow`, `npm install -g @google/gemini-cli`

## Installing a skill

Copy the skill folder into your Claude skills directory:

```bash
# macOS / Linux
cp -r study-buddy ~/.claude/skills/

# Windows
xcopy /E /I study-buddy %USERPROFILE%\.claude\skills\study-buddy
```

Then restart Claude Code — the skill is picked up automatically.
