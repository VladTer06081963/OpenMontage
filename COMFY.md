# Руководство по работе с ComfyUI в OpenMontage

В этом проекте ComfyUI подключен как универсальная локальная/сетевая студия генерации медиа (изображений, видео и музыки) без облачных затрат на API.

---

## 1. Архитектура и конфигурация

### Конфигурация подключения
Все параметры подключения и пути задаются в файле [`.env`](file:///Users/mac/openMontag/.env):
* **Сервер ComfyUI**: адрес настраивается через переменные [`COMFYUI_SERVER_URL`](file:///Users/mac/openMontag/.env#L123) и [`COMFY_URL`](file:///Users/mac/openMontag/.env#L125).
* **Каталог шаблонов воркфлоу**: путь задается в [`COMFY_TEMPLATES`](file:///Users/mac/openMontag/.env#L126), а также поддерживается локальная папка проекта [`ComfyUI/workflows/`](file:///Users/mac/openMontag/ComfyUI/workflows).
* **Каталог вывода**: путь задается в [`COMFY_OUT_DIR`](file:///Users/mac/openMontag/.env#L127) (а также автоматически в `projects/<project-id>/assets/` при сборке видео пайплайнами).
* **MCP-сервер**: зарегистрирован в конфигурациях [`~/.gemini/config/mcp_config.json`](file:///Users/mac/.gemini/config/mcp_config.json) и [`.agents/mcp_config.json`](file:///Users/mac/openMontag/.agents/mcp_config.json).

### Локальная папка `ComfyUI/`
В корне проекта создана папка [`ComfyUI/`](file:///Users/mac/openMontag/ComfyUI/), внесенная в [`.gitignore`](file:///Users/mac/openMontag/.gitignore):
* `ComfyUI/workflows/` — сохраняйте сюда ваши `.json` воркфлоу в формате API.
* `ComfyUI/templates/` — локальные шаблоны для быстрого повторного использования.
* `ComfyUI/outputs/` — тестовые и промежуточные генерации.

### Переменные в [`.env`](file:///Users/mac/openMontag/.env)
В файле [`.env`](file:///Users/mac/openMontag/.env) настраиваются следующие параметры:
* [`COMFYUI_SERVER_URL`](file:///Users/mac/openMontag/.env#L123) — основной сетевой/локальный URL сервера ComfyUI (REST API и WebSocket).
* [`COMFYUI_VIDEO_SERVER_URL`](file:///Users/mac/openMontag/.env#L124) — опциональный URL отдельного сервера/GPU для генерации видео.
* [`COMFY_URL`](file:///Users/mac/openMontag/.env#L125) — адрес сервера ComfyUI для MCP-инструментов.
* [`COMFY_TEMPLATES`](file:///Users/mac/openMontag/.env#L126) — директория с JSON-шаблонами воркфлоу.
* [`COMFY_OUT_DIR`](file:///Users/mac/openMontag/.env#L127) — каталог для сохранения загруженных артефактов и результатов.

---

## 2. Два способа работы

### Способ А: Нативные инструменты OpenMontage (для пайплайнов видео)

Инструменты полностью интегрированы в общую систему селекторов (`image_selector`, `video_selector`) и пайплайнов создания видео (`animated-explainer`, `cinematic`, `hybrid`):

1. **`comfyui_image`** ([tools/graphics/comfyui_image.py](file:///Users/mac/openMontag/tools/graphics/comfyui_image.py))
   * Генерация изображений: FLUX, SDXL, кастомные графы.
   * Параметры: `prompt`, `width`, `height`, `steps`, `guidance`, `seed`, `workflow_path`, `output_node`.
2. **`comfyui_video`** ([tools/video/comfyui_video.py](file:///Users/mac/openMontag/tools/video/comfyui_video.py))
   * Генерация видеоклипов: Wan 2.1/2.2, LTX-Video, Hunyuan Video, MiniMax H3.
   * Параметры: `prompt`, `duration`, `aspect_ratio`, `image_url` / `image_path` (Image-to-Video), `workflow_path`, `output_node`.
3. **`comfyui_music`** ([tools/audio/comfyui_music.py](file:///Users/mac/openMontag/tools/audio/comfyui_music.py))
   * Генерация фоновой музыки и аудиодорожек через ACE-Step v1.

### Способ Б: Прямое управление через MCP-сервер (`comfyui-mcp`)

В чате с агентом доступны специальные MCP-команды прямого взаимодействия с ComfyUI:

* **`comfy_status`** — получение информации о системе, версии ComfyUI, свободном VRAM/RAM и очереди.
* **`comfy_queue`** — просмотр текущих задач в очереди.
* **`comfy_list_nodes`** — список всех зарегистрированных типов нод на сервере (для проверки наличия Custom Nodes).
* **`comfy_object_info`** — детальная схема входов/выходов конкретной ноды.
* **`comfy_templates`** — список доступных готовых шаблонов воркфлоу в каталоге [`$COMFY_TEMPLATES`](file:///Users/mac/openMontag/.env#L126).
* **`comfy_run_workflow`** — запуск любого JSON-воркфлоу с динамической подстановкой параметров.
* **`comfy_upload_image`** — загрузка локального изображения на сервер ComfyUI для задач Image-to-Video / ControlNet.
* **`comfy_get_image`** — загрузка готового результата в локальный каталог [`$COMFY_OUT_DIR`](file:///Users/mac/openMontag/.env#L127).
* **`comfy_cancel`** — отмена выполняемой генерации.
* **`comfy_history`** — просмотр истории генераций по `prompt_id`.

---

## 3. Как правильно сохранять и запускать кастомные воркфлоу

### ВАЖНО: Формат экспорта API (API Format JSON)
Обычный файл воркфлоу, сохраненный кнопкой «Save», содержит координаты UI-графа и не подходит для серверного запуска.

1. Откройте веб-интерфейс ComfyUI по адресу из [`$COMFYUI_SERVER_URL`](file:///Users/mac/openMontag/.env#L123).
2. Нажмите **Settings (иконка шестерёнки)** в меню.
3. Включите чекбокс **«Enable Dev mode Options»**.
4. В панели меню появится кнопка **«Save (API format)»**.
5. Сохраните файл в каталог проекта: [`ComfyUI/workflows/my_workflow.json`](file:///Users/mac/openMontag/ComfyUI/workflows).

### Определение `output_node`
При запуске кастомного воркфлоу агент должен знать, какая именно нода сохраняет финальный файл:
* Найдите в вашем графе ноду сохранения: обычно это `SaveImage`, `SaveVideo` или `VHS_VideoCombine`.
* Посмотрите её ID (число в заголовке ноды, например `9`, `108`, `12`).
* Передавайте этот ID строкой: `output_node: "108"`.

### Правило видеолатентов (Temporal Latent)
Для видео-воркфлоу узел пустого латента обязательно должен быть временным (видео) латентом:
* **Правильно**: `EmptyHunyuanLatentVideo`, `WanImageToVideo`, `Wan22ImageToVideoLatent`.
* **Ошибочно**: `EmptyLatentImage` с `batch_size = N` (создаст набор несвязанных картинок вместо плавного видеоряда).

---

## 4. Примеры запросов агенту

Вы можете давать задачи на русском языке прямо в диалоге:

#### 1. Проверить статус сервера:
> *"Проверь статус ComfyUI и какие шаблоны воркфлоу сейчас доступны"*

#### 2. Генерация изображения:
> *"Сгенерируй изображение через ComfyUI: неоновый мегаполис будущего, 16:9, фотореализм"*

#### 3. Генерация видео:
> *"Сделай 5-секундный видеоклип через ComfyUI: камера плавно пролетает сквозь туманный осенний лес"*

#### 4. Запуск вашего сохраненного воркфлоу:
> *"Запусти воркфлоу `ComfyUI/workflows/cyberpunk_portrait.json` с output_node 9 и промптом 'киберпанк девушка с неоновыми волосами'"*

#### 5. Image-to-Video:
> *"Возьми картинку `ComfyUI/outputs/character.png`, загрузи в ComfyUI и анимируй через Image-to-Video воркфлоу"*

---

## 5. Устранение неполадок

* **Сервер не отвечает**:
  Проверьте доступность узла по адресу из [`.env`](file:///Users/mac/openMontag/.env):
  ```bash
  source .env && curl "${COMFYUI_SERVER_URL:-http://localhost:8188}/system_stats"
  ```
* **Отсутствуют кастомные ноды (Custom Nodes)**:
  Используйте `comfy_list_nodes`, чтобы проверить, установлены ли необходимые плагины через ComfyUI Manager на сервере.
* **Таймаут длительной генерации**:
  Если генерация видео заняла много времени и клиент прервал соединение по таймауту, задача продолжает выполняться на сервере. Вы можете запросить:
  > *"Проверь последний prompt_id в ComfyUI history и забери готовое видео"*
