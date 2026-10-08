# dh-data

Статистика драфта и офлайн-пакет для Ghost (Umbrella, Dota 2). Каждую ночь GitHub Actions берёт свежие публичные матчи из OpenDota и пересобирает файлы в ветке `draft-data`.

## Что в ветке `draft-data`

- `stats/0_<ранг>_<объём>.txt`: рейтинговые матчи (`lobby_type=7`), ранги 0, 60, 70, 75 (средний `avg_rank_tier` от), объёмы 50 000, 100 000, 200 000 последних матчей.
- `stats/1_<ранг>_40000.txt`: Captains Mode за последние 60 дней, до 40 000 матчей.
- `manifest.json`: сколько матчей в каждом наборе и за какой период, когда была сборка.
- `raw/*.bin.gz`: списки матчей, чтобы следующая сборка докачивала только новые.
- `pack/<герой>.txt`: строки покупок героя по позициям, тот же запрос, что скрипт шлёт в OpenDota (`pack.py` берёт его из публичного `draft_helper.lua`). Первая строка `t=<время> q=<версия запроса>`.
- `pack/pro_pos.json`, `pack/pro_contest.json`: позиции героев и пики/баны в про-матчах. Ghost берёт пакет, когда OpenDota недоступна, а в кэше ничего нет.

Формат файла статистики: строка `n <матчей>`, затем строки `b <герой> <игр> <побед>`, `v <ключ> <игр> <побед>` (против, ключ `a*256+b` при `a<b`, победы героя `a`) и `s <ключ> <игр> <побед>` (в одной команде).

## Откуда скрипт скачивает

По очереди, пока какой-то источник не ответит:

1. `https://raw.githubusercontent.com/gademoffshit/dh-data/draft-data/`
2. `https://cdn.jsdelivr.net/gh/gademoffshit/dh-data@draft-data/`
3. `https://gcore.jsdelivr.net/gh/gademoffshit/dh-data@draft-data/`
4. `https://fastly.jsdelivr.net/gh/gademoffshit/dh-data@draft-data/`

Если не ответил ни один, скрипт берёт статистику с диска: из кэша или из `configs\dh_pack\stats_*.txt`, сначала нужный ранг, потом ближайший. Если на диске ничего нет, он сам загружает матчи из OpenDota.

## Запуск вручную

Actions, workflow «draft data», кнопка Run workflow. Локально можно так:

```
DH_QUICK=1 python build.py data
```

Запросы к OpenDota ограничены переменной `DH_BUDGET` (по умолчанию 2500 за запуск, бесплатный лимит OpenDota 3000 в сутки).
