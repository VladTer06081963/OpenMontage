# 002 · Добавить публичный `append_decision_log()` и вести решения без похода через стадию

**Дата:** 2026-10-25 · **Штамп:** governance-api · **Аудит:** [A002](../audit/002_governance-api_2026-10-25.md)

## Цель
Фиксировать решение **посреди стадии**, не трогая статус стадии и не лезя в приватный API.

## Что сделать
1. В `lib/checkpoint.py` добавить публичную
   `append_decision_log(pipeline_dir, project_id, new_log) -> Path`:
   собрать `{"version": "1.0", "project_id": ..., "decisions": [...]}` →
   `validate_artifact("decision_log", payload)` (`schemas/artifacts/__init__.py`) →
   делегировать `_merge_decision_log`.
2. `_merge_decision_log` остаётся приватной; мост использует только публичную обёртку.
3. Мост: `om_decision_append` → `append_decision_log`; `checkpoint_<stage>.json` не меняется.
4. Контракт: собирать ровно поля схемы — лишние ключи (напр. `notes`) дают ошибку валидации.
5. Пересмотр решения = новая запись с тем же `(category, subject)`; старая остаётся неизменной.

## Готово, когда
- [ ] `append_decision_log` валидирует по схеме и пишет `projects/<id>/decision_log.json`
- [ ] решение посреди стадии не меняет статус чекпоинта (тест)
- [ ] повторная запись с тем же `(category, subject)` не мутирует предыдущую (тест)
- [ ] невалидная категория или лишний ключ → понятная ошибка, а не тихая запись
