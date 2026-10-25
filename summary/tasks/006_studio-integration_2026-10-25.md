# 006 · Описать и реализовать связку «проект ↔ студия»

**Дата:** 2026-10-25 · **Штамп:** studio-integration · **Аудит:** [A006](../audit/006_studio-integration_2026-10-25.md)
**Статус:** ✅ выполнено

## Цель
Результат из внешней студии попадает в проект по правилам workspace, а готовый рендер
уходит в NLE — без ручного копирования и без потери путей.

## Что сделано

| Файл | Что |
|---|---|
| `lib/mcp_bridge.py` | `asset_list()`, `media_path()`, `staging_roots()`, `staging_copy()` |
| `scripts/openmontage_mcp.py` | тулы `om_asset_list`, `om_media_path`, `om_staging_roots`, `om_staging_copy` |
| `docs/mcp/studio-integration.md` | форматы конфигов для 5 клиентов, оба транспорта, полный круг «сгенерировать → забрать → отдать в Resolve», таблица типовых ошибок |
| `tests/lib/test_mcp_bridge_studio.py` | 11 тестов контракта |

**Контракт hand-off:**

| Тул | Что делает |
|---|---|
| `om_asset_list(project_id, kind?)` | файлы workspace по видам (`images`/`video`/`audio`/`music`) с размерами |
| `om_media_path(project_id, rel_path)` | абсолютный путь файла — то, что принимает `comfy_upload_image` или импорт Resolve; путь вне workspace → отказ |
| `om_staging_roots()` | кандидаты staging-корней с `exists`/`writable` |
| `om_staging_copy(project_id, rel_path, target_root?, project_name?, overwrite?)` | копирование в staging-корень с **детерминированной** структурой `<root>/<project_name>/<relative path>` |

**Sandbox Resolve зафиксирован в контракте:** Resolve на macOS не импортирует из
произвольных папок (`imported: 0`), поэтому порядок всегда такой:
`om_staging_roots()` → узнать разрешённые тома у Resolve → `om_staging_copy` → импорт.
Staging-корень по умолчанию `~/Movies/DaVinci Resolve`, переопределяется
`OPENMONTAGE_STAGING_ROOT`.

**Автосоздание корня — на один уровень:** если `target_root` не существует, но его
родитель есть, корень создаётся; если нет и родителя — отказ. Так опечатка в пути
не порождает дерево каталогов.

## Проверено
- `om_media_path` отказывает для пути вне workspace — тест
- `om_staging_copy` создаёт детерминированную структуру — тест
- повторная копировка без `overwrite` → отказ; с `overwrite=true` → проходит — тест
- источник вне проекта → отказ — тест
- корень с отсутствующим родителем → отказ — тест
- `om_staging_roots` показывает источник (env/default) и состояние — тест
- на реальном проекте: 18 audio-файлов, `renders/final.mp4` резолвится, корень Resolve `writable: true`

## Готово, когда
- [x] `om_media_path` отказывает для пути вне workspace — тест
- [x] `om_staging_copy` создаёт детерминированную структуру в целевом томе — тест
- [x] `docs/mcp/studio-integration.md` описывает все 5 клиентов и оба транспорта
