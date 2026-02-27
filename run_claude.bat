@echo off
set ANTHROPIC_BASE_URL=
set ANTHROPIC_API_KEY=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
cd /d C:\Users\anuji\Documents\MidnightTavern
echo [%time%] Starting claude... 1>&2
claude -p "Say hello." --model claude-opus-4-6 2>run_err.txt
echo [%time%] Exit code: %ERRORLEVEL% 1>&2
