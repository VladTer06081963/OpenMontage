# OpenMontage × MCP

Как управлять проектом OpenMontage из внешнего агента (Hermes, Bb, Claude Code, pi, OpenCode, Cursor) через Model Context Protocol.

Документ описывает: **(1)** как это выглядит прямо сейчас, **(2)** чего не хватает для полного управления проектом извне, **(3)** как это достроить.

Дата среза: текущее состояние репозитория `/Users/mac/openMontag`.

---

## 0. TL;DR

- **Внутри OpenMontage MCP-сервера не было** — он реализован 2026-10-25 (Фаза 0, read-only):
  `lib/mcp_bridge.py` + `scripts/openmontage_mcp.py`, 11 тулов, зарегистрирован в
  pi / Claude Code / Gemini CLI / OpenCode. Статус — раздел 3.7.
- **Система instruction-driven**: агент читает YAML-манифесты пайплайнов и директорские скиллы, а вызывает Python-инструменты через реестр `tools/tool_registry.py`.
- **MCP уже используется как мост «агент → студия»**: ComfyUI MCP, DaVinci Resolve MCP, CapCut, Voicebox, LeronX, CodeGraph. Это работает сегодня.
- **Чтобы внешний агент (Hermes/Bb) управлял всем проектом**, нужен обратный мост: тонкий MCP-сервер `openmontage_mcp` поверх реестра + `lib/checkpoint.py` + `skills/`. Фаза 0 сделана, остальное — раздел 4.
- **Гейты, бюджет и аудит не переносятся в MCP-протокол** — они остаются в коде (`lib/checkpoint.py`), MCP лишь доставляет команды и ответы. Иначе внешний агент сможет обойти approval.

---

## 1. Как это выглядит сейчас

### 1.1. Что такое OpenMontage технически

```
внешний агент (Hermes / Bb / pi / Claude Code)
        │  читает инструкции
        ▼
skills/pipelines/<pipeline>/<stage>-director.md   ← «как делать стадию»
skills/meta/*.md                                 ← reviewer, checkpoint-protocol, taste-direction
pipeline_defs/<pipeline>.yaml                    ← стадии, гейты, quality gates
        │  вызывает инструменты
        ▼
tools/tool_registry.py  →  BaseTool.execute(params) → ToolResult
        │  пишет артефакты и чекпоинты
        ▼
projects/<project-id>/{artifacts,assets,renders,checkpoint_*.json,decision_log.json}
        │  читает
        ▼
backlot/  (FastAPI + SSE) → живая доска в браузере
```

Три слоя знаний (обязательны к прочтению):

| Слой | Где | Что даёт |
|---|---|---|
| Layer 1 — инструменты | `tools/` | что существует, статус, цена, fallback |
| Layer 2 — пайплайны | `skills/pipelines/`, `pipeline_defs/` | как использовать в контексте стадии |
| Layer 3 — вендоры | `.agents/skills/` | промпт-инжиниринг конкретного провайдера |

Агент в OpenMontage — это «интеллект», который читает эти слои. Код в Python — только транспорт и персистентность.

### 1.2. Почему MCP-сервера нет внутри

Это осознанное архитектурное решение: MCP — протокол для вызова инструментов, а в OpenMontage интеллект живёт в инструкциях. MCP-обёртка поверх реестра потребовала бы дублировать `skills/` в промптах внешнего агента и рисковала бы обойти гейты.

Практическое следствие: **агент, который не умеет читать репозиторий (или не имеет к нему доступа), не может вести пайплайн** — у него нет ни манифестов, ни директорских скиллов.

### 1.3. Реальное состояние MCP-точек входа (на этой машине)

| Конфиг | MCP-серверы | Назначение |
|---|---|---|
| `/Users/mac/openMontag/.agents/mcp_config.json` (копия — `~/.gemini/config/mcp_config.json`) | `comfyui` → `/usr/bin/python3 /Users/mac/comfy-mcp/comfy_mcp.py` (env: `COMFY_URL=http://192.168.50.97:8188`, `COMFY_TEMPLATES`, `COMFY_OUT_DIR`) | локальная генерация: `comfy_status`, `comfy_run_workflow`, `comfy_upload_image`, `comfy_get_image`, `comfy_cancel`, `comfy_templates`, `comfy_history` |
| там же | `davinci-resolve` → `~/Library/Application Support/davinci-resolve-mcp/venv/bin/python src/server.py` | NLE: импорт, таймлайн, маркеры, рендер |
| `/Users/mac/openMontag/opencode.jsonc` | `codegraph` (`codegraph serve --mcp`) | навигация по коду репозитория |
| `/Users/mac/.pi/agent/mcp.json` | `leronx`, `voicebox` (`http://127.0.0.1:17493/mcp/`), `davinci` (`davinci-resolve-advanced-mcp`), `capcut` | видео-пайплайн, локальная озвучка, монтаж, CapCut-команды |

В этой сессии pi видны `mcp__davinci` (18 инструментов: `media`, `editorial`, `conform`, `color_trace`, `deliverable`, `pipeline`, `project_db`, `capabilities`…), `mcp__capcut` (86 команд), `mcp__leronx`, `mcp__voicebox`.

**Вывод:** MCP уже даёт агенту управление *студией* (генерация, монтаж, NLE), но не управление *проектом OpenMontage* (пайплайн, стадии, артефакты, гейты, доска).

### 1.4. Снимок возможностей (preflight)

```bash
cd /Users/mac/openMontag
.venv/bin/python -c "
from tools.tool_registry import registry; import json
registry.discover(); print(json.dumps(registry.provider_menu_summary(), indent=2))"
```

Фактические значения сейчас:

| Метрика | Значение |
|---|---|
| Инструментов в реестре | **158** |
| Доступно (status=available) | **45** |
| Недоступно (нет ключа/рантайма) | **109** |
| Семейств capabilities | **27** |
| Composition runtimes | `ffmpeg: true`, `remotion: true`, `hyperframes: true` (3/3) |
| Пайплайнов в `pipeline_defs/` | **13** |

Ключевые capability-строки (`configured/total`):

```
video_generation      2/34      image_generation   2/30      tts             0/18
analysis              8/15      video_post         9/10      character_anim  6/6
subtitle              2/2       audio_processing   2/2       screen_capture  2/2
publish               1/1       3d_world_generation 1/1      music_search    1/2
avatar                0/5       music_generation   0/6       music_library   0/1
```

То есть типичная сессия на этой машине: **видео есть только через kie.ai/minimax, TTS не настроен вовсе, локальный пост-продакшн (ffmpeg/remotion/hyperframes) — полный.** ComfyUI вынесен в отдельный MCP-канал (сам tool `comfyui_*` в реестре не сконфигурирован, но сервер доступен по MCP).

Runtime-warning, который надо показывать пользователю дословно: `comfyui_video` — «The top-level resource_profile is a ComfyUI provider floor, not a promise that every workflow fits 8GB VRAM…».

### 1.5. Что агент видит по проекту сегодня (контур управления)

Всё это — **файлы на диске**, а не MCP:

