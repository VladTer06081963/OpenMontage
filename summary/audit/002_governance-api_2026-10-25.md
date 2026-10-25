# 002 · Публичный API для решений — пробел

**Дата:** 2026-10-25 · **Штамп:** governance-api · **Задача:** [T002](../tasks/002_governance-api_2026-10-25.md)

## Текущее состояние
Записать решение в `decision_log.json` штатным путём нельзя: `lib/checkpoint.py::write_checkpoint`
требует полный набор артефактов стадии, валидирует их и меняет статус стадии.
Append-only merge живёт в `_merge_decision_log` (строка 392) — но функция приватная.

## Что зафиксировано
- `write_checkpoint` (422) → `_stage_requires_approval` (251), `_enforce_stage_prerequisites` (284),
  `_validate_artifacts_for_stage` (124), `_merge_decision_log` (392), `_archive_superseded_checkpoint` (349).
- Fail-closed: неизвестный `pipeline_type` → `CheckpointValidationError`, а не тихое отключение гейта.
- `decision_log.schema.json`: `additionalProperties: false`; `category` — закрытый enum из 17 значений;
  обязательны `decision_id, stage, category, subject, options_considered[], selected, reason`.
- Append-only: дедуп по `decision_id`; смена решения = **новая** запись с тем же `(category, subject)`.

## Риск
Решения фиксируются в обход аудита или теряются — доска продолжает показывать устаревший
выбор провайдера/рантайма, и это читается как «система решила сама».

## Состояние на 2026-10-25 (после T002)
Пробел закрыт: в `lib/checkpoint.py` появились публичные `append_decision_log()` и
`read_decision_log()` с валидацией по схеме; мост использует только их.
Подробности — в [summary/tasks/002](../tasks/002_governance-api_2026-10-25.md).

## Связанные задачи
- [T002 · Публичный `append_decision_log()`](../tasks/002_governance-api_2026-10-25.md)
