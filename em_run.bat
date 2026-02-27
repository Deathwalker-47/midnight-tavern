@echo off
set ANTHROPIC_BASE_URL=
set ANTHROPIC_API_KEY=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
cd /d C:\Users\anuji\Documents\MidnightTavern
echo [%date% %time%] TASK START >> task_log.txt
claude -p %* --model claude-opus-4-6 --output-format json > last_result.json 2> last_error.txt
echo [%date% %time%] EXIT CODE: %ERRORLEVEL% >> task_log.txt
type last_result.json >> task_log.txt
echo. >> task_log.txt
