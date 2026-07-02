# LOR Reaction Avatar

Минимальный помощник для linux.org.ru, который обновляет аватарку пользователя на основе реакций, видимых на странице уведомлений.

Программа открывает `https://www.linux.org.ru/notifications`, считает выбранные emoji-реакции на видимой странице, рисует эти значения на локальной аватарке и загружает новую аватарку на LOR только если отображаемые значения изменились.

## Текущее поведение

- авторизуется через сохранённые cookies или через логин/пароль;
- читает `GET /notifications`;
- не нажимает сброс уведомлений и не вызывает endpoints отметки прочтения;
- считает только реакции, которые видны на странице `/notifications`;
- не считает hourly rate, delta и прирост за период;
- всегда рисует все реакции из `lor.reactions`;
- если какой-то реакции нет на странице, рисует `+0`;
- если видимые значения не изменились с прошлого успешного состояния, аватарка локально перерисовывается, но на LOR не загружается;
- если значения изменились, аватарка загружается через `/addphoto.jsp`, multipart field `file`;
- работает бесконечно, по умолчанию не чаще одного раза в 120 минут.

Поле `rates` в JSON-логе оставлено для совместимости. В актуальном режиме оно дублирует отображаемые `counts`.

## Структура проекта

```text
.
├── main.py
├── libs/
│   ├── connection.py      # HTTP-сессия, cookies, proxy, retry, rate limit
│   └── lor_client.py      # LOR, парсинг уведомлений, рендер и upload аватарки
├── configs/
│   ├── conn.yml           # сеть, proxy, cookies
│   └── user.yml           # LOR, реакции, аватарка, runner
├── avatar/                # исходная аватарка: avatar/<username>.jpg
├── data/                  # cookies, state, generated-avatar
├── Dockerfile
└── docker-compose.yml
```

## Быстрый старт

Создайте рабочие каталоги:

```bash
mkdir -p configs avatar data
```

Положите исходную аватарку:

```bash
cp my-avatar.jpg avatar/<username>.jpg
```

Если файла нет, программа создаст белый квадрат `300x300`.

## configs/user.yml

Минимальный рабочий пример:

```yaml
lor:
  base-url: "https://www.linux.org.ru"
  username: "your_username"
  password: "change_me"
  notifications-path: "/notifications"
  reactions:
    - "👍"
    - "😊"
    - "☕☕"
    - "🎉"
    - "🔥"

state:
  file: "data/reaction-state.json"

avatar:
  source-dir: "avatar"
  output-dir: "data/generated-avatar"
  default-size: [300, 300]

  font: "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
  font-size: 28
  font-color: "#00a000"

  emoji-font: "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"
  emoji-font-size: 28
  emoji-spacing: 1

  right-padding: 75
  top-padding: 72
  line-spacing: 10

  output-format: "png"
  max-file-size-kb: 100
  jpeg-quality: 90

  upload:
    form-url: "/addphoto.jsp"
    file-field: "file"

runner:
  interval-minutes: 120
  max-runs: 0
  run-on-start: true
  dry-run: false
```

### Важные параметры

`lor.reactions` — список реакций, которые нужно искать на `/notifications` и рисовать на аватарке. Порядок в конфиге совпадает с порядком строк на аватарке.

`runner.interval-minutes` — интервал между циклами. Значения меньше `120` принудительно поднимаются до `120`, чтобы контейнер не обращался к LOR чаще одного раза в два часа.

`runner.max-runs: 0` — бесконечная работа. Для одного прохода можно использовать CLI-флаг `--once`.

`runner.dry-run: true` — сгенерировать локальную аватарку и state, но не загружать её на LOR.

`state.file` — файл состояния. В нём хранится последнее видимое состояние реакций и данные по последней сгенерированной/загруженной аватарке.

`state.history-hours` больше не нужен для актуального режима отображения видимых counts. Если параметр остался в старом конфиге, его можно оставить: на отображение текущих counts он не влияет.

## configs/conn.yml

Пример без proxy:

```yaml
connection:
  timeout: 60
  request-min-interval: 1.2
  request-jitter: 0.6
  retry-count: 2
  retry-backoff: 2.0
  user-agent: "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
  accept-language: "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"

cookies:
  file: "data/lor-cookies.txt"

proxy:
  enabled: false
  url: ""
  http-url: ""
  https-url: ""
  username: ""
  password: ""
  no-proxy: ""
```

Пример SOCKS5 proxy с авторизацией:

```yaml
proxy:
  enabled: true
  url: "socks5://proxy.host:50080"
  username: "proxy_user"
  password: "proxy_password"
```

## Авторизация

Рекомендуемый способ — войти на LOR в браузере и экспортировать cookies в Netscape format:

```text
data/lor-cookies.txt
```

Права на файл:

```bash
chmod 600 ./data/lor-cookies.txt
```

Нужна cookie `remember_me`. Если её нет, программа попробует выполнить логин по `lor.username` и `lor.password`, но LOR может запросить CAPTCHA. В этом случае используйте cookies из браузера.

## Запуск

Сборка:

```bash
docker compose build
```

Запуск в фоне:

```bash
docker compose up -d
```

Логи:

```bash
docker logs lor-reaction-avatar -f
```

Один проход:

```bash
docker compose run --rm lor-reaction-avatar --once --print-proxy
```