| Объект | Путь | API |
|---|---|---|
| Инициализация проекта | `projects/<id>/project.json` + `artifacts/ assets/ renders/` | `lib/checkpoint.py::init_project(project_id, title=, pipeline_type=, style_playbook=)` |
| Чекпоинт стадии | `projects/<id>/checkpoint_<stage>.json` | `write_checkpoint(pipeline_dir, project_id, stage, status, artifacts, human_approval_required=, human_approved=, review=, cost_snapshot=, metadata=)` |
| Чтение состояния | — | `read_checkpoint`, `get_latest_checkpoint`, `get_completed_stages`, `get_next_stage(pipeline_dir, project_id, pipeline_type)` |
| Артефакты стадий | `projects/<id>/artifacts/*.json` | валидация по `schemas/artifacts/*.schema.json` |
| Decision log | `projects/<id>/decision_log.json` | append-only, merge по `decision_id`, схема `schemas/artifacts/decision_log.schema.json` |
| История перезапусков | `projects/<id>/history/` | автоархив при перезаписи чекпоинта |
| События/события-лог | `projects/<id>/events.jsonl` | пишется инструментарией `BaseTool` |
| Доска | HTTP `127.0.0.1:4750` | `python -m backlot open <id>`; API: `/api/health`, `/api/projects`, `/api/project/{id}/state`, `/api/project/{id}/events` (SSE), `/media/...`, `/thumb/...` |

Стадии пайплайна (пример, `animated-explainer`): `research → proposal → script → scene_plan → assets → edit → compose → publish`. Манифест — единственный источник истины по тому, какая стадия гейтовая (`human_approval_default`), какие тулы доступны (`tools_available`), и что проверяет ревьюер (`review_focus`).

Decision log: категории зафиксированы схемой — `pipeline_selection`, `provider_selection`, `render_runtime_selection`, `composition_mode`, `music_source`, `voice_selection`, `approval_policy` и др. Правило: при смене решения **дописывается новая запись с тем же `(category, subject)`**, старая не мутируется.

### 1.6. Референс: агент уже управлял проектом через MCP

`test.md` §«Шаг 7. Интеграция в DaVinci Resolve через MCP» — рабочий кейс на этом же репозитории:

1. `media_storage.get_volumes()` → найден разрешённый том `/Users/mac/Movies/DaVinci Resolve/` (Resolve на macOS не импортирует файлы из произвольных папок — sandbox).
2. Копирование `final.mp4` + `master_voiceover.mp3` в этот каталог.
3. `media_storage.import_to_pool()` → `imported: 2`.
4. `create_timeline_from_clips` → таймлайн `How AI Sees The World`, дорожки V1/A1.
5. Пересчёт границ сцен из секунд в кадры (24 fps) → `timeline_markers.add()` по 6 маркерам сцен из `scene_plan.json`.
6. `open_page(page="edit")`.

Вывод: **MCP-управление внешней студией внутри пайплайна — рабочая практика**, не теория. Чего не хватает — слой управления самим проектом.

---

## 2. Чего не хватает: разрыв между «студией» и «проектом»

| # | Способность | Есть сейчас | Нужна внешнему агенту |
|---|---|---|---|
| 1 | Discovery возможностей | только изнутри репозитория (`registry.discover()`) | MCP-вызов `preflight` → меню провайдеров, setup_offers, runtime_warnings |
| 2 | Список/контракт тулов | `registry.list_all()`, `tool.get_info()` | `tool_list(capability)`, `tool_info(name)` со `best_for`, `install_instructions`, `agent_skills` |
| 3 | Запуск тула | `tool.execute(params)` из Python | `tool_run(name, params)` + `tool_estimate(name, params)` (цена до оплаты) |
| 4 | Управление пайплайном | `lib/pipeline_loader.py` | `pipelines()`, `pipeline_manifest(name)`, `next_stage(project_id)` |
| 5 | Гейты и чекпоинты | `lib/checkpoint.py` | `project_state()`, `checkpoint_write(..., human_approved=)` |
| 6 | Артефакты и валидация | `schemas/artifacts/` | `artifact_write/read` с валидацией по схеме |
| 7 | Аудит решений | `decision_log.json` | `decision_append()` (append-only, `(category, subject)`) |
| 8 | Контекст-скиллы | агент читает файлы репозитория | `read_skill(path)` / `context_pack(pipeline, stage)` — иначе Hermes/Bb не знает quality bar |
| 9 | Наблюдение за доской | Backlot HTTP | `board_state(project_id)` (или напрямую SSE) |
| 10 | Бюджет | `tools/cost_tracker.py`, `cost_snapshot` в чекпоинте | `budget(project_id)` — потрачено/лимит |

Пункт 8 — самый недооценённый: без директорских скиллов внешний агент будет делать «средний по качеству» результат, потому что правила (`Rule Zero`, present-both-runtimes, atelier vs templated, Layer-3 промптинг) живут в Markdown, а не в коде.

---

## 3. Целевая архитектура

### 3.1. Слои

```
┌──────────────────────────────────────────────────────────────┐
│  Внешний агент: Hermes / Bb / Claude Code / pi / OpenCode   │
│  (MCP-клиент: stdio или streamable HTTP)                     │
└───────────────────────────┬──────────────────────────────────┘
                            │  MCP tools
┌───────────────────────────▼──────────────────────────────────┐
│  openmontage_mcp  (тонкий транспорт, ~400 строк)             │
│  ┌────────────┬──────────────┬───────────────┬─────────────┐ │
│  │ om_control │  om_tools    │  om_context   │  om_assets  │ │
│  │ пайплайн,  │  реестр:     │  скиллы,      │  артефакты,  │ │
│  │ стадии,    │  list/info/  │  манифесты,   │  decision_  │ │
│  │ чекпоинты, │  estimate/   │  pack для     │  log, доска, │ │
│  │ гейты      │  run         │  стадии       │  бюджет     │ │
│  └────────────┴──────────────┴───────────────┴─────────────┘ │
└───────────────────────────┬──────────────────────────────────┘
                            │  прямые вызовы Python API
┌───────────────────────────▼──────────────────────────────────┐
│  Репозиторий OpenMontage (единственный источник истины)     │
│  tools/tool_registry.py · lib/checkpoint.py · skills/ ·       │
│  pipeline_defs/ · schemas/ · projects/ · backlot/            │
└──────────────────────────────────────────────────────────────┘
```

Принцип: **MCP-сервер — тонкий адаптер, а не новый слой логики.** Вся governance остаётся в репозитории.

### 3.2. Предлагаемая поверхность MCP-инструментов

**Важно:** не выставлять 158 MCP-инструментов по одному на каждый OpenMontage-тул — MCP-клиенты плохо переваривают большие тулсеты, а доступность тулов динамическая. Правильно: узкий curated-слой управления + один универсальный `tool_run` с рантайм-фильтром.

**`om_control` — управление проектом**

