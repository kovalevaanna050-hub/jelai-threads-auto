# jelai-threads-auto

Автовыкладка в Threads для @jelai.anna: 10 постов с 08:00 и 10 постов с 19:00 (Asia/Saigon) с интервалом 5 минут. Каждый пост: фото товара и подпись, а в ветке ответ с артикулами WB и Ozon (только там, где товар в наличии).

- `catalog.json`: товары, артикулы, подписи и привязка фото к товару.
- `photos/`: фото товаров. Репозиторий должен быть публичным, потому что Threads забирает картинки по ссылке.
- `stock.json`: наличие по артикулам, `true` значит «в наличии». Если товар закончился или появился, поменяйте значение и сохраните через «Commit changes».
- `state.json`: какие фото и подписи уже выходили. Скрипт обновляет этот файл сам.
- `post.py`: скрипт публикации. `fetch_photos.py`: одноразовая загрузка фото из ваших постов через Threads API.

## Настройка

1. Settings → Secrets and variables → Actions → Secrets: `THREADS_TOKEN` (обязательно), `THREADS_APP_SECRET`, `GH_PAT`, `TG_BOT_TOKEN` (по желанию).
2. Actions → Setup → Run workflow.
3. Actions → Fetch photos → Run workflow.
4. Actions → Post to Threads → Run workflow с галочкой `dry_run`.
