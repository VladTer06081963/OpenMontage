# Studio Integration — как проект OpenMontage доходит до внешней студии

Как внешний агент (Hermes / Bb / Claude Code / pi / OpenCode) передаёт результат
из проекта OpenMontage в ComfyUI, DaVinci Resolve и CapCut — и обратно.

Тул-контракт описан в [`OpenMontagMCP.md`](../../OpenMontagMCP.md); здесь — только
связка «проект ↔ студия».

---

## 1. Кто есть кто

| Сторона | MCP-сервер | Конфиг на этой машине | Что умеет |
|---|---|---|---|
| Проект | `openmontage` (наш) | `.pi/mcp.json`, `.mcp.json`, `.agents/mcp_config.json`, `opencode.jsonc` | состояние проекта, артефакты, запуск тулов OpenMontage |
| Генерация | `comfyui` | `.agents/mcp_config.json` → `/Users/mac/comfy-mcp/comfy_mcp.py` | локальные image/video/music без API-расходов |
| Монтаж/NLE | `davinci` | `~/.pi/agent/mcp.json` → `davinci-resolve-advanced-mcp` | импорт, таймлайн, маркеры, рендер |
| Монтаж (альт.) | `capcut` | `~/.pi/agent/mcp.json` → `capcut_mcp_server.py` | 86 команд монтажа, субтитры, экспорт |
| Озвучка | `voicebox` | `~/.pi/agent/mcp.json` → HTTP `127.0.0.1:17493/mcp/` | локальный TTS/транскрипция |
| Код | `codegraph` | `opencode.jsonc` | навигация по репозиторию |

### Команды ComfyUI MCP (полный список)

`comfy_status` · `comfy_queue` · `comfy_jobs` · `comfy_history` · `comfy_cancel` ·
`comfy_list_nodes` · `comfy_object_info` · `comfy_templates` · `comfy_run_workflow` ·
`comfy_upload_image` · `comfy_get_image`

### DaVinci Resolve MCP

Сервер составной: каждый тул принимает параметр `action`. **Имена actions зависят от
версии** — в v2 это `media_storage.get_volumes()`, `media_storage.import_to_pool()`,
`create_timeline_from_clips`, `timeline_markers.add()`, `open_page`; в v3 — составные
`media_pool`, `media_analysis`, `timeline`, `timeline_markers`, `project_manager`,
`resolve_control`, `timeline_frame`.

**Правило:** не угадывайте action. Сначала вызовите тул capabilities
(`resolve_control(action="get_version")`, `media_pool(action="ingest_capabilities")`),
затем нужный составной тул. Рабочий пример из `test.md` (v2) — в разделе 5.

---

## 2. Тул-контракт OpenMontage для hand-off

| Тул | Что делает |
|---|---|
| `om_asset_list(project_id, kind?)` | все файлы workspace по видам (`images`/`video`/`audio`/`music`) с размерами |
| `om_media_path(project_id, rel_path)` | **абсолютный** путь файла проекта — то, что нужно внешнему MCP |
| `om_staging_roots()` | кандидаты staging-корней: существует / доступен для записи |
| `om_staging_copy(project_id, rel_path, target_root?, project_name?, overwrite?)` | копирование файла в staging-корень: `<root>/<project_name>/<relative path>` |

### Sandbox Resolve — главное правило

DaVinci Resolve на macOS **не импортирует файлы из произвольных папок**: вызов
`ImportMedia` возвращает `imported: 0`. Импортировать можно только из томов, которые
пользователь явно разрешил.

Порядок действий:

1. Узнать разрешённые тома у Resolve: `media_storage.get_volumes()` (v2) или
   `media_pool(action="ingest_capabilities")` (v3).
2. Скопировать туда файл: `om_staging_copy(...)`.
3. Импортировать: `media_storage.import_to_pool(...)` / `media_pool(action="import")`.

Staging-корень по умолчанию — `~/Movies/DaVinci Resolve` (это и есть стандартная
разрешённая папка на macOS). Переопределяется переменной окружения:

```bash
export OPENMONTAGE_STAGING_ROOT="/Volumes/Production/Ingest"
```

---

## 3. Форматы конфигурации MCP-клиентов

Все клиенты читают одну и ту же структуру, но ключи различаются.

### pi — `~/.pi/agent/mcp.json` (user) или `.pi/mcp.json` (project)

```jsonc
{
  "mcpServers": {
    "openmontage": {
      "command": "/Users/mac/openMontag/.venv/bin/python",
      "args": ["/Users/mac/openMontag/scripts/openmontage_mcp.py"],
      "cwd": "/Users/mac/openMontag",
      "env": { "PYTHONPATH": "/Users/mac/openMontag" },
      "timeout": 120,
      "description": "OpenMontage project control"
    }
  }
}
```

Поддерживает `command`/`args`/`env`/`cwd`, а также `url` + `headers` для HTTP-серверов.
Проектный конфиг читается только после доверия проекту.

### Gemini CLI — `~/.gemini/config/mcp_config.json` (+ копия `.agents/mcp_config.json`)