| Инструмент | Аргументы | Что делает | Под капотом |
|---|---|---|---|
| `om_preflight` | — | меню возможностей | `registry.provider_menu_summary()` |
| `om_pipelines` | — | список пайплайнов + стадии | `pipeline_loader.list_pipelines()` |
| `om_pipeline_manifest` | `pipeline` | YAML-манифест: стадии, гейты, `tools_available`, `review_focus` | `pipeline_loader.load_pipeline()` |
| `om_project_init` | `project_id, title, pipeline_type, style_playbook?` | создаёт workspace + `project.json` | `checkpoint.init_project()` |
| `om_project_state` | `project_id` | сводка: стадии, статусы, next_stage, spend, решения | `read_checkpoint`/`get_next_stage`/`cost_snapshot` |
| `om_checkpoint_write` | `project_id, stage, status, artifacts, human_approved=false` | запись чекпоинта **с enforcement гейта** | `checkpoint.write_checkpoint()` |
| `om_checkpoint_read` | `project_id, stage?` | чтение чекпоинтов | `checkpoint.read_checkpoint()` |
| `om_next_stage` | `project_id` | какая стадия следующая | `checkpoint.get_next_stage()` |
| `om_decision_append` | `project_id, stage, category, subject, options_considered[], selected, reason, user_approved?` | append-only запись решения **без прохождения через стадию** | `checkpoint.append_decision_log()` — публичная обёртка над `_merge_decision_log` (см. §4, «доработка upstream») |
| `om_artifact_write` | `project_id, artifact, payload` | запись артефакта **с валидацией по схеме** | `schemas/artifacts/__init__.py` |
| `om_artifact_read` | `project_id, artifact` | чтение артефакта | `projects/<id>/artifacts/<artifact>.json` |
| `om_board_state` | `project_id` | состояние доски Backlot | HTTP `GET /api/project/{id}/state` |
| `om_open_board` | `project_id` | URL доски (+ опционально открыть браузер) | `python -m backlot open` |
| `om_budget` | `project_id` | потрачено / остаток / прогноз | `cost_snapshot` чекпоинтов + `cost_tracker` |

**`om_tools` — доступ к реестру**

| Инструмент | Аргументы | Что делает |
|---|---|---|
| `om_tool_list` | `capability?, status?` | каталог: имя, capability, provider, статус, `best_for` |
| `om_tool_info` | `tool` | полный контракт из `get_info()`: `input_schema`, `install_instructions`, `dependencies`, `agent_skills`, `fallback_tools`, `resource_profile` |
| `om_tool_estimate` | `tool, params` | вызов встроенного `BaseTool.dry_run(params)` → `estimated_cost_usd`, `estimated_runtime_seconds`, `status`, `cost_status` (`estimated` \| `quote_required`) |
| `om_tool_run` | `tool, params, project_id, dry_run=false, mode=sync\|job` | `tool.execute(params)`; на выходе `ToolResult(success, data, artifacts, error, cost_usd, model, seed)`; пишет в `projects/<id>/assets/...`. В режиме `job` — мгновенно возвращает `job_id` (см. §3.5) |
| `om_approve_tool` | `tool` | **бюджетный хендшейк**: `CostTracker.approve_tool(tool)` — снимает `ApprovalRequiredError` для первого платного использования тула |
| `om_job_status` | `job_id` | состояние фоновой задачи: `running \| completed \| failed` | 
| `om_job_result` | `job_id` | результат завершённой задачи (пути, стоимость, seed) |

> `dry_run` — **не самописная заглушка, а штатный метод `BaseTool`** (`tools/base_tool.py:611`): он возвращает `estimated_cost_usd`, `cost_status` и `quote_reason` (`PriceQuoteRequired` из `tools/provider_pricing.py`, когда тариф зависит от аккаунта). Это единственный корректный способ показать цену **до** платного вызова.

**`om_context` — контекст для агента**

| Инструмент | Аргументы | Что делает |
|---|---|---|
| `om_read_skill` | `path, section?, max_chars=12000` | **выборочное** чтение скилла: `skills/**`, `AGENT_GUIDE.md`, `.agents/skills/**` (Layer 3). Отдаёт срез по `section`/лимиту, а не весь файл |
| `om_context_pack` | `pipeline, stage?` | **структурированная выжимка**: цель стадии, `success_criteria`, `review_focus`, `human_approval_default`, список доступных тулов + индекс скиллов (путь, размер, краткое описание). **Без полных текстов** (см. §3.6) |
| `om_layer3_skills` | `tool` | Layer-3 скиллы тула (по `agent_skills`) — обязательны к прочтению перед вызовом; отдаёт пути + краткие описания |

**`om_assets` — медиа и доска** (таблица `om_control` + следующие)

| Инструмент | Аргументы | Что делает |
|---|---|---|
| `om_asset_list` | `project_id, kind?` | что уже нагенерено (images/video/audio/music) |
| `om_media_path` | `project_id, rel_path` | абсолютный путь файла (для передачи в другой MCP, напр. ComfyUI/DaVinci) |
| `om_render_state` | `project_id` | рендеры, снапшоты, экспорт-бандл (`tools/publishers/export_bundle.py`) |

### 3.3. Транспорт

| Режим | Когда | Как |
|---|---|---|
| **stdio** | агент локальный (Hermes/Bb/pi/Claude Code на той же машине) | `command: .venv/bin/python`, `args: [scripts/openmontage_mcp.py]`, `cwd=/Users/mac/openMontag`, `env: PYTHONPATH=/Users/mac/openMontag` |
| **streamable HTTP** | агент удалённый или несколько клиентов | `mcp.run(transport="streamable-http")` на `127.0.0.1:8765` + bearer-токен; SSE для live-обновлений |

Локальный режим безопаснее: ключи в `.env` не покидают машину.

### 3.4. Что MCP **не** должен делать

| Правило | Почему |
|---|---|
| Обходить `human_approval_default` | `om_checkpoint_write` лишь передаёт `human_approved` в `lib/checkpoint.py`, который сам бросает GATE VIOLATION при `status="completed"` без апрува |
| Типить имя пайплайна | `write_checkpoint` fail-closed: неизвестный `pipeline_type` → `CheckpointValidationError`, а не тихое отключение гейта (`lib/checkpoint.py:251`) |
| Менять провайдера/модель/рендер-рантайм молча | только через `om_decision_append` с новой записью `(category, subject)` |
| Писать ассеты вне `projects/<id>/` | путь-контракт: всё вне workspace невидимо доске и ревьюеру |
| Обходить пайплайн (Rule Zero) | `om_tool_run` не заменяет стадии; агент обязан идти по стадиям манифеста |
| Трогать артефакты в обход схем | `om_artifact_write` валидирует по `schemas/artifacts/` |

### 3.5. Долгие генерации: режим `job` — ✅ реализовано 2026-10-25

Тяжёлые тулы (видео-генерация `kie/minimax/kling/atlas_3d`, рендер Remotion/HyperFrames, 3D-рендер Blender) работают 30 с — 10 мин. Клиенты MCP имеют собственные таймауты (обычно 60–120 с), поэтому синхронный `om_tool_run` отвалится на стороне клиента, **хотя генерация на бэкенде продолжится** — и платный вызов придётся повторять.

Реализация: два режима у одного тула + **отсоединённый воркер**.

| Режим | Поведение | Когда |
|---|---|---|
| `mode="auto"` (по умолчанию) | сам выбирает: `estimate_runtime() >= 60 с` или capability из `LONG_CAPABILITIES` → job, иначе sync | обычный вызов |
| `mode="sync"` | блокирующий вызов, полный `ToolResult` | короткие тулы |
| `mode="job"` | мгновенно возвращает `job_id`; работа идёт в отдельном процессе | тяжёлые тулы |

Жизненный цикл задачи:

```
om_tool_run(mode="job")  -> { job_id, status: "running", estimated_cost_usd, estimated_seconds }
om_job_status(job_id)    -> { status: running|completed|failed, cost_usd, error }
om_job_result(job_id)    -> { data, artifacts, cost_usd, model, seed }
om_job_list(project_id)  -> все задачи проекта (после переподключения клиента)
```

Ключевые решения:

- **Воркер — отдельный процесс** (`scripts/mcp_job_worker.py`, `start_new_session=True`):
  переживает таймаут клиента **и** падение MCP-сервера. Он читает job-запись, выполняет тул
  **ровно один раз**, сверяет бюджет и пишет результат обратно.
- **Job-запись — источник истины**: `projects/<id>/artifacts/jobs/<job_id>.json`,
  атомарная запись через `tools/provider_jobs.save_job`. Никаких «повторных отправок»:
  запись переживает перезапуск, а статус читается с диска.
- **Идемпотентность**: ключ `tool:idempotency_key(params)`; повторный вызов с теми же
  параметрами возвращает **тот же** `job_id` с `idempotent_replay: true`, а не создаёт
  вторую оплату.
- **Бюджет**: `reserve()` **до** старта воркера, `reconcile()` — по его завершении
  (в воркере). `CostTracker.reload()` добавлен upstream, чтобы длинный процесс (мост)
  и воркер не затирали состояния друг друга.
- **Признак долгого вызова**: `estimate_runtime()` есть не у всех тулов, поэтому
  fallback — capability (`video_generation`, `avatar`, `3d_*`, `music_generation`,
  `character_animation`) → 120 с.
- **Порог**: `OPENMONTAGE_MCP_JOB_THRESHOLD_SECONDS` (по умолчанию 60).

> Тул, зарегистрированный только в памяти процесса моста, воркер не увидит — это
> относится к тестовым заглушкам; все реальные тулы лежат в пакете `tools/` и
> находятся через `registry.discover()`.

### 3.6. Бюджет контекста: `om_context_pack` отдаёт выжимку, а не простыни

Реальные размеры (этот репозиторий):

| Файл | Размер | ≈ токенов |
|---|---|---|
| `AGENT_GUIDE.md` | 48 КБ | ~13k |
| `skills/pipelines/explainer/proposal-director.md` | 33 КБ | ~9k |
| `skills/pipelines/explainer/compose-director.md` | 19 КБ | ~5k |
| остальные 8 director-скиллов explainer | ~110 КБ суммарно | ~28k |
| **весь explainer-контур** | **~153 КБ** | **~38k** |

Выгружать это одним ответом — значит выбить контекстное окно внешнего агента (у Hermes/Bb/Cursor это обычно 100–200k на всю сессию, из которых на одну стадию уйдёт треть).

Правило разделения труда:

| Что | Как |
|---|---|
| `om_context_pack(pipeline, stage)` | **только структура**: цель стадии, `success_criteria[]`, `review_focus[]`, флаг гейта, список доступных тулов, индекс скиллов (путь + размер + первая строка-описание). Ориентир — до 3–4 КБ |
| `om_read_skill(path, section?, max_chars=12000)` | полный текст — **по запросу**, срезом. Параметр `section` ищет по markdown-заголовкам, `max_chars` жёстко режет |
| `om_layer3_skills(tool)` | список путей Layer-3 + краткие описания; чтение — через `om_read_skill` |

Агент читает конкретный скилл, когда дошёл до соответствующей стадии, а не заранее. Правило «прочти Layer-3 перед вызовом тула» при этом сохраняется — просто проверяется по факту, а не превентивно.

### 3.7. Статус реализации (на 2026-10-25)

**Фазы 0 и 1 (read-only + управляемое исполнение с фоновыми задачами) — реализованы.**

| Файл | Роль |
|---|---|
| `lib/mcp_bridge.py` | вся логика: чистые функции без MCP-зависимостей, тестируются обычными юнит-тестами |
| `scripts/openmontage_mcp.py` | stdio-сервер на `MCPServer` (mcp **2.x**; в 2.x `FastMCP` переименован в `MCPServer`, в скрипте есть fallback на 1.x) — только маппинг в MCP-тулы |
| `scripts/mcp_smoke.py` | смоук через настоящий MCP-клиент: `make mcp-smoke` |
| `scripts/mcp_job_worker.py` | отсоединённый воркер фоновой задачи (переживает таймаут клиента) |
| `tests/lib/test_mcp_bridge.py`, `tests/lib/test_mcp_bridge_phase1.py`, `tests/lib/test_mcp_bridge_jobs.py`, `tests/lib/test_mcp_bridge_studio.py` | 64 теста контракта |

Реализованная поверхность (27 тулов):

```
Фаза 0 (read-only)                      Фаза 1 (управляемое исполнение)
om_preflight         om_pipelines         om_tool_estimate    om_tool_run
om_pipeline_manifest om_project_state     om_approve_tool     om_budget
om_checkpoint_read   om_artifact_read     om_decision_append  om_decision_read
om_board_state       om_tool_list         om_checkpoint_write om_artifact_write
om_tool_info         om_read_skill        om_project_init
om_context_pack

Фоновые задачи: om_job_status · om_job_result · om_job_list
Связка со студией: om_asset_list · om_media_path · om_staging_roots · om_staging_copy
```

Формат ответа: `{"ok": true, ...}` для dict-результатов, `{"ok": true, "items": [...]}`
для списков, `{"ok": false, "error": ..., "error_type": ...}` для ошибок.

Ключевые контракты Фазы 1:

- **Порядок вызова тула**: доступность → проверка `output_path` внутри `projects/<id>/` →
  `dry_run()` (цена) → `reserve()` (бюджет) → `execute()` → `reconcile()`.
- **`refund()` не вызывается** при отказе `reserve()` — деньги не зарезервированы, запись
  остаётся `estimated`, сервер не падает.
- **Трекер — один на проект**, state-файл `projects/<id>/.cost_tracker_state.json`;
  одобрения и траты переживают перезапуск сервера.
- **Решения** — `lib/checkpoint.py::append_decision_log()`: append-only, валидация по схеме,
  ревизия = новая запись с тем же `(category, subject)`, статус стадии не трогается.
- **Гейт** — `write_checkpoint` в репозитории: гейтовая стадия без `human_approved=True`
  бросает GATE VIOLATION, и мост это не перехватывает.

Регистрация (сделано): `.pi/mcp.json` (pi), `.mcp.json` (Claude Code / Cursor),
`.agents/mcp_config.json` + `~/.gemini/config/mcp_config.json` (Gemini CLI),
`opencode.jsonc` (OpenCode).

Проверено: `64 passed` (pytest) · `21/21 PASS` (смоук, включая реальный воркер) ·
`pi mcp list` → `openmontage: connected, 27 tools (codemode, project)`.

**Закрыто:** T002 (решения), T003 (бюджет), T004 (фоновые задачи), T005 (контекст),
T006 (связка со студией).

**Ограничения текущей фазы:**
- файловая блокировка state-файла трекера не защищена от гонки двух процессов
  (окно узкое: reserve до старта, reconcile после);
- `om_layer3_skills` свёрнут в `om_tool_info`/`om_tool_estimate` (они возвращают
  `agent_skills` — пути Layer-3, которые читаются через `om_read_skill`);
- воркер не увидит тул, зарегистрированный только в памяти процесса моста
  (реальные тулы лежат в пакете `tools/`).

---

## 4. План реализации

### Фаза 0 — read-only (быстро, ~1 день) — ✅ сделано 2026-10-25

Цель: внешний агент видит проект и умеет читать инструкции.

- `scripts/openmontage_mcp.py` — stdio-сервер.
- Инструменты: `om_preflight`, `om_pipelines`, `om_pipeline_manifest`, `om_project_state`, `om_checkpoint_read`, `om_artifact_read`, `om_read_skill`, `om_context_pack`, `om_tool_list`, `om_tool_info`, `om_board_state`.
- Ничего не исполняет и не пишет. Реализация и проверка — §3.7.

### Фаза 1 — управляемое исполнение (~2–3 дня) — ✅ сделано 2026-10-25

- `om_tool_estimate` + `om_tool_run` с обязательным `project_id` и проверкой `output_path` внутри workspace.
- `om_project_init`, `om_checkpoint_write`, `om_decision_append`, `om_artifact_write`, `om_budget`, `om_approve_tool`.
- **Доработка upstream**: публичная `append_decision_log()` в `lib/checkpoint.py` (сделано).
- **Бюджетный предохранитель**: `CostTracker` в режиме `CAP`, трекер один на проект + state-файл.
- **Анонс провайдера**: ответ `om_tool_run` содержит `provider`, `model`, `cost_usd`, `seed`.
- **Не сделано (T004):** режим `mode="job"` для долгих генераций.

### Фаза 2 — полный контур

- `om_budget`, `om_asset_list`, `om_media_path`, `om_render_state`, `om_open_board`.
- Интеграция с внешними MCP: `om_media_path` отдаёт путь, который агент передаёт в `comfy_upload_image` / `davinci import` — связка «проект ↔ студия» без копирования вручную.
- Опционально: режим «Approval broker» — гейт `awaiting_human` поднимается как уведомление в чат внешнего агента и ждёт явного `human_approved=true`.

### Файлы

```
scripts/openmontage_mcp.py          # сервер (фазы 0–2)
lib/mcp_bridge.py                   # тонкие обёртки над registry/checkpoint/pipeline_loader (логика вне MCP-слоя)
tests/test_mcp_bridge.py            # контракт: гейт, append-only decision_log, пути ассетов
docs/mcp/openmontage-mcp.md         # (опц.) краткая референс-дока для внешних агентов
```

Правило: `lib/mcp_bridge.py` — чистые функции без MCP-зависимостей (тестируются обычными юнит-тестами), `scripts/openmontage_mcp.py` — только маппинг в MCP-тулы.

### Скелет

```python
# scripts/openmontage_mcp.py
"""MCP transport over OpenMontage. Transport only — governance stays in lib/."""
from mcp.server.fastmcp import FastMCP
from lib.mcp_bridge import (
    preflight, list_pipelines, pipeline_manifest, project_state,
    checkpoint_write, decision_append, artifact_read,
    tool_list, tool_info, tool_estimate, tool_run, read_skill, context_pack,
)

mcp = FastMCP("openmontage")

@mcp.tool()
def om_preflight() -> dict:
    """Capability menu: composition_runtimes, capabilities, setup_offers, runtime_warnings."""
    return preflight()

@mcp.tool()
def om_pipeline_manifest(pipeline: str) -> dict:
    """YAML manifest: stages, gates (human_approval_default), tools_available, review_focus."""
    return pipeline_manifest(pipeline)

@mcp.tool()
def om_project_state(project_id: str) -> dict:
    """Stages, statuses, next_stage, spend, decisions for one project."""
    return project_state(project_id)

@mcp.tool()
def om_checkpoint_write(project_id: str, stage: str, status: str, artifacts: dict,
                        human_approved: bool = False) -> str:
    """Write checkpoint_<stage>.json. Gate enforcement lives in lib/checkpoint.py —
    writing 'completed' for a gated stage without approval raises GATE VIOLATION."""
    return checkpoint_write(project_id, stage, status, artifacts, human_approved)

@mcp.tool()
def om_decision_append(project_id: str, stage: str, category: str, subject: str,
                       options_considered: list, selected: str, reason: str,
                       user_approved: bool = False) -> str:
    """Append-only decision log entry. Reuse the same (category, subject) to revise a choice.
    Writes ONLY the decision log — it does not touch stage status or checkpoints."""
    return decision_append(project_id, stage, category, subject,
                           options_considered, selected, reason, user_approved)

@mcp.tool()
def om_tool_estimate(tool: str, params: dict) -> dict:
    """Cost + runtime estimate (BaseTool.dry_run). Always call before a paid run."""
    return tool_estimate(tool, params)

@mcp.tool()
def om_tool_run(tool: str, params: dict, project_id: str, dry_run: bool = False,
                mode: str = "sync") -> dict:
    """Execute one registered tool. Writes only under projects/<project_id>/.
    mode='job' returns {job_id, status:'running'} immediately for long generations."""
    return tool_run(tool, params, project_id, dry_run, mode)

@mcp.tool()
def om_job_status(job_id: str) -> dict:
    """Poll a background job: running | completed | failed."""
    return job_status(job_id)

@mcp.tool()
def om_context_pack(pipeline: str, stage: str | None = None) -> dict:
    """Structured SUMMARY of a stage: goal, success_criteria, review_focus, gate flag,
    available tools, skill index. Full skill texts are NOT included — use om_read_skill."""
    return context_pack(pipeline, stage)

@mcp.tool()
def om_read_skill(path: str, section: str | None = None, max_chars: int = 12000) -> dict:
    """Read a skill file (skills/**, AGENT_GUIDE.md, .agents/skills/**) with slicing."""
    return read_skill(path, section, max_chars)

if __name__ == "__main__":
    mcp.run()  # stdio; transport="streamable-http" for remote agents
```

Ключевые инварианты, которые должен обеспечивать `lib/mcp_bridge.py` (примитивы — **штатные**, из репозитория, проверены через CodeGraph):

