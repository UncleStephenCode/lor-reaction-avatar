# LOR Reaction Avatar

Минимальный помощник для linux.org.ru: читает первую страницу уведомлений текущего пользователя, считает rate реакций за последний час и обновляет аватарку с колонкой реакций.

Поддерживаемые реакции задаются в `configs/user.yml`. По умолчанию используются:

```yaml
lor:
  reactions: ["👍", "😊", "☕☕", "🎉"]
```

## Что делает

- авторизуется на LOR через сохранённые cookies или логин/пароль;
- читает только первую страницу `https://www.linux.org.ru/notifications`;
- не сбрасывает уведомления;
- считает прирост реакций за последний час;
- при первом запуске без `data/reaction-state.json` показывает общее текущее количество реакций;
- всегда рисует все реакции из конфига: при отсутствии прироста показывает `+0`;
- пишет время активности в лог;
- генерирует PNG/JPG/TIFF аватарку до лимита, заданного в конфиге;
- загружает аватарку через LOR userpic form `/addphoto.jsp`, multipart field `file`.

## Структура проекта

```text
.
├── main.py
├── libs/
│   ├── connection.py      # общий HTTP-клиент: direct/proxy/cookies/retry
│   └── lor_client.py      # логика LOR, реакций и аватарки
├── configs/
│   ├── conn.yml          # сеть, proxy, cookies
│   └── user.yml           # LOR, реакции, аватарка, расписание
├── avatar/                # исходная аватарка: avatar/<username>.jpg|png
├── data/                  # state, cookies, generated-avatar
├── Dockerfile
└── docker-compose.yml
```

## Быстрый старт

Создайте каталоги:

```bash
mkdir -p configs avatar data
```

Положите исходную аватарку:

```bash
cp my-avatar.jpg avatar/<username>.jpg
```

Если файла нет, помощник создаст белый квадрат 300×300.

Настройте `configs/user.yml`:

```yaml
lor:
  base-url: "https://www.linux.org.ru"
  username: "your_username"
  password: "change_me"
  notifications-path: "/notifications"
  reactions: ["👍", "😊", "☕☕", "🎉"]

state:
  file: "data/reaction-state.json"
  history-hours: 3

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

Настройте `configs/conn.yml`:

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

Рекомендуемый способ — один раз войти в LOR через браузер и экспортировать cookies в Netscape формат.

Файл должен лежать здесь:

```bash
./data/lor-cookies.txt
```

Права:

```bash
chmod 600 ./data/lor-cookies.txt
```

Нужна cookie `remember_me`. Если её нет, помощник попробует выполнить логин по `lor.username` и `lor.password`, но LOR может запросить CAPTCHA.

## Запуск

Сборка:

```bash
docker compose build
```

Запуск:

```bash
docker compose up
```

Один цикл проверки:

```bash
docker compose run --rm lor-reaction-avatar --once --print-proxy
```

Только проверить proxy-настройки:

```bash
docker compose run --rm lor-reaction-avatar --print-proxy
```

Только логин и сохранение cookies:

```bash
docker compose run --rm lor-reaction-avatar --login
```

## Состояние и rate

State хранится в:

```text
data/reaction-state.json
```

Правила расчёта:

- если `reaction-state.json` отсутствует, rate равен текущему общему количеству реакций;
- если state есть, rate считается как прирост относительно baseline за последний час;
- если прироста нет, всё равно рисуются все реакции из `lor.reactions` с `+0`.

Принудительно повторить первый запуск:

```bash
rm -f ./data/reaction-state.json
docker compose run --rm lor-reaction-avatar --once --print-proxy
```

## Лог активности

В каждом цикле выводится JSON со счётчиками:

```json
{
  "counts": {"👍": 9, "😊": 3, "☕☕": 2, "🎉": 0},
  "rates": {"👍": 0, "😊": 0, "☕☕": 0, "🎉": 0},
  "avatar_path": "data/generated-avatar/your_username.png",
  "uploaded": true,
  "changed": true
}
```

Также выводится строка активности:

```text
activity: at=2026-07-01 14:23:10 MSK active=yes rate_sum=14 last_activity_at=2026-07-01 14:23:10 MSK inactive_for=0s
```

## Настройка позиции текста

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

LOR принимает PNG, JPG и TIFF. Рекомендуется PNG:

```yaml
avatar:
  output-format: "png"
  max-file-size-kb: 100
```

Если файл больше лимита, помощник пытается уменьшить/оптимизировать результат. Для JPG можно управлять качеством:

```yaml
avatar:
  jpeg-quality: 85
```

## Docker network

Если Docker bridge-сеть сломана из-за iptables/nftables, можно запускать контейнер через host network:

```yaml
services:
  lor-reaction-avatar:
    build:
      context: .
      dockerfile: Dockerfile
      network: host
    network_mode: host
```

`ports:` для этого проекта не нужны.

## Частые проблемы

### LOR просит CAPTCHA

Используйте cookies из браузера вместо автоматического логина. Проверьте наличие `remember_me` в `data/lor-cookies.txt`.

### `/edit-profile.jsp` возвращает 404

Для загрузки аватарки используется `/addphoto.jsp`, поле файла `file`. Проверьте:

```yaml
avatar:
  upload:
    form-url: "/addphoto.jsp"
    file-field: "file"
```

### Emoji криво отображаются или обрезаются

Проверьте, что обычный шрифт и emoji-шрифт разделены:

```yaml
avatar:
  font: "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
  emoji-font: "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"
```

Для `☕☕` используется отдельная сборка emoji-единиц, поэтому не надо заменять реакцию на одну картинку вручную.

### Конфиг не меняется после rebuild

`configs/`, `avatar/` и `data/` подключены как volumes. Пересборка образа не меняет локальные файлы в этих каталогах. Правьте локальный `./configs/user.yml` и перезапускайте контейнер:

```bash
docker compose restart
```
