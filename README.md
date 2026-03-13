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

## Skills

| Skill | Description |
|---|---|
| [`study-buddy`](study-buddy/) | Deep-learning study companion for PDF-based course materials. Extracts slides with Gemini Vision, teaches with diagrams, worked examples, and quizzes. |
