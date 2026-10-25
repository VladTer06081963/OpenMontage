# 006 · Связка «проект ↔ внешняя студия» не описана

**Дата:** 2026-10-25 · **Штамп:** studio-integration · **Задача:** [T006](../tasks/006_studio-integration_2026-10-25.md)

## Текущее состояние
MCP-студии настроены и работают: ComfyUI (`.agents/mcp_config.json`), DaVinci Resolve
(`davinci-resolve-advanced-mcp` в `~/.pi/agent/mcp.json`, 18 инструментов), CapCut (86 команд),
Voicebox, LeronX, Codegraph. В `test.md` §Шаг 7 — рабочий кейс: импорт в Media Pool,
таймлайн, маркеры сцен по `scene_plan.json`.

Но в спецификации моста нет операций, которые отдают агенту файл проекта наружу:
`om_media_path` и `om_asset_list` намечены, но без сигнатур, формата ответа и правил.

Отдельная ловушка: DaVinci Resolve на macOS не импортирует файлы из произвольных папок —
нужен разрешённый том `/Users/mac/Movies/DaVinci Resolve/`.

## Что зафиксировано
- Форматы MCP-конфигов различаются по клиентам: `mcpServers` (pi, Gemini CLI, Claude Code)
  против `mcp.servers` (OpenCode).
- ComfyUI — локально, 0 API-расходов; TTS в реестре не сконфигурирован (0/18).
- Пайплайн требует, чтобы любой `output_path` лежал в `projects/<id>/` — иначе доска его не видит.

## Риск
Агент генерирует в ComfyUI и не может отдать результат в пайплайн (или наоборот) —
ассеты остаются вне `projects/<id>/`, доска пуста, гейт `assets` ревьюит нечего.

## Состояние на 2026-10-25 (после T006)
Hand-off реализован: `om_asset_list`, `om_media_path`, `om_staging_roots`,
`om_staging_copy` + документ `docs/mcp/studio-integration.md`. Sandbox Resolve
зафиксирован в контракте. Подробности — в
[summary/tasks/006](../tasks/006_studio-integration_2026-10-25.md).

## Связанные задачи
- [T006 · Paths, staging и порядок вызовов «студия ↔ проект»](../tasks/006_studio-integration_2026-10-25.md)
