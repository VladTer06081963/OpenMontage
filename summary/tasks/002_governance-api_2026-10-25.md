# 002 · Добавить публичный `append_decision_log()` и вести решения без похода через стадию

**Дата:** 2026-10-25 · **Штамп:** governance-api · **Аудит:** [A002](../audit/002_governance-api_2026-10-25.md)
**Статус:** ✅ выполнено

## Цель
Фиксировать решение **посреди стадии**, не трогая статус стадии и не лезя в приватный API.

## Что сделано

| Файл | Что |
|---|---|
| `lib/checkpoint.py` | публичные `append_decision_log(pipeline_dir, project_id, decisions)` и `read_decision_log(pipeline_dir, project_id)` |
| `lib/mcp_bridge.py` | `decision_append()`, `decision_read()` — мост поверх публичного API |
| `scripts/openmontage_mcp.py` | тулы `om_decision_append`, `om_decision_read` |
| `tests/lib/test_mcp_bridge_phase1.py` | 15 тестов контракта (в т.ч. 5 на решения) |

**Контракт `append_decision_log`:**
- принимает одну запись или список;
- валидирует по `schemas/artifacts/decision_log.schema.json` **до** записи — невалидная
  категория или лишний ключ (`additionalProperties: false`) → `CheckpointValidationError`;
- append-only: существующие записи не мутируются, дедуп по `decision_id`;
- ревизия = **новая** запись с тем же `(category, subject)` — так доска Backlot помечает
  изменённое решение («revised»);
- не трогает `checkpoint_<stage>.json` вообще.

`om_decision_append` в ответе возвращает `revises: [<decision_id>]` — агенту видно, что он
пересматривает прежний выбор, а не плодит дубли.

## Проверено
- `pytest tests/lib/test_mcp_bridge_phase1.py` → **15 passed**
- решение в стадии `proposal` → `checkpoint_*.json` не создаются (тест)
- ревизия → в логе 2 записи, первая не тронута (тест)
- `category="not_a_real_category"` и `confidence=5.0` → отказ (тест)

## Готово, когда
- [x] `append_decision_log` валидирует по схеме и пишет `projects/<id>/decision_log.json`
- [x] решение посреди стадии не меняет статус чекпоинта (тест)
- [x] повторная запись с тем же `(category, subject)` не мутирует предыдущую (тест)
- [x] невалидная категория или лишний ключ → понятная ошибка, а не тихая запись