```python
# 1. ассеты — только внутрь workspace
from lib.paths import PROJECTS_DIR

def _assert_in_workspace(project_id: str, path: str) -> Path:
    root = (PROJECTS_DIR / project_id).resolve()
    p = (root / path).resolve()
    if not p.is_relative_to(root):
        raise ValueError("output_path must live under projects/<project_id>/")
    return p


# 2. бюджет — CostTracker, но ОДИН экземпляр на проект (иначе одобрения и траты теряются)
from lib.config_model import BudgetMode
from tools.cost_tracker import CostTracker, ApprovalRequiredError, BudgetExceededError

_TRACKERS: dict[str, CostTracker] = {}

def tracker_for(project_id: str) -> CostTracker:
    """Per-project tracker with a persistent state file.

    CostTracker._save() persists entries + approved_tools + budget_total_usd,
    and __init__ restores them via _load() — so a fresh instance per MCP call
    would lose approvals and reset the spend to zero.
    """
    if project_id not in _TRACKERS:
        state = PROJECTS_DIR / project_id / "artifacts" / "cost_tracker_state.json"
        _TRACKERS[project_id] = CostTracker(
            budget_total_usd=PROJECT_BUDGET_USD,
            mode=BudgetMode.CAP,
            cost_log_path=state,
        )
    return _TRACKERS[project_id]


# 3. запуск тула — без вызова refund() на непрорезервированной записи
def tool_run(tool, params, project_id, dry_run=False, mode="sync"):
    t = registry.get(tool)
    if t is None:
        return {"success": False, "error": f"unknown tool: {tool}"}
    if t.get_status() is not ToolStatus.AVAILABLE:
        return {"success": False, "error": "tool unavailable",
                "install_instructions": t.install_instructions}

    pre = t.dry_run(params)                     # штатный preflight BaseTool
    est = pre.get("estimated_cost_usd") or 0.0
    if dry_run:
        return {"success": True, "dry_run": True, **pre}

    tracker = tracker_for(project_id)
    entry_id = tracker.estimate(t.name, "mcp_run", est)
    try:
        tracker.reserve(entry_id)               # ApprovalRequiredError / BudgetExceededError
    except (ApprovalRequiredError, BudgetExceededError) as exc:
        # ВАЖНО: refund() здесь НЕЛЬЗЯ — reserve() упал, запись осталась 'estimated',
        # а refund() требует статус 'reserved' и бросил бы ValueError, убив сервер.
        # Денег не зарезервировано (reserved_usd = 0), учёт не пострадал.
        return {"success": False, "needs_approval": True, "error": str(exc),
                "estimated_cost_usd": est, "cost_entry_id": entry_id,
                "cost_entry_status": "estimated"}

    if mode == "job":
        job_id = _spawn_job(project_id, t.name, params, entry_id)
        return {"success": True, "job_id": job_id, "status": "running",
                "estimated_cost_usd": est, "cost_entry_id": entry_id}

    r = t.execute(params)
    tracker.reconcile(entry_id, r.cost_usd or 0.0, success=r.success)
    return {"success": r.success, "data": r.data, "artifacts": r.artifacts,
            "error": r.error, "cost_usd": r.cost_usd, "model": r.model, "seed": r.seed}
```

Доработка upstream (нужна до Фазы 1, иначе мост лажет на приватный API):

| Файл | Что добавить | Зачем |
|---|---|---|
| `lib/checkpoint.py` | публичная `append_decision_log(pipeline_dir, project_id, new_log) -> Path` — тонкая обёртка над `_merge_decision_log` + `validate_artifact("decision_log", ...)` | зафиксировать решение **посреди стадии**, не трогая статус стадии; сейчас `_merge_decision_log` приватный, а `write_checkpoint` для этого не годится (он требует полный набор артефактов стадии и валидирует их) |
| `schemas/artifacts/decision_log.schema.json` | уже `additionalProperties: false` → мост обязан собирать ровно `decision_id, stage, category, subject, options_considered[{option_id,label,score,reason,rejected_because?}], selected, reason, user_visible?, user_approved?, confidence?` | лишние поля (напр. `notes`) → ошибка валидации |

Жизненный цикл бюджета в репозитории (`tools/cost_tracker.py`), который мост переиспользует как есть:

```
CostTracker(mode=BudgetMode.CAP|WARN|OBSERVE)
  estimate(tool, operation, usd) -> entry_id          # статус: estimated
  reserve(entry_id)      -> ApprovalRequiredError (порог одного действия / первый платный тул)
                         -> BudgetExceededError  (превышен доступный бюджет, режим CAP)
                         # при ошибке запись остаётся 'estimated' — refund() не звать!
  approve_tool(tool)     -> снимает «первый платный тул» (вызывается из om_approve_tool)
  execute(...)           -> фактический вызов
  reconcile(entry_id, actual_usd, success)   # из 'reserved' -> completed/failed
  refund(entry_id)       # ТОЛЬКО из 'reserved' (отмена резерва)
```

То есть бюджетный гейт над MCP работает **нативно**: `reserve()` бросает `ApprovalRequiredError` → мост возвращает `needs_approval: true` → внешний агент спрашивает человека → вызывает `om_approve_tool(tool)` → повторяет вызов. Никакого параллельного «своего» бюджета в мосте.

> **Ловушка `cost_log`:** `CostTracker._save()` пишет ключ `approved_tools` (и, в режиме WARN, `budget_warning`/`budget_warning_message` в записях), а `schemas/artifacts/cost_log.schema.json` объявлен с `additionalProperties: false` → файл трекера **не пройдёт** `validate_artifact("cost_log", ...)`. Поэтому состояние трекера держим в отдельном `artifacts/cost_tracker_state.json` (не канонический артефакт), а канонический `cost_log.json` мост формирует отдельно — проекцией записей на поля схемы.

---

## 5. Подключение внешнего агента

### 5.1. Локальный агент (Hermes / Bb / pi / Claude Code / Cursor / OpenCode)

Общий блок (формат `mcpServers`):

```jsonc
{
  "mcpServers": {
    "openmontage": {
      "command": "/Users/mac/openMontag/.venv/bin/python",
      "args": ["/Users/mac/openMontag/scripts/openmontage_mcp.py"],
      "cwd": "/Users/mac/openMontag",
      "env": { "PYTHONPATH": "/Users/mac/openMontag" }
    }
  }
}
```

Куда положить:

| Клиент | Файл |
|---|---|
| pi | `/Users/mac/.pi/agent/mcp.json` (формат `mcpServers`) |
| Gemini CLI | `/Users/mac/.gemini/config/mcp_config.json` (формат тот же) + репо-копия `.agents/mcp_config.json` |
| Claude Code | `.mcp.json` в корне проекта или `claude mcp add --transport stdio openmontage -- /Users/mac/openMontag/.venv/bin/python /Users/mac/openMontag/scripts/openmontage_mcp.py` |
| OpenCode | `opencode.jsonc` → `mcp.servers.openmontage = { "type": "local", "command": [...] }` |
| Cursor / VS Code | `.vscode/mcp.json` |

**Обязательно:** интерпретатор проекта `/Users/mac/openMontag/.venv/bin/python` (Python 3.10) и `cwd=/Users/mac/openMontag`. Системный `python3` (3.9) не увидит `tools/`, `lib/`, `fastapi` и `.env`.

### 5.2. Удалённый агент

```bash
# в scripts/openmontage_mcp.py: mcp.run(transport="streamable-http", host="127.0.0.1", port=8765)
curl -H "Authorization: Bearer $OM_MCP_TOKEN" http://127.0.0.1:8765/mcp
```

