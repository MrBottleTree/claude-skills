# claude-skills

A personal collection of custom skills for [Claude Code](https://claude.ai/claude-code).

## Installing a skill

Copy the skill folder into your Claude skills directory and restart Claude Code:

```bash
# macOS / Linux
cp -r <skill-name> ~/.claude/skills/

# Windows
xcopy /E /I <skill-name> %USERPROFILE%\.claude\skills\<skill-name>
```

---

## Skills

### `study-buddy`

A deep-learning study companion for PDF-based course materials. Extracts slide content using Gemini Vision (with Claude as fallback), then teaches with exhaustive detail — analogies, worked examples, diagrams, mid-slide checkpoints, and quizzes.

**Dependencies:**
```bash
pip install pymupdf Pillow
npm install -g @google/gemini-cli   # then: gemini auth login
```

**Usage:**

1. Navigate to your slides folder in Claude Code.

2. Extract and set up all slides:
   ```
   /study-buddy init
   ```
   This converts any `.pptx` files to PDF and extracts every slide into a per-slide directory (`<deck>_extracted/`).

3. If your slides are one big dump folder and you want them organized by topic first:
   ```
   /study-buddy organize <folder>
   ```
   This analyzes the course structure, groups slides into modules, and generates a `course_map.md`.

4. Start a study session:
   ```
   /study-buddy teach
   ```
   Or jump to a specific topic:
   ```
   /study-buddy <topic>
   ```

Claude will synthesize the course structure first, then teach every concept with analogies, diagrams, worked examples, and comprehension checkpoints. At the end of each session it generates a cheat-sheet summary and a cumulative quiz.