Тот же формат `mcpServers`. Уже настроены `comfyui`, `davinci-resolve`, `openmontage`.

### Claude Code и Cursor — `.mcp.json` в корне репозитория

Тот же формат `mcpServers`. Либо командами:

```bash
claude mcp add --transport stdio openmontage -- \
  /Users/mac/openMontag/.venv/bin/python /Users/mac/openMontag/scripts/openmontage_mcp.py
```

### OpenCode — `opencode.jsonc`

Другой ключ (`mcp.servers`) и массив вместо строки:

```jsonc
{
  "mcp": {
    "servers": {
      "openmontage": {
        "type": "local",
        "command": [
          "/Users/mac/openMontag/.venv/bin/python",
          "/Users/mac/openMontag/scripts/openmontage_mcp.py"
        ],
        "environment": { "PYTHONPATH": "/Users/mac/openMontag" },
        "disabled": false
      }
    }
  }
}
```

### VS Code — `.vscode/mcp.json`

Формат `mcpServers`, как у Claude Code.

---

## 4. Транспорт

| Режим | Когда | Как |
|---|---|---|
| **stdio** | агент на той же машине | `command` + `args` + `cwd`; сервер живёт вместе с клиентом |
| **streamable HTTP** | удалённый агент или несколько клиентов | `python scripts/openmontage_mcp.py --http` → `127.0.0.1:8765`; `OPENMONTAGE_MCP_HOST` / `OPENMONTAGE_MCP_PORT` |

Для HTTP нужен токен и TLS-терминация перед выставлением наружу: сервер доверяет
локальному пользователю и читает `.env` с ключами провайдеров.

```jsonc
{
  "mcpServers": {
    "openmontage": {
      "url": "http://127.0.0.1:8765/mcp",
      "headers": { "Authorization": "Bearer ${OPENMONTAGE_MCP_TOKEN}" }
    }
  }
}
```

Проверка: `pi mcp list` покажет `openmontage: connected, N tools`.

---

## 5. Полный круг: сгенерировать → забрать → отдать в Resolve

```text
1. om_preflight                       → что доступно (кие/minimax — платно, ComfyUI — локально)
2. om_project_init / om_project_state  → где мы
3. om_tool_run(tool="comfyui_video"…) → генерация (job-режим, если долго)
   ↳ om_job_status / om_job_result     → дождаться
4. om_asset_list(project_id)           → что появилось в assets/
5. om_media_path(project_id, "assets/video/clip.mp4")
                                      → абсолютный путь
6. (ComfyUI) comfy_upload_image       → загрузить результат обратно в ComfyUI,
                                        если он там и был нужен
7. om_staging_roots()                  → куда Resolve разрешил импорт
8. om_staging_copy(project_id, "assets/video/clip.mp4", project_name="My Film")
                                      → /Users/mac/Movies/DaVinci Resolve/My Film/assets/video/clip.mp4
9. (Resolve) media_pool import        → imported: 1
10. (Resolve) timeline create + markers → по scene_plan.json
```

Шаги 7–10 — не гипотетика: этот маршрут отработан в `test.md` (§Шаг 7), включая
создание таймлайна и шесть цветных маркеров сцен.

### Маркеры сцен из `scene_plan.json`

Границы сцен в секундах пересчитываются в кадры таймлайна (`fps` из настроек Resolve)
и ставятся через `timeline_markers.add()` — один маркер на сцену, цвет по типу.
Источник истины — `projects/<id>/artifacts/scene_plan.json`, а не ручные заметки.

---

## 6. Ошибки, которые встречаются на этом маршруте

| Симптом | Причина | Что делать |
|---|---|---|
| `imported: 0` | Resolve не видит путь (sandbox) | `om_staging_copy` в разрешённый том, затем импорт |
| `unknown tool` от ComfyUI | сервер ComfyUI не поднят или `COMFY_URL` неверен | `comfy_status`, проверить `COMFY_URL` в `.env` |
| результат есть в ComfyUI, но не в проекте | генерация шла мимо `projects/<id>/` | `output_path` должен быть внутри workspace — `om_tool_run` это проверяет |
| повторная оплата за ту же генерацию | клиент перезапустил вызов после таймаута | `om_tool_run(mode="job")` + `om_job_status`; идентичный вызов вернёт тот же `job_id` |
| файл есть, но доска его не показывает | ассет вне `projects/<id>/` | путь-контракт: только `projects/<id>/assets/...` |

---

## 7. Чек-лист развёртывания

- [ ] `om_staging_roots()` возвращает корень с `writable: true`
- [ ] Resolve видит этот корень в своём списке томов
- [ ] `om_staging_copy` создаёт `<root>/<project>/<relative path>`
- [ ] импорт через Resolve даёт `imported: 1`
- [ ] `om_media_path` отдаёт путь, который ComfyUI MCP принимает в `comfy_upload_image`
- [ ] `pi mcp list` показывает `openmontage`, `comfyui`, `davinci` как `connected`
