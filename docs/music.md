# Музыка

Порядок предпочтения по безопасности (Content ID):

1. **YouTube Audio Library** — скачать в Studio, положить в `assets/music/`, записать в
   реестр `assets/music/README.md`.
2. **Локальная генерация ACE-Step** — прав третьих лиц нет вообще (Apache 2.0 на модель и
   результат), своя подложка под каждый ролик. Нужна NVIDIA с 8+ ГБ.
3. **Синтезированные подложки** установщика — простые, но безопасные.

## ACE-Step (необязательно)

ACE-Step ставится в **отдельное** окружение: ему нужен torch с CUDA, который может
конфликтовать с остальным.

```bash
git clone https://github.com/ace-step/ACE-Step.git ~/ace-step
cd ~/ace-step && python -m venv venv
# Windows: venv\Scripts\activate    macOS/Linux: source venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cu121
pip install -e .
```

Сверяйтесь с README ACE-Step — установка у них меняется. Затем укажите нарезчику путь:

```
ACE_STEP_PYTHON=C:\Users\<вы>\ace-step\venv\Scripts\python.exe   # или ~/ace-step/venv/bin/python
ACE_STEP_CHECKPOINTS=D:\ace-step-checkpoints                        # ~3.5 ГБ, скачаются сами
```

Использование:

```bash
python scripts/pipeline/gen_music.py --mood dark_debate --clip-id podcast_long01 --duration 120
python scripts/pipeline/gen_music.py --prompt "warm lo-fi, soft piano, calm, instrumental" \
    --out assets/music/my_track.wav --duration 90
```

Готовые настроения: `dark_debate`, `warm_story`, `money_focus`, `provocation`, `uplift_end`.
Рядом с треком пишется `.txt` с промптом и сидом — это ваша заметка о происхождении.

Не запускайте генерацию одновременно с 4K-рендером и с транскрипцией на GPU — не хватит памяти.
