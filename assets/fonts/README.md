# Шрифты субтитров

`Montserrat-ExtraBold.ttf` — основная гарнитура субтитров (стиль `Dialogue` в
`scripts/pipeline/generate_subtitles.py`). Лицензия SIL OFL 1.1, источник:
https://github.com/JulietaUla/Montserrat

Почему именно она: `Arial Black` и `Impact`, стоявшие здесь раньше, рисуют кириллицу
вторичными начертаниями — буквы получаются неровными по весу. У Montserrat кириллица
нарисована наравне с латиницей.

**Шрифт не нужно устанавливать в систему.** `render_short.py` передаёт эту папку
фильтру `ass` через параметр `fontsdir`, поэтому libass берёт гарнитуру прямо отсюда.
На новой машине достаточно `git pull` — доустанавливать ничего не требуется.

Проверить, что шрифт подхватывается:

```bash
ffmpeg -y -v warning -f lavfi -i color=0x101010:s=1080x1920:d=1 \
  -vf "ass=f='temp/fonttest.ass':fontsdir='assets/fonts'" -frames:v 1 -update 1 temp/fonttest.png
```

В выводе не должно быть `fontselect: failed to find font`.