Правила: биндинг только на loopback (как Backlot: `127.0.0.1` + проверка Host-заголовка против DNS rebinding), токен в env, никаких 0.0.0.0 без VPN/туннеля.

### 5.3. Готовый «бюджетный» вариант без нового кода

Если полноценный MCP-сервер не нужен сразу — внешний агент уже может управлять проектом **через существующие MCP + shell**:

- `mcp__davinci__*` — монтаж/доставка;
- `comfy_*` — генерация;
- `codegraph` — навигация по коду;
- shell (`python -c "from lib.checkpoint import ..."`) — пайплайн-контроль.

Минус: каждая из этих операций требует от агента знать внутренние пути и API — это ровно то, что закрывает `om_control`.

---

## 6. Безопасность и бюджет

| Механизм | Реализация |
|---|---|
| Read-only режим по умолчанию | флаг `OM_MCP_MODE=readonly` → доступны только `om_*_read/list/info` |
| Allowlist инструментов | `OM_MCP_ALLOWED_TOOLS=om_preflight,om_tool_run,...`; `om_tool_run` дополнительно ограничен по capability (`video_generation` требует явного разрешения) |
| Дневной/проектный бюджет | `CostTracker(mode=BudgetMode.CAP)` из `tools/cost_tracker.py`: `estimate → reserve → approve_tool → execute → reconcile`; превышение → `BudgetExceededError` **до** вызова, не после |
| Порог одного платного действия | `CostTracker(single_action_approval_usd=...)` → `ApprovalRequiredError` → ответ `needs_approval: true` → человек → `om_approve_tool` → повтор |
| Персистентность бюджета | один `CostTracker` на `project_id` + `cost_log_path` (state-файл) — `_save()`/`_load()` сохраняют `entries`, `approved_tools`, `budget_total_usd` между MCP-вызовами и перезапусками сервера |
| Dry-run | `om_tool_estimate` вызывает штатный `BaseTool.dry_run()`; `om_tool_run(dry_run=True)` — обязательный шаг перед платным вызовом |
| Таймауты клиента | `mode="job"` для всего, что дольше ~60 с; повторный вызов платного тула запрещён (идемпотентность по `idempotency_key`) |
| Бюджет контекста | `om_context_pack` отдаёт выжимку (≤3–4 КБ), полные тексты — только через `om_read_skill(path, section, max_chars)` |
| Секреты | `.env` читается сервером локально; ключи наружу не отдаются (в ответе — только `provider`, `model`, `cost_usd`) |
| Путь-контракт | любой `output_path` обязан быть внутри `projects/<project_id>/` |
| Локальность | `127.0.0.1` + токен; Backlot уже так сделан — не ломать модель |
| Аудит | `events.jsonl` (инструментария `BaseTool`) + `decision_log.json` + `history/` чекпоинтов |

---

## 7. Как будет выглядеть сессия внешнего агента

```
Hermes/Bb: «Сделай 60-секундный ролик про то, как устроен квантовый компьютер»

  om_preflight()                      → video 2/34, tts 0/18, render runtimes 3/3
                                        → Hermes показывает меню человеку
  om_pipelines()                      → 13 пайплайнов
  om_pipeline_manifest("animated-explainer")
  om_context_pack("animated-explainer", "research")
                                        → читает director-скилл, знает quality bar
  om_tool_estimate("tts_selector", {...})  → cost 0.00 / runtime 12s
  ... генерация ...
  om_checkpoint_write(project_id, "research", "completed", {research_brief})
  om_checkpoint_write(project_id, "proposal", "awaiting_human", {proposal_packet})
                                        → СТОП: гейт, ждёт человека
  Human: «да, го»
  om_decision_append(project_id, "proposal", "render_runtime_selection",
                     "Composition runtime", [ffmpeg, remotion, hyperframes],
                     "hyperframes", "kinetic typography для этого брифа", user_approved=True)
  om_checkpoint_write(..., "proposal", "completed", {...}, human_approved=True)
  ... далее по стадиям ...
  om_board_state(project_id)           → доска обновляется вживую
```

Ключевое: **гейт `awaiting_human` обязан останавливать и внешнего агента** — иначе Hermes/Bb «прогонит» проект без ревью, а это ровно то, что запрещает `AGENT_GUIDE.md`.

---

## 8. Чек-лист готовности

- [ ] `mcp` установлен в `.venv` (`pip install mcp`) — сейчас не установлен
- [ ] `scripts/openmontage_mcp.py` + `lib/mcp_bridge.py` созданы
- [ ] `lib/checkpoint.py` получил публичную `append_decision_log()` (иначе мост зовёт приватный `_merge_decision_log`)
- [ ] `om_tool_run` пишет только в `projects/<id>/` и уважает `dry_run`
- [ ] `om_tool_estimate` вызывает **штатный** `BaseTool.dry_run()`, а не самописный `estimate_cost`
- [ ] Бюджет — через `CostTracker` (`estimate → reserve → approve_tool → reconcile`), без параллельного «своего» лимита в мосте
- [ ] Трекер — **один на проект**, с `cost_log_path` (state-файл); одобрения переживают перезапуск сервера
- [ ] `ApprovalRequiredError` → `needs_approval: true` в ответе + тул `om_approve_tool`
- [ ] После отказа `reserve()` **не** вызывается `refund()` (запись остаётся `estimated`)
- [ ] `om_checkpoint_write` пробрасывает `human_approved` в `lib/checkpoint.py` (гейт не обходится)
- [ ] `om_decision_append` — append-only, `(category, subject)` для ревизии, **не трогает** статус стадии
- [ ] `om_context_pack` возвращает выжимку (≤ ~4 КБ) + индекс скиллов; полный текст — только из `om_read_skill(path, section, max_chars)`
- [ ] Тяжёлые тулы поддерживают `mode="job"`; повторный платный вызов невозможен (идемпотентность)
- [ ] `om_tool_run` для недоступного тула возвращает `install_instructions`, а не молчит
- [ ] Тест: гейт без апрува → GATE VIOLATION
- [ ] Тест: опечатка в `pipeline_type` → `CheckpointValidationError` (fail-closed, гейт не отключается)
- [ ] Тест: `output_path` вне workspace → отказ
- [ ] Тест: платный вызов без `approve_tool` → `needs_approval: true`, тул не запущен
- [ ] Тест: отказ `reserve()` → сервер не падает (нет `ValueError` от `refund`)
- [ ] Тест: два вызова `om_tool_run` подряд → одобрения и траты сохраняются (state-файл)
- [ ] Тест: `om_decision_append` посреди стадии → статус стадии не изменился
- [ ] Тест: смена провайдера → новая запись в `decision_log.json`, старая не тронута
- [ ] Конфиг добавлен в pi / Gemini CLI / OpenCode / `.mcp.json`
- [ ] Смоук: внешний агент проходит `research → proposal` и останавливается на гейте

---

## Приложение A. Ключевые пути

