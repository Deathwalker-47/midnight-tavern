# PLAN: Sprint A Foundation Aggregation

This document aggregates the active Sprint A planning context previously spread across multiple docs.

## Primary Objective
Deliver a stable foundation for Midnight Tavern with backend scaffolding, core modules, and initial frontend integration.

## Consolidated Workstreams

1. **Core platform foundation**
   - Backend skeleton, config, logging, middleware, health endpoints.
   - Async DB setup and migration baseline.

2. **Core product modules**
   - Auth, users, characters, chats, messages.
   - Provider abstraction + Anthropic integration.

3. **Streaming and frontend wiring**
   - SSE for generation/event streams.
   - Frontend routes/layout + character/chat UI.

4. **Dungeon Master module completion**
   - Apply migrations for DM tables.
   - Wire DM evaluation into chat flow.
   - Add auth scoping for DM resources.
   - Complete frontend DM panel and DM message rendering integration.

## Notes
- Source of truth for detailed sprint task ordering is still maintained in `CLAUDE.md`.
- As planning is migrated, each module plan should be added to `docs/plans/` and listed in `docs/plans/README.md`.
