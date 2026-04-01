# OpenClaw Implementation Guide (Based on This Repository)

This document is a practical instruction set for implementing the Life AI Assistant bot in OpenClaw using the current repository as the source of truth.

## 1) Goal

Build a production-grade assistant in OpenClaw with the same business capabilities as this repo, but with cleaner architecture and stronger reliability:

- YouGile local-first workspace sync (projects, boards, columns, tasks)
- Task creation flow with two branches:
  - without time (store in YouGile/local DB)
  - with time (store in YouGile + schedule in calendar)
- Calendar local-first behavior with sync to Google
- Conversational orchestration layer (agent-driven decisions)
- No template spam, no brittle keyword-only routing

## 2) Source of truth in this repo

Use these modules as behavioral reference:

- `app/bot/handlers.py` - Telegram conversation flows and wizard behavior
- `app/ai/agent_orchestrator.py` - tool-driven orchestration loop
- `app/integrations/yougile.py` - YouGile auth/data calls
- `app/services/yougile_sync_service.py` - local-first YouGile sync logic
- `app/services/calendar_domain_service.py` and `app/services/calendar_sync_service.py` - calendar write/sync pattern
- `app/database/models.py` - data model baseline
- `app/main.py` - API endpoints and mini app routes

Do not copy handler-level hacks as-is. Reuse domain behavior, then redesign flow with explicit state machines.

## 3) Mandatory architecture for OpenClaw

Use clean architecture (ports/adapters):

- **Domain layer**
  - entities/value objects
  - use-cases (pure business logic)
- **Application layer**
  - orchestration services
  - workflow state machines
- **Infrastructure layer**
  - adapters for Telegram/OpenClaw transport
  - YouGile adapter
  - Google Calendar adapter
  - persistence adapter

### Required ports

- `YouGilePort`
- `CalendarPort`
- `MemoryPort`
- `TaskRepositoryPort`
- `WorkspaceRepositoryPort`
- `EventBusPort` (optional but recommended)

### Required use-cases

- `SyncWorkspaceUseCase`
- `CreateTaskUseCase`
- `AttachScheduleUseCase`
- `MoveTaskUseCase`
- `UpdateTaskMetadataUseCase` (description/stickers/assignees)
- `PlanUnscheduledTasksUseCase`

## 4) Data model requirements

Preserve/implement these concepts:

- Workspace:
  - project
  - board
  - column (status container)
- Task:
  - `task_id` (external)
  - `project_id`, `board_id`, `column_id`
  - `status_flag` (derived from column)
  - `title`, `description`
  - `stickers`, `assignees`
  - `ready_for_calendar` (bool)
  - `linked_calendar_event_id` (nullable)
  - sync metadata (`updated_at`, `deleted`, source payload hash/etag)

Calendar mapping:

- if task scheduled -> create/update calendar event
- if event linked -> store back-reference in task

## 5) Workflow requirements (user-facing)

Implement a deterministic wizard state machine for "New Task":

1. Start new task
2. Ask "Do you know when to do it?"
3. Capture description
4. Select project
5. Select board
6. Select column
7. Add stickers/assignees
8. Branch:
   - no time -> save task only
   - yes time -> open date/time picker (Mini App equivalent) -> save task + create calendar event
9. Confirm summary
10. Commit and return result

No hidden side effects before final confirm.

## 6) Reliability rules (non-negotiable)

- Idempotency key for every mutating operation
- Outbox pattern for external sync
- Retry with exponential backoff for YouGile/Calendar
- Timeout on every external call
- Structured logs with trace/request id
- Explicit typed errors (no silent broad `except`)
- Dead-letter or retry budget for permanent failures

## 7) YouGile integration specifics

Follow auth/access pattern similar to `yougilego` behavior:

- companies -> select target company
- keys list/create key
- fetch projects, boards, columns, tasks

Reference:
- [yougilego repository](https://github.com/Hazardooo/yougilego)

Important:

- company selection must be strict (name/id), no silent fallback to wrong company
- store and expose project/board/column relationships explicitly
- handle encoding inconsistencies safely

## 8) Calendar integration specifics

- Local DB is primary read model
- Google Calendar is sync target/source
- Mutations first write locally, then outbox push
- Pull sync must not overwrite unsynced local mutation incorrectly

## 9) Testing strategy required

### Unit tests

- all use-cases
- state machine transitions
- mapping logic (YouGile <-> domain <-> persistence)

### Integration tests

- YouGile adapter mocked HTTP contract tests
- Calendar adapter mocked contract tests
- outbox retry behavior

### E2E tests

At least these scenarios:

- create task without time
- create task with time and calendar creation
- move task between columns
- update stickers/assignees
- sync workspace and verify project/board/column counts
- recover from temporary YouGile 401/429

## 10) Migration plan from this repo

1. Freeze behavior with acceptance scenarios from current bot.
2. Extract domain contracts and DTOs.
3. Implement repositories and adapters.
4. Implement use-cases and state machines.
5. Integrate OpenClaw transport layer.
6. Run shadow mode (compare decisions/results with current bot).
7. Cut over feature by feature.

## 11) Definition of Done

Feature is done only if:

- all acceptance tests green
- no unresolved retry-loop errors in logs
- deterministic state transitions for wizard
- project/board/column/task relations persisted and queryable
- scheduling branch creates linked calendar events
- no template-only fallback response for core flows

## 12) Prompt template for OpenClaw agent

Use this as task prompt:

```text
Implement feature set X for Life AI Assistant using clean architecture.
Repository behavior reference: OPENCLAW_BOT_IMPLEMENTATION_GUIDE.md and current app modules.

Constraints:
- Preserve domain behavior, not handler-level hacks.
- Use ports/adapters + use-cases + state machine.
- Add tests first for acceptance scenarios.
- No silent fallback to wrong YouGile company.
- Local-first persistence with outbox sync.

Deliver:
1) Design summary (modules/interfaces)
2) Code changes by layer
3) Tests (unit/integration/e2e)
4) Migration notes
5) Risk list + mitigations
```

---

If OpenClaw proposes shortcuts that violate reliability or explicit state handling, reject and request strict implementation per this guide.
