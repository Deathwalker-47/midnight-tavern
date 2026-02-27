# Midnight Tavern — Bootstrap Troubleshooting & Lessons Learned

Date: 2026-02-27
Session: Initial project bootstrap via EM skill + Desktop Commander

---

## Issue 1: PowerShell Commands Fail Constantly

**Symptom:** Nearly every command run via Desktop Commander in PowerShell fails with parsing errors, escaping issues, or unexpected behavior.

**Fix:** Set Desktop Commander default shell to cmd permanently:
```
desktop-commander:set_config_value key="defaultShell" value="cmd"
```
**Rule:** Never use PowerShell. All commands via cmd only.

---

## Issue 2: GitHub Fine-Grained PAT Lacks Permissions

**Symptom:** 403 on repo creation and git push.

**Fix:** Use a **classic PAT** (ghp_xxx) with `repo` scope. Fine-grained tokens (github_pat_xxx) cause repeated permission blocks.

---

## Issue 3: Git Commit Message Quoting in cmd

**Symptom:** `git commit -m "multi word message"` splits at spaces.

**Fix:** Use single-word messages (`git commit -m feat-skeleton`) or let Claude Code handle commits internally.

---

## Issue 4: Always Test API Independently First

**Fix:** Save a test_api.py script and run it to verify the API key works before debugging Claude Code.

---

## Issue 5: Claude Code Hangs with No Output (THE BIG ONE)

**Root Cause:** Ink TUI framework needs raw stdin. Desktop Commander doesn't provide a TTY.

**Fix:** Redirect stdin from NUL:
```cmd
claude -p "prompt" --output-format text < NUL 2>&1
```

**Diagnostic:** Run `claude doctor 2>&1` to see the Ink error.

---

## Issue 6: Claude Code Asks Questions Instead of Executing

**Fix:** Write prompt to file, pipe it:
```cmd
type task_prompt.txt | claude -p - --dangerously-skip-permissions --output-format text 2>&1
```
Start prompts with "DO NOT ASK QUESTIONS. Execute immediately."

---

## Issue 7: cmd Quoting Breaks Complex Commands

**Fix:** Never inline complex JSON in cmd. Write to script files (.py, .bat, .txt) and execute those.

---

## Working Claude Code Pattern

```cmd
REM 1. Write prompt to task_prompt.txt via desktop-commander:write_file
REM 2. Run:
cd /d C:\Users\anuji\Documents\MidnightTavern && set ANTHROPIC_BASE_URL= && set ANTHROPIC_API_KEY=KEY && type task_prompt.txt | claude -p - --model claude-sonnet-4-5-20250929 --dangerously-skip-permissions --output-format text 2>&1
```

Key: `-p -` reads from pipe, `--dangerously-skip-permissions` skips dialogs, `< NUL` only if NOT piping.
