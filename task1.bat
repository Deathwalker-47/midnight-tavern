@echo off
set ANTHROPIC_BASE_URL=
set ANTHROPIC_API_KEY=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
cd /d C:\Users\anuji\Documents\MidnightTavern
echo [%date% %time%] TASK 1 START >> task_log.txt

claude -p "Sprint A Task 1: Build the backend skeleton. Create these files: 1) backend/app/core/config.py - Pydantic Settings class loading from env vars: DATABASE_URL, REDIS_URL, SECRET_KEY, CORS_ORIGINS, DEBUG, APP_NAME='Midnight Tavern'. 2) backend/app/core/logging.py - structlog setup with JSON output, request_id and user_id context binding. 3) backend/app/core/errors.py - Custom exception classes (AppError, NotFoundError, AuthError, ValidationError) and a FastAPI exception handler that returns {'error': {'code': '...', 'message': '...', 'details': ...}, 'request_id': '...'}. 4) backend/app/core/middleware.py - RequestID middleware that generates a UUID per request and adds it to structlog context and response headers. CORS middleware config. 5) backend/app/api/v1/health.py - Router with GET /api/v1/healthz returning {'status': 'ok'} and GET /api/v1/readyz that will check DB later. 6) backend/app/api/v1/router.py - Main v1 router that includes health router. 7) backend/app/main.py - FastAPI app with async lifespan handler (placeholder for DB init), includes the v1 router, mounts middleware, registers error handlers. Use type hints everywhere, async functions, and follow the conventions in CLAUDE.md. After creating all files, commit with message 'feat: backend skeleton with config, logging, middleware, errors, health endpoints' and push to origin main." --model claude-opus-4-6 --output-format json > last_result.json 2> last_error.txt

echo [%date% %time%] TASK 1 EXIT CODE: %ERRORLEVEL% >> task_log.txt
