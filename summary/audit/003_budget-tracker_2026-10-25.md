# 003 · Бюджетный контур: ловушки статусов и персистентность

**Дата:** 2026-10-25 · **Штамп:** budget-tracker · **Задача:** [T003](../tasks/003_budget-tracker_2026-10-25.md)

## Текущее состояние
`tools/cost_tracker.py` — готовый бюджетный контур: `estimate → reserve → approve_tool →
reconcile/refund`, режимы `observe | warn | cap`, персистентность через `cost_log_path`.
При наивном использовании из MCP — три ловушки:

1. `refund()` (175) и `reconcile()` (165) требуют статус `reserved` (`_require_status`, 521).
   После упавшего `reserve()` запись остаётся `estimated` → `refund()` бросит `ValueError`.
2. Без `cost_log_path` трекер не сохраняет `entries`, `approved_tools`, `budget_total_usd` —
   новый экземпляр на каждый вызов обнуляет одобрения и траты.
3. `CostTracker._save()` пишет `approved_tools` (и `budget_warning*` в режиме warn), а
   `cost_log.schema.json` объявлен с `additionalProperties: false` → файл трекера
   **не проходит** `validate_artifact("cost_log", ...)`.

## Что зафиксировано
- `ApprovalRequiredError` — порог одного действия (`single_action_approval_usd`) и первый платный тул.
- `BudgetExceededError` — превышение `usable_budget_usd` в режиме `cap`.
- `cost_snapshot()` → `total_spent_usd`, `total_reserved_usd`, `budget_remaining_usd`.
- Канонический артефакт `cost_log` из продакшн-кода не пишет никто — только сам трекер.

## Риск
`ValueError` роняет MCP-сервер на ровном месте; одобрения теряются между вызовами;
канонический `cost_log` невалидируем.

## Состояние на 2026-10-25 (после T003)
Контур починен: трекер — один на проект с персистентным state-файлом, `refund()` из `except`
убран, канонический `cost_log` собирается проекцией и проходит валидацию.
Подробности — в [summary/tasks/003](../tasks/003_budget-tracker_2026-10-25.md).

## Связанные задачи
- [T003 · Починить бюджетный контур моста](../tasks/003_budget-tracker_2026-10-25.md)