Проверить proxy-настройки:

```bash
docker compose run --rm lor-reaction-avatar --print-proxy
```

Только логин и сохранение cookies:

```bash
docker compose run --rm lor-reaction-avatar --login
```

## docker-compose.yml

Пример сервиса:

```yaml
services:
  lor-reaction-avatar:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: lor-reaction-avatar
    restart: unless-stopped
    command: ["--print-proxy"]
    volumes:
      - ./configs:/app/configs
      - ./avatar:/app/avatar
      - ./data:/app/data
```

Не добавляйте `--once` в постоянный `command`, иначе процесс завершится после одного прохода, а Docker будет запускать контейнер заново из-за `restart: unless-stopped`.

Если Docker bridge-сеть сломана из-за iptables/nftables, можно использовать host network:

```yaml
services:
  lor-reaction-avatar:
    build:
      context: .
      dockerfile: Dockerfile
      network: host
    network_mode: host
```

`ports:` проекту не нужны.

## Вывод в лог

Пример JSON после прохода:

```json
{
  "counts": {"👍": 9, "😊": 3, "☕☕": 2, "🎉": 0, "🔥": 1},
  "rates": {"👍": 9, "😊": 3, "☕☕": 2, "🎉": 0, "🔥": 1},
  "avatar_path": "data/generated-avatar/your_username.png",
  "uploaded": true,
  "changed": true
}
```

Смысл полей:

- `counts` — количество реакций, видимых на `/notifications`;
- `rates` — то же самое, оставлено для совместимости со старым форматом;
- `avatar_path` — путь к локально сгенерированной аватарке;
- `changed` — видимые counts отличаются от прошлого сохранённого состояния;
- `uploaded` — аватарка была отправлена на LOR в этом проходе.

Если видимые counts не изменились, ожидаемый вывод:

```json
{
  "uploaded": false,
  "changed": false
}
```

В логах также будет строка:

```text
avatar upload skipped: visible reaction counts unchanged
```

## Как считается отображение

Программа не пытается вычислять полный рейтинг пользователя и не обходит всю историю. Она показывает только то, что сейчас видно на странице уведомлений.

Например, если на `/notifications` видны:

```text
👍 9
😊 3
☕☕ 2
🔥 1
```

на аватарке будет:

```text
+9  👍
+3  😊
+2  ☕☕
+0  🎉
+1  🔥
```

Если старое уведомление исчезло с видимой страницы, значение может уменьшиться. Это ожидаемое поведение текущего упрощённого режима.

## Загрузка аватарки

Загрузка выполняется через LOR userpic form:

```yaml
avatar:
  upload:
    form-url: "/addphoto.jsp"
    file-field: "file"
```

Если видимые counts не изменились, upload не выполняется. Это снижает количество обращений к endpoint загрузки аватарки.

Если counts изменились, но upload завершился ошибкой, локальная аватарка остаётся в `data/generated-avatar/`, а следующий запуск сможет попробовать снова.

## Настройка позиции и размера

Сдвинуть колонку левее:

```yaml
avatar:
  right-padding: 90
```

Сдвинуть ниже:

```yaml
avatar:
  top-padding: 85
```

Увеличить расстояние между строками:

```yaml
avatar:
  line-spacing: 12
```

Уменьшить emoji:

```yaml
avatar:
  emoji-font-size: 24
```

## Формат и лимит файла

Рекомендуемый формат — PNG:

```yaml
avatar:
  output-format: "png"
  max-file-size-kb: 100
```

Если PNG выходит больше лимита, программа пытается оптимизировать его через palette PNG. Для JPG можно управлять качеством:

```yaml
avatar:
  output-format: "jpg"
  jpeg-quality: 85
```

## Зависимости

Chromium/Playwright для актуального режима не нужен.

Минимальные Python-зависимости:

```text
requests[socks]
beautifulsoup4
PyYAML
Pillow
```

Минимальные системные пакеты для Debian slim:

```text
ca-certificates
fonts-dejavu-core
fonts-noto-color-emoji
```

## Частые проблемы

### LOR просит CAPTCHA

Используйте cookies из браузера вместо автоматического логина. Проверьте наличие `remember_me` в `data/lor-cookies.txt`.

### `/edit-profile.jsp` возвращает 404

Используйте `/addphoto.jsp`:

```yaml
avatar:
  upload:
    form-url: "/addphoto.jsp"
    file-field: "file"
```

### Emoji чёрно-белые или криво отображаются

Проверьте, что обычный шрифт и emoji-шрифт разделены:

```yaml
avatar:
  font: "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
  emoji-font: "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"
```

Для `☕☕` строка разбивается на emoji-единицы и собирается обратно, чтобы обе чашки не обрезались.

### Изменил конфиг, но ничего не поменялось

`configs/`, `avatar/` и `data/` подключены как volumes. Пересборка образа не меняет локальные файлы в этих каталогах.

После правки конфига достаточно перезапустить контейнер:

```bash
docker compose restart
```

После правки Python-кода нужна пересборка:

```bash
docker compose build --no-cache
docker compose up -d
```

### Контейнер стартует слишком часто

Проверьте, что в `docker-compose.yml` нет `--once`:

```yaml
command: ["--print-proxy"]
```

И что в конфиге бесконечный режим:

```yaml
runner:
  interval-minutes: 120
  max-runs: 0
```