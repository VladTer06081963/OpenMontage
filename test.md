# Практическое руководство: Создание ИИ-эксплейнера от идеи до таймлайна в DaVinci Resolve через OpenMontage

> **Для кого этот гайд:** Для студентов, разработчиков, моушн-дизайнеров и режиссеров монтажа, которые хотят освоить современный пайплайн генеративного видеопроизводства (AI Video Pipeline) с нулевыми затратами на сторонние API ($0 budget) и глубокой интеграцией в профессиональный софт (DaVinci Resolve Studio).

---

## 📌 Введение и Архитектурный контекст

Современное ИИ-производство медиаконтента уходит от парадигмы «напиши один промпт в Midjourney/Runway и надейся на чудо». Профессиональный подход строится на **детерминированных пайплайнах** (pipelines), где агент управляет производственной цепочкой: от сбора научных данных и написания сценария до компиляции кода в анимационном движке (Remotion) и экспорта готовых медиа напрямую в проект NLE (Non-Linear Editor) через протокол MCP (Model Context Protocol).

В данном проекте мы создали видеоролик:
- **Тема:** «Анатомия зрения ИИ: от пикселей к скрытому пространству» (*How AI Sees The World*).
- **Хронометраж:** 43.17 секунды (1295 кадров @ 30 fps).
- **Стиль:** *Flat Motion Graphics / Cyber-minimalism* (Atelier bespoke).
- **Голос:** Milena (нативная русскоязычная нейросетевая модель macOS).
- **Стек технологий:** OpenMontage + Remotion (React, TypeScript) + FFmpeg + DaVinci Resolve MCP.
- **Бюджет:** **$0.00** (все сгенерировано и собрано локально).

---

## 🗺️ Общая схема пайплайна (State Machine)

Вся работа строго подчинена регламенту OpenMontage:

```
[1. Research] ──► [2. Proposal] ──► [3. Script] ──► [4. Scene Plan]
      │                  │                 │                │
      ▼                  ▼                 ▼                ▼
research_brief.json proposal_packet.json  script.json   scene_plan.json
                                                            │
[8. DaVinci MCP] ◄── [7. Publish] ◄── [6. Compose] ◄── [5. Edit & Assets]
        │                    │                 │                │
        ▼                    ▼                 ▼                ▼
Timeline & Markers    publish_log.json     final.mp4    asset_manifest.json
```

Каждый этап фиксируется контрольной точкой (**Checkpoint**), валидируется JSON-схемой и не позволяет двигаться дальше при наличии дефектов или пропусков.

---

## 🛠️ Шаг 1. Исследование темы (Research)

Перед тем как писать сценарий, агент проводит глубокое исследование темы, формируя документ [research_brief.json](file:///Users/mac/openMontag/projects/how-ai-sees-the-world/artifacts/research_brief.json).

### Что мы исследовали:
1. **Математическая база компьютерного зрения:** 
   - Изображение как тензор $H \times W \times C$ (высота, ширина, 3 канала RGB, значения $0..255$).
   - Скользящие ядра свертки $3\times3$ и фильтры Габора, реагирующие на перепады яркости, углы и ребра.
2. **Иерархия признаков (Feature Hierarchy):**
   - Первые слои: контрастные линии и базовые градиенты.
   - Промежуточные слои: паттерны, текстуры, формы.
   - Глубокие слои: семантические части объектов (глаза, колеса, контуры).
3. **Эволюционный сдвиг: Vision Transformers (ViT):**
   - Разбиение картинки на сетку $14\times14$ патчей размером $16\times16$ пикселей.
   - Механизм Self-Attention (внимание), соединяющий все части кадра сквозь пространство.
4. **Латентное пространство (Latent Space):**
   - Сжатие многомерного массива в одну точку-вектор в гиперпространстве смыслов (например, вектор размером 512 или 1024), где похожие концепты группируются в семантические созвездия.

---

## 💡 Шаг 2. Пакет предложений и выбор направления (Proposal)

На базе исследования формируется [proposal_packet.json](file:///Users/mac/openMontag/projects/how-ai-sees-the-world/artifacts/proposal_packet.json) с 3 концепциями:
1. **Концепт 1 (Выбранный):** «Анатомия зрения: от пикселей к скрытому пространству» — академичный, но визуально эффектный научно-популярный ролик.
2. **Концепт 2:** «Через призму фильтров: детективная история свертки» — метафорический разбор задачи детекции.
3. **Концепт 3:** «Трансформеры против Сверток: битва архитектур зрения» — фокус на алгоритмическом баттле CNN vs ViT.

Также на этом этапе пользователь выбирает движок:
- **Remotion:** Выбран из-за необходимости точной программной векторной графики, математических матриц и контролируемых 60/30 fps анимаций на чистом коде.

---

## 🎙️ Шаг 3. Сценарий, диктор и тайминги (Script & Voiceover)

Сценарий [script.json](file:///Users/mac/openMontag/projects/how-ai-sees-the-world/artifacts/script.json) разбит на 6 логических актов:
- `s1`: Введение (RGB-тензор).
- `s2`: Свертка $3\times3$ и фильтры Габора.
- `s3`: Иерархия слоев и сборка силуэтов.
- `s4`: Мозаика Vision Transformer и Attention.
- `s5`: Кульминация (Hero Moment) — латентное пространство смыслов.
- `s6`: Финал и призыв к действию (CTA).

### Локальная генерация голоса ($0):
Вместо облачных платных сервисов использован нативный высококачественный движок синтеза macOS с голосом **Milena**:
```bash
say -v Milena -r 185 "Для нас это фотография кота. Но для нейросети здесь нет ни кота, ни шерсти, ни взгляда..." -o assets/audio/s1.aiff
ffmpeg -i assets/audio/s1.aiff -codec:a libmp3lame -b:a 192k assets/audio/s1.mp3
```
Все реплики были объединены в мастер-дорожку [master_voiceover.mp3](file:///Users/mac/openMontag/projects/how-ai-sees-the-world/assets/audio/master_voiceover.mp3) (43.17 сек), и на ее основе сгенерированы точные субтитры [subtitles.srt](file:///Users/mac/openMontag/projects/how-ai-sees-the-world/assets/subtitles.srt).

---

## 📐 Шаг 4. Раскадровка и арт-дирекшн (Scene Plan & Edit Decisions)

В [scene_plan.json](file:///Users/mac/openMontag/projects/how-ai-sees-the-world/artifacts/scene_plan.json) и [edit_decisions.json](file:///Users/mac/openMontag/projects/how-ai-sees-the-world/artifacts/edit_decisions.json) закреплена визуальная система:
- **Цветовая палитра:** Глубокий космический индиго (`#070A13`, `#0D111F`), неоновый циановый акцент (`#00F5FF`), фиолетовый ультрафиолет (`#8B5CF6`), теплый золотой/розовый (`#EC4899`).
- **Типографика:** Моноширинные математические маркеры (`JetBrains Mono`, `Space Mono`) и четкие гротески (`Inter`, `system-ui`).
- **Сцены:**
  - **Сцена 1 (00:00 - 07:83):** Распад картинки на матрицу светящихся RGB-чисел.
  - **Сцена 2 (07:83 - 16:08):** Скользящая рамка ядра свертки $3\times3$ со скалярным произведением.
  - **Сцена 3 (16:08 - 23:59):** Изометрический 3D-стек слоев нейросети (линии $\to$ текстуры $\to$ глаза).
  - **Сцена 4 (23:59 - 29:76):** Разрезание кадра на $14\times14$ патчей с лазерными лучами Attention Rollout.
  - **Сцена 5 (29:76 - 36:55):** Hero moment — камера отлетает в созвездие латентного пространства векторов.
  - **Сцена 6 (36:55 - 43.17):** Брендовый финал «Геометрия смыслов» с пульсирующей кнопкой подписки.

---

## 💻 Шаг 5. Программный монтаж в Remotion (Atelier Authoring)

Вместо шаблонных плашек видео пишется как полноценное React-приложение: файл [index.tsx](file:///Users/mac/openMontag/projects/how-ai-sees-the-world/index.tsx).

### Ключевые приемы реализации анимаций:
1. **Кадровый детерминизм (Frame Determinism):**
   Все анимации привязаны к функции кадра `useCurrentFrame()` и интерполяции `interpolate()` / `spring()`:
   ```tsx
   const frame = useCurrentFrame();
   const { fps } = useVideoConfig();
   
   // Плавный зум матрицы
   const scale = interpolate(frame, [0, 60], [1.2, 1.0], {
     extrapolateRight: 'clamp',
     easing: Easing.bezier(0.16, 1, 0.3, 1),
   });
   ```

2. **Математическая сетка свертки:**
   Динамический расчет смещения ядра $3\times3$:
   ```tsx
   const col = Math.floor((frame / 8) % 6);
   const row = Math.floor((frame / 48) % 6);
   ```

3. **Синхронизация с аудио:**
   Мастер-аудио внедрено прямо в композицию через тег `<Audio src={staticFile('audio/master_voiceover.mp3')} />`, что гарантирует идеальный липсинк и совпадение визуальных акцентов со словами диктора.

### Решение технической проблемы с рендером Remotion:
При попытке сборки Remotion CLI по умолчанию пытался скачать с серверов Google архив `chrome-headless-shell`, что приводило к `ETIMEDOUT`.
**Решение:** Мы пропатчили резолвер браузера в [tools/video/video_compose.py](file:///Users/mac/openMontag/tools/video/video_compose.py), автоматически передавая путь к уже установленному системному Chrome:
```bash
npx remotion render index.tsx Explainer renders/final.mp4 --browser-executable="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
```
Рендер всех 1295 кадров прошел на GPU/M-чипе за считанные секунды!

---

## 📦 Шаг 6. Контроль качества и экспорт (QA & Export)

1. **Spotcheck кадров:**
   FFmpeg автоматически извлек 6 контрольных кадров ([frame_1.jpg](file:///Users/mac/openMontag/projects/how-ai-sees-the-world/renders/frames/frame_1.jpg) ... [frame_6.jpg](file:///Users/mac/openMontag/projects/how-ai-sees-the-world/renders/frames/frame_6.jpg)) для валидации отсутствия артефактов, черных экранов и перекрытий текста.
2. **Отчет ревью:** В [final_review.json](file:///Users/mac/openMontag/projects/how-ai-sees-the-world/artifacts/final_review.json) зафиксирован статус `pass`.
3. **Пакет публикации:** Инструмент `ExportBundle` собрал в папку [exports/](file:///Users/mac/openMontag/projects/how-ai-sees-the-world/exports/) финальное видео, файл субтитров и метаданные YouTube (заголовок, SEO-описание, таймкоды, теги).

---

## 🎛️ Шаг 7. Интеграция в DaVinci Resolve через MCP

Самый интересный этап — автоматизация монтажной станции через **Model Context Protocol**.

### Важнейший нюанс macOS Sandbox в DaVinci Resolve:
DaVinci Resolve на macOS имеет строгую политику безопасности и **не может импортировать файлы из произвольных папок** (например, из папки проекта внутри скрытых путей разработки). Попытка вызвать `ImportMedia` возвращает `imported: 0`.

**Решение:**
1. Мы опросили список разрешенных накопителей в Resolve через `media_storage.get_volumes()`.
2. Обнаружили смонтированную директорию: `/Users/mac/Movies/DaVinci Resolve/`.
3. Скопировали наши готовые файлы в эту директорию:
   - `/Users/mac/Movies/DaVinci Resolve/How-AI-Sees-The-World/final.mp4`
   - `/Users/mac/Movies/DaVinci Resolve/How-AI-Sees-The-World/master_voiceover.mp3`
4. Вызвали `media_storage.import_to_pool()`, что мгновенно дало `imported: 2`!

### Создание таймлайна и расстановка маркеров:
1. Создали таймлайн:
   ```json
   {
     "action": "create_timeline_from_clips",
     "params": {
       "name": "How AI Sees The World",
       "clip_ids": ["86af96d0-efde-4878-b5c0-cc0608fdff3c"]
     }
   }
   ```
2. Видеодорожка **V1** и аудиодорожка **A1** заполнились клипом `final.mp4`.
3. Пересчитали границы сцен из секунд в кадры таймлайна (24 fps) и расставили маркеры через инструмент `timeline_markers.add()`:
   - 🔵 **Кадр 0 (Cyan):** `Scene 1: RGB Tensor [224x224x3]`
   - 🔵 **Кадр 188 (Cyan):** `Scene 2: Conv 3x3 & Gabor`
   - 🔵 **Кадр 386 (Cyan):** `Scene 3: Feature Hierarchy`
   - 🔷 **Кадр 566 (Sky):** `Scene 4: ViT & Attention`
   - 🟣 **Кадр 714 (Fuchsia):** `Scene 5: Latent Space Hero`
   - 🟢 **Кадр 877 (Green):** `Scene 6: Geometry of Meanings CTA`
4. Переключили окно DaVinci Resolve на страницу монтажа:
   ```json
   {
     "action": "open_page",
     "params": { "page": "edit" }
   }
   ```

Теперь монтажер может открыть проект, видеть структуру всех сцен по цветным маркерам, подрезать кадры или применять грейдинг на странице Color!

---

## 🎓 Главные выводы для учеников

1. **Pipeline First:** Не приступайте к коду или генерациям без `research_brief` и `proposal`. Структура данных экономит 90% времени.
2. **Zero-Budget — это реально:** Высококачественный ролик можно сделать без платных генераторов картинок и платной озвучки, если умело использовать математический моушн-дизайн (Remotion/HTML5) и встроенные в ОС качественные синтезаторы.
3. **MCP стирает грань между кодом и NLE:** Вам больше не нужно руками перетаскивать файлы мышкой в DaVinci Resolve. Агент через MCP может создать проект, создать таймлайн, нарезать клипы, расставить маркеры и выставить параметры рендера.
4. **Учитывайте права доступа и sandbox ОС:** Автоматизация софта уровня DaVinci требует понимания ограничений путей медиа-хранилищ.

---

### 📂 Ссылки на ключевые материалы проекта:
- 🎬 [Финальное видео (MP4)](file:///Users/mac/openMontag/projects/how-ai-sees-the-world/renders/final.mp4)
- ⚛️ [Исходный код анимации Remotion (index.tsx)](file:///Users/mac/openMontag/projects/how-ai-sees-the-world/index.tsx)
- 📝 [Сценарий с таймингами (script.json)](file:///Users/mac/openMontag/projects/how-ai-sees-the-world/artifacts/script.json)
- 📊 [План сцен и раскадровка (scene_plan.json)](file:///Users/mac/openMontag/projects/how-ai-sees-the-world/artifacts/scene_plan.json)
- 📑 [Пакет для публикации YouTube (exports/)](file:///Users/mac/openMontag/projects/how-ai-sees-the-world/exports/)
