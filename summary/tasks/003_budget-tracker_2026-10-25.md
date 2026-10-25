# 003 · Починить бюджетный контур моста: трекер на проект, без `refund()` в `except`

**Дата:** 2026-10-25 · **Штамп:** budget-tracker · **Аудит:** [A003](../audit/003_budget-tracker_2026-10-25.md)
**Статус:** ✅ выполнено

## Цель
Бюджетный гейт над MCP работает нативно и не падает: резервирование, одобрение и учёт
переживают границы MCP-вызовов и перезапуск сервера.

## Что сделано

| Файл | Что |
|---|---|
| `lib/mcp_bridge.py` | `tracker_for(project_id)` — кэш трекеров + state-файл `projects/<id>/.cost_tracker_state.json`; `tool_estimate()`, `tool_run()`, `tool_approve()`, `budget()`, `_write_cost_log()` |
| `scripts/openmontage_mcp.py` | тулы `om_tool_estimate`, `om_tool_run`, `om_approve_tool`, `om_budget` |
| `tests/lib/test_mcp_bridge_phase1.py` | 15 тестов (в т.ч. 7 на бюджет) |

**Жизненный цикл вызова (в порядке, который важен):**

```
1. доступность тула          -> если недоступен: install_instructions, не молчание
2. проверка output_path      -> путь обязан быть внутри projects/<id>/
3. dry_run()                 -> цена ДО вызова (BaseTool.dry_run)
4. tracker.estimate()        -> запись в статусе estimated
5. tracker.reserve()         -> ApprovalRequiredError / BudgetExceededError
6. execute()                 -> только после успешного reserve
7. tracker.reconcile()       -> spent = фактические деньги
```

**Ключевые решения**

- **`refund()` в `except` убран.** После упавшего `reserve()` деньги не зарезервированы,
  запись остаётся в статусе `estimated`, а `refund()` требует `reserved` и бросил бы
  `ValueError`, убив сервер. Ответ моста: `needs_approval: true` + `cost_entry_status: "estimated"`.
- **Трекер — один на проект**, с `cost_log_path`: `_save()`/`_load()` сохраняют `entries`,
  `approved_tools` и `budget_total_usd` между MCP-вызовами и перезапусками сервера.
  Проверено тестом: «свежий» `CostTracker` с тем же state-файлом восстанавливает одобрения.
- **Канонический `cost_log` — проекция.** State-файл трекера не является артефактом
  (в нём `approved_tools` и `budget_warning*`, а схема запрещает лишние ключи). Артефакт
  `projects/<id>/artifacts/cost_log.json` собирается отдельно и проходит `validate_artifact`.
- **Порядок проверок в `reserve()`** (важно для предсказуемости): порог одного действия →
  первый платный тул → бюджет. Поэтому «нужно одобрение» приходит раньше «превышен бюджет».

## Проверено
- `pytest tests/lib/test_mcp_bridge_phase1.py` → **15 passed**
- отказ `reserve()` → сервер жив, запись `estimated`, reserved = 0 (тест)
- `om_approve_tool` → следующий вызов проходит (тест)
- превышение бюджета → `budget_exceeded: true`, spent = 0 (тест)
- `output_path` вне workspace → `BridgeError` (тест)
- `cost_log.json` проходит `validate_artifact` (тест)

## Готово, когда
- [x] отказ `reserve()` не роняет сервер (нет `ValueError` от `refund`)
- [x] два вызова `om_tool_run` подряд: одобрения и траты сохранились (state-файл)
- [x] `cost_log.json` проходит `validate_artifact("cost_log", ...)`
- [x] `om_budget` показывает spent/reserved/remaining по проекту

## Смежное (сделано в рамках Фазы 1)

- `om_project_init`, `om_checkpoint_write`, `om_artifact_write` — тонкие обёртки над
  `lib/checkpoint.py`; гейт не обходится (проверено тестом).
- `om_decision_append` / `om_decision_read` — см. T002.
- `mode="job"` для долгих генераций — остаётся **T004** (клиентский таймаут 60–120 с).
