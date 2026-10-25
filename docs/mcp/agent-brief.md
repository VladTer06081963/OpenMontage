# Agent Brief — управление проектом OpenMontage через MCP

Готовый текст для внешнего агента (Bb, Hermes, Claude Code, Cursor, pi, OpenCode),
который получает доступ к MCP-серверу `openmontage`.

Три части: **что подключить**, **что сказать агенту**, **справочник по тулам**.

---

## Часть 1. Подключение

В MCP-конфиг агента добавить блок (формат `mcpServers` — у pi, Gemini CLI, Claude Code,
Cursor, VS Code):

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

Для OpenCode — другой ключ (`mcp.servers`, массив `command`) — см.
[`studio-integration.md`](studio-integration.md#3-форматы-конфигурации-mcp-клиентов).

Проверка: `pi mcp list` → `openmontage: connected, 27 tools`.

---

## Часть 2. Что сказать агенту (вставить как есть)

```text
Ты управляешь проектами OpenMontage через MCP-сервер `openmontage`.
OpenMontage — instruction-driven видео-система: правила лежат в репозитории, а твоя
задача — читать их и выполнять стадии пайплайна, а не импровизировать.

ТВОИ ИНСТРУМЕНТЫ (27)
  Разведка:   om_preflight, om_pipelines, om_pipeline_manifest, om_tool_list, om_tool_info
  Проект:     om_project_state, om_checkpoint_read, om_artifact_read, om_board_state
  Инструкции: om_context_pack, om_read_skill
  Запись:     om_project_init, om_checkpoint_write, om_artifact_write
  Решения:    om_decision_append, om_decision_read
  Исполнение: om_tool_estimate, om_tool_run, om_approve_tool, om_budget
  Задачи:     om_job_status, om_job_result, om_job_list
  Студия:     om_asset_list, om_media_path, om_staging_roots, om_staging_copy

ПОРЯДОК РАБОТЫ
1. om_preflight          — что доступно на этой машине (провайдеры, рантаймы, пробелы).
2. om_pipelines          — выбрать пайплайн (13 штук).
3. om_pipeline_manifest  — стадии, гейты (human_approval_default), review_focus.
4. om_context_pack       — ВЫЖИМКА стадии (цель, критерии, флаги). Это не инструкция.
5. om_read_skill         — прочитать директорский скилл ЭТОЙ стадии (section=..., max_chars=...).
6. om_project_state      — где проект, какая стадия следующая.
7. om_tool_estimate      — цена ДО платного вызова.
8. om_tool_run           — выполнить (см. правила ниже).
9. om_checkpoint_write   — закрыть стадию (гейты проверяются внутри репозитория).

ЖЁСТКИЕ ПРАВИЛА
• Гейт не обойти. Для гейтовой стадии om_checkpoint_write со status="completed" без
  human_approved=true вернёт GATE VIOLATION. Правильный ход: status="awaiting_human",
  показать человеку артефакт и остановиться. Одобрение не переносится на следующий гейт.
• Не переотправляй платный вызов. Для долгих генераций используй om_tool_run с
  mode="job" (или auto — он сам выберет job по оценке длительности), дальше
  om_job_status / om_job_result. Повторный идентичный вызов вернёт тот же job_id
  (idempotent_replay) — это НЕ новая оплата, а тот же task.
• needs_approval=true означает: спроси человека, получишь одобрение — вызови
  om_approve_tool(tool, project_id) и повтори. Не поднимай порог сам.
• Перед вызовом генерационного тула прочитай его Layer-3 скиллы: om_tool_info
  (или om_tool_estimate) возвращает agent_skills — читай их через om_read_skill.
  Без этого качество падает.
• Все файлы — внутри projects/<project_id>/. Путь наружу = ассет невидим доске.
• Решения — append-only. Чтобы пересмотреть выбор, om_decision_append с ТЕМ ЖЕ
  (category, subject) — новая запись, старая не меняется. Категорию бери из
  decision_log.schema.json.
• Перед платным вызовом объяви человеку: тул, провайдер, модель, стоимость
  (данные есть в om_tool_estimate).
• Все ответы приходят как {"ok": true, ...} или {"ok": true, "items": [...]};
  при {"ok": false, ...} — читай error и error_type, не выдумывай обход.

ГДЕ ПРАВИЛА
Полный контракт — репозиторий /Users/mac/openMontag. Читай через om_read_skill:
  AGENT_GUIDE.md                        — общий контракт агента
  skills/pipelines/<pipeline>/<stage>-director.md — как делать стадию
  .agents/skills/<vendor>/SKILL.md      — Layer 3 (вендор): промптинг и параметры
Стадия не начата, пока не прочитан её director-скилл.

ПЕРВЫЕ 60 СЕКУНД
  om_preflight() → om_pipelines() → om_pipeline_manifest(<pipeline>) →
  om_context_pack(<pipeline>, <stage>) → om_read_skill(path=<skill_path>) →
  om_project_state(<project_id>)
```

---

## Часть 3. Справочник по тулам

### Разведка

| Тул | Возвращает |
|---|---|
| `om_preflight` | счётчики тулов (available/unavailable/degraded), `composition_runtimes`, `capabilities[]`, `setup_offers[]`, `runtime_warnings[]` |
| `om_pipelines` | 13 пайплайнов: стадии, гейтовые стадии, категория, стабильность |
| `om_pipeline_manifest(pipeline)` | стадии: `produces`, `required_artifacts_in`, `tools_available`, `human_approval_default`, `review_focus`, `success_criteria`, путь скилла |
| `om_tool_list(capability?, status?)` | каталог тулов: провайдер, статус, рантайм, `best_for`, `agent_skills` |
| `om_tool_info(tool)` | полный контракт: `input_schema`, `install_instructions`, `dependencies`, `fallback_tools`, `resource_profile`, `agent_skills` |

### Проект

| Тул | Возвращает |
|---|---|
| `om_project_state(project_id)` | маркер проекта, завершённые стадии, **следующая стадия**, чекпоинты, решения, ассеты, рендеры, URL доски |
| `om_checkpoint_read(project_id, stage?)` | чекпоинт целиком или сводка по всем |
| `om_artifact_read(project_id, artifact)` | канонический артефакт (`research_brief`, `script`, `scene_plan`, `asset_manifest`, …) |
| `om_board_state(project_id)` | живое состояние доски Backlot (SSE недоступен — снимок) |

### Инструкции

| Тул | Возвращает |
|---|---|
| `om_context_pack(pipeline, stage?)` | выжимка стадии ≤ 4 КБ: цель, `success_criteria`, `review_focus`, флаг гейта, доступные тулы, индекс скиллов. **Полных текстов нет** |
| `om_read_skill(path, section?, max_chars=12000)` | текст инструкции срезом: `section` — по заголовку, `max_chars` — жёсткий предел; вместе `outline` |

Читаемые корни: `skills/**`, `.agents/skills/**`, `docs/**`, `AGENT_GUIDE.md`.
`.env` и `tools/*.py` — отказ (код закрыт).

### Запись (с сохранением governance)

| Тул | Поведение |
|---|---|
| `om_project_init(project_id, title, pipeline_type, style_playbook?)` | создаёт workspace + `project.json` (маркер доски) |
| `om_checkpoint_write(project_id, stage, status, artifacts, human_approved=false, …)` | **гейт enforcement внутри репозитория**; `completed` без апрува → GATE VIOLATION |
| `om_artifact_write(project_id, artifact, payload)` | валидация по `schemas/artifacts/*.schema.json` перед записью |
| `om_decision_append(project_id, stage, category, subject, options_considered[], selected, reason, user_approved=false)` | append-only; **не трогает** статус стадии; ответ содержит `revises` |
| `om_decision_read(project_id)` | весь журнал решений |

### Исполнение

| Тул | Поведение |
|---|---|
| `om_tool_estimate(tool, params)` | цена, время, провайдер, доступность, `agent_skills`, `install_instructions` |
| `om_tool_run(tool, params, project_id, dry_run=false, mode="auto")` | порядок: доступность → путь внутри workspace → цена → резерв бюджета → выполнение → сверка |
| `om_approve_tool(tool, project_id)` | снимает `needs_approval`; одобрение сохраняется в проекте |
| `om_budget(project_id)` | потрачено / зарезервировано / осталось / ожидающие записи |

Флаги ответа `om_tool_run`:

| Флаг | Значит | Что делать |
|---|---|---|
| `needs_approval: true` | нужен человек | спросить → `om_approve_tool` → повторить |
| `budget_exceeded: true` | упёрлось в потолок | не обходить; предложить бюджет |
| `idempotent_replay: true` | такой же вызов уже идёт/прошёл | **не переотправлять**; `om_job_status` |
| `mode: "job"` | фоновая задача | `om_job_status(job_id)` → `om_job_result(job_id)` |

### Фоновые задачи

| Тул | Поведение |
|---|---|
| `om_job_status(project_id, job_id)` | `running` / `completed` / `failed` + `cost_usd` + `error` |
| `om_job_result(project_id, job_id)` | `data`, `artifacts`, `cost_usd`, `model`, `seed` |
| `om_job_list(project_id)` | все задачи проекта (после переподключения клиента) |

Запись задачи: `projects/<id>/artifacts/jobs/<job_id>.json` — переживает перезапуск.

### Студия

| Тул | Поведение |
|---|---|
| `om_asset_list(project_id, kind?)` | файлы по видам с размерами |
| `om_media_path(project_id, rel_path)` | абсолютный путь — для `comfy_upload_image` / импорта Resolve |
| `om_staging_roots()` | кандидаты staging-корней (`exists`, `writable`) |
| `om_staging_copy(project_id, rel_path, target_root?, project_name?, overwrite?)` | копирование в `<root>/<project>/<relative path>` — нужно, потому что Resolve не импортирует из произвольных папок |

---

## Часть 4. Типовые сценарии

### Начать проект

```
om_preflight() → om_pipelines() → om_pipeline_manifest("animated-explainer")
→ om_project_init("<id>", "<title>", "animated-explainer")
→ om_context_pack("animated-explainer", "research")
→ om_read_skill(path="skills/pipelines/explainer/research-director.md", section="When to Use")
```

### Сгенерировать и не попасть в двойную оплату

```
om_tool_estimate(tool="<t>", params={...})            # цена и Layer-3 скиллы
om_read_skill(path="<layer3>")                          # обязательно до вызова
om_tool_run(tool="<t>", params={...}, project_id="<id>")
  → {"mode": "job", "job_id": "job-..."}                # долгий вызов
om_job_status(project_id="<id>", job_id="job-...")      # poll, НЕ повторный вызов
om_job_result(project_id="<id>", job_id="job-...")
```

### Закрыть стадию и остановиться на гейте

```
om_checkpoint_write(project_id="<id>", stage="proposal", status="awaiting_human",
                    artifacts={...}, human_approved=false)
→ ждёт человека
```

### Зафиксировать смену решения

```
om_decision_append(project_id="<id>", stage="proposal",
                   category="voice_selection", subject="Narration TTS provider",
                   options_considered=[{...}], selected="chirp3", reason="...")
→ {"revises": ["d-..."]}   # та же (category, subject) — доска пометит «revised»
```

---

## Часть 5. Если что-то пошло не так

| Симптом | Причина | Решение |
|---|---|---|
| `{"ok": false, "error_type": "bridge"}` | неверный id/путь | исправить аргумент; не искать обход |
| GATE VIOLATION | попытка закрыть гейт без апрува | `status="awaiting_human"`, спросить человека |
| `needs_approval: true` | платный тул впервые | спросить → `om_approve_tool` → повторить |
| `idempotent_replay: true` | такой же вызов уже есть | `om_job_status`, **не** повторять |
| тул `unavailable` | нет ключа/рантайма | `install_instructions` из `om_tool_info` → показать человеку |
| доска пустая | ассет вне `projects/<id>/` | путь-контракт; `om_asset_list` для проверки |
| `imported: 0` в Resolve | sandbox: файл вне разрешённого тома | `om_staging_roots` → `om_staging_copy` → импорт |
