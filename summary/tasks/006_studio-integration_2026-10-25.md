# 006 · Описать и реализовать связку «проект ↔ студия»

**Дата:** 2026-10-25 · **Штамп:** studio-integration · **Аудит:** [A006](../audit/006_studio-integration_2026-10-25.md)

## Цель
Результат из внешней студии попадает в проект по правилам workspace, а готовый рендер
уходит в NLE — без ручного копирования и без потери путей.

## Что сделать
1. `om_asset_list(project_id, kind?)` → список `assets/{images,video,audio,music}` с размерами.
2. `om_media_path(project_id, rel_path)` → абсолютный путь, с проверкой что он внутри workspace.
3. `om_staging_copy(project_id, rel_path, target)` → копирование в разрешённый том Resolve
   (`/Users/mac/Movies/DaVinci Resolve/`) с понятной ошибкой импорта.
4. Документ `docs/mcp/studio-integration.md`: форматы конфигов для pi / Gemini CLI / OpenCode /
   Claude Code / Cursor; разница stdio и streamable HTTP; порядок вызовов
   «сгенерировать в ComfyUI → забрать в проект → импортировать в Resolve».
5. Учесть sandbox Resolve: файлы импортируются только из разрешённых томов.

## Готово, когда
- [ ] `om_media_path` отказывает для пути вне workspace — тест
- [ ] `om_staging_copy` создаёт детерминированную структуру в целевом томе — тест
- [ ] `docs/mcp/studio-integration.md` описывает все 5 клиентов и оба транспорта