| Что | Путь |
|---|---|
| Корень | `/Users/mac/openMontag` |
| Python проекта | `/Users/mac/openMontag/.venv/bin/python` (3.10) |
| Реестр тулов | `tools/tool_registry.py` |
| Контракт тула | `tools/base_tool.py` (`get_info()`, `execute()`, `estimate_cost()`) |
| Чекпоинты/проекты | `lib/checkpoint.py` |
| Пути | `lib/paths.py` (`PROJECTS_DIR`, `REPO_ROOT`) |
| Манифесты пайплайнов | `pipeline_defs/*.yaml` (13) |
| Директорские скиллы | `skills/pipelines/<pipeline>/<stage>-director.md` |
| Мета-скиллы | `skills/meta/*.md` |
| Layer-3 (вендоры) | `.agents/skills/<vendor>/SKILL.md` |
| Схемы артефактов | `schemas/artifacts/*.schema.json` |
| Доска | `backlot/server.py`, `python -m backlot open <id>`, порт 4750 |
| Проекты | `projects/<project-id>/` (`artifacts/ assets/ renders/ checkpoint_*.json decision_log.json events.jsonl history/`) |
| MCP-конфиги | `.agents/mcp_config.json`, `opencode.jsonc`, `~/.pi/agent/mcp.json`, `~/.gemini/config/mcp_config.json` |

## Приложение B. Команды проверки

```bash
cd /Users/mac/openMontag

# возможности (должно печатать 158 total / 45 available, runtimes 3/3)
.venv/bin/python -c "
from tools.tool_registry import registry; import json
registry.discover(); s = registry.provider_menu_summary()
print(len(registry.list_all()), len(registry.get_available()))
print(json.dumps(s['composition_runtimes']))"

# состояние проекта
.venv/bin/python -c "
from lib.checkpoint import get_next_stage, get_completed_stages
from lib.paths import PROJECTS_DIR
pid='how-ai-sees-the-world'
print(get_completed_stages(PROJECTS_DIR, pid), '->', get_next_stage(PROJECTS_DIR, pid))"

# доска
python -m backlot open how-ai-sees-the-world
curl -s http://127.0.0.1:4750/api/project/how-ai-sees-the-world/state | head -c 400

# MCP-сервер (после сборки): проба handshake
.venv/bin/python scripts/openmontage_mcp.py   # ждёт stdio-клиента
```

## Приложение C. Точки enforcement (проверено CodeGraph)

Мост не должен реализовывать governance заново — он обязан вызывать существующие функции, где enforcement уже есть:

| Проверяемое свойство | Точка в коде | Поведение при нарушении |
|---|---|---|
| Гейт стадии | `lib/checkpoint.py:422` `write_checkpoint` → `_stage_requires_approval` (251), `_enforce_stage_prerequisites` (284), `_validate_artifacts_for_stage` (124) | `CheckpointValidationError`; `completed` без `human_approved=True` — GATE VIOLATION |
| Fail-closed на опечатке | `lib/checkpoint.py:251` `_stage_requires_approval` | неизвестный `pipeline_type` → ошибка, а не тихое отключение гейта |
| Валидация артефактов | `lib/checkpoint.py:160` `validate_checkpoint` + `schemas/artifacts/*.schema.json` | артефакт не проходит схему → отказ |
| Append-only аудит | `lib/checkpoint.py:392` `_merge_decision_log` → `projects/<id>/decision_log.json` | дедуп по `decision_id`, старые записи не мутируются |
| История перезапусков | `lib/checkpoint.py:349` `_archive_superseded_checkpoint` → `projects/<id>/history/` | старый чекпоинт архивируется, не теряется |
| Бюджет | `tools/cost_tracker.py:40` `CostTracker` → `estimate`/`reserve`/`approve_tool`/`reconcile` | `ApprovalRequiredError` / `BudgetExceededError` **до** вызова |
| **Ловушка `refund()`** | `tools/cost_tracker.py:175` `refund()` + `:521` `_require_status(entry, RESERVED, "refund")` | после упавшего `reserve()` запись остаётся `estimated` → `refund()` бросит `ValueError` и уронит сервер. Звать `refund()` только из `reserved` |
| **Ловушка `reconcile()`** | `tools/cost_tracker.py:165` `reconcile()` требует `RESERVED` | `reconcile()` нельзя вызвать на непрорезервированной записи — тот же `ValueError` |
| Персистентность бюджета | `tools/cost_tracker.py:490` `_save()` / `:505` `_load()` | без `cost_log_path` трекер теряет `entries`, `approved_tools` и `budget_total_usd` при каждом вызове |
| **Конфликт схемы `cost_log`** | `schemas/artifacts/cost_log.schema.json` (`additionalProperties: false`) vs `CostTracker._save()` (пишет `approved_tools`, а в WARN-режиме ещё `budget_warning`/`budget_warning_message`) | файл трекера **не пройдёт** `validate_artifact("cost_log", ...)` → держать состояние трекера в отдельном state-файле, канонический `cost_log` собирать проекцией |
| Предполётная цена | `tools/base_tool.py:611` `BaseTool.dry_run` | `cost_status: quote_required` + `quote_reason` (`tools/provider_pricing.py:4`) |
| Статус тула | `tools/base_tool.py:480` `get_status()`, 488 `check_dependencies()` | недоступный тул → `install_instructions`, а не молчание |
| Диспетчеризация | `tools/base_tool.py:409` `BaseTool` — 146 реализаций, `execute` — runtime dispatch | вызов идёт через `registry.get(name)` |
| Долгие задачи | `tools/provider_jobs.py:41` `save_job()` (атомарно), `:50` `poll()` | `TimeoutError: Job is still pending; resume with the returned job metadata, do not resubmit` — платный запрос не переотправлять |
| Идемпотентность | `tools/base_tool.py:571` `idempotency_key()`, 577 `cache_key()` | повторный вызов того же платного тула должен возвращать тот же job, а не платить дважды |
| Пути проектов | `lib/paths.py` `PROJECTS_DIR` / `REPO_ROOT` (единый источник, переопределяется `OPENMONTAGE_PROJECTS_DIR`) | ассет вне `projects/<id>/` невидим доске |
| Наблюдение | `backlot/server.py:179` `GET /api/project/{id}/state` | — |
| **Пробел в публичном API** | `lib/checkpoint.py:392` `_merge_decision_log` — приватная функция; публичной обёртки нет | мост вынужден лазить в приватный API → добавить `append_decision_log()` upstream |

Полезные команды CodeGraph для тех, кто будет расширять мост:

```bash
cd /Users/mac/openMontag
codegraph explore "write_checkpoint gate enforcement human_approved"
codegraph explore "cost_tracker approve_tool budget cap enforcement"
codegraph explore "BaseTool execute output_path asset write projects directory"
```

---

## Приложение D. Связанные документы

| Файл | О чём |
|---|---|
| `AGENT_GUIDE.md` | контракт агента: Rule Zero, гейты, decision log, Decision Communication Contract |
| `PROJECT_CONTEXT.md` | архитектура и конвенции |
| `test.md` | §Шаг 7 — работающий пример управления проектом через DaVinci MCP |
| `COMFY.md` | ComfyUI через MCP: команды `comfy_*`, формат workflow-JSON |
| `backlot/README.md` | доска: API, SSE, replay |
| `skills/meta/checkpoint-protocol.md` | протокол чекпоинтов и гейтов |
