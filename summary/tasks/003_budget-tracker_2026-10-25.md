# 003 · Починить бюджетный контур моста: трекер на проект, без `refund()` в `except`

**Дата:** 2026-10-25 · **Штамп:** budget-tracker · **Аудит:** [A003](../audit/003_budget-tracker_2026-10-25.md)

## Цель
Бюджетный гейт над MCP работает нативно и не падает: резервирование, одобрение и учёт
переживают границы MCP-вызовов и перезапуск сервера.

## Что сделать
1. `lib/mcp_bridge.py`: `tracker_for(project_id)` — кэш `dict[str, CostTracker]`,
   `cost_log_path=projects/<id>/artifacts/cost_tracker_state.json`, `mode=BudgetMode.CAP`.
   Один экземпляр на проект на весь процесс сервера.
2. `tool_run`: при `ApprovalRequiredError` / `BudgetExceededError` **не** звать `refund()` —
   запись остаётся `estimated`, деньги не зарезервированы (`reserved_usd=0`).
   Ответ: `needs_approval: true`, `cost_entry_id`, `cost_entry_status`.
3. `om_approve_tool` → `tracker.approve_tool(tool)`; одобрение переживает перезапуск сервера.
4. `reconcile()` вызывать только из `reserved` (после успешного `reserve()`).
5. Канонический `cost_log.json` собирать отдельно — проекцией записей трекера на поля
   `cost_log.schema.json` (без `approved_tools`/`budget_warning*`).

## Готово, когда
- [ ] отказ `reserve()` не роняет сервер (нет `ValueError` от `refund`) — тест
- [ ] два вызова `om_tool_run` подряд: одобрения и траты сохранились — тест
- [ ] `cost_log.json` проходит `validate_artifact("cost_log", ...)` — тест
- [ ] `om_budget` показывает spent/reserved/remaining по проекту
