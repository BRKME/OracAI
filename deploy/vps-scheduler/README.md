# Внешний планировщик OracAI на VPS

## Зачем

GitHub Actions на free-tier не соблюдает расписание scheduled-воркфлоу.
Замер по 25 последовательным прогонам `lp_system.yml` (26–31.08.2026):

| | |
|---|---|
| задержка от слота | 2–114 мин, медиана ~50 |
| разрывы между прогонами | до 13.4 часа, по 5 слотов подряд пропущено |

Из-за этого утренний LP-отчёт приходил в 10:32, 08:57, 11:58 МСК вместо 7:00.

Прогоны, запущенные через `workflow_dispatch`, под эту раздачу не попадают —
GitHub стартует их за секунды. Поэтому время задаёт VPS, а GitHub выполняет.

Код остаётся в GitHub Actions: секреты (RPC, Telegram, OpenAI) никуда не
переезжают, на VPS живёт только токен на запуск воркфлоу.

## Что ставится

| юнит | время (МСК) | что запускает |
|---|---|---|
| `oracai-lp-morning.timer` | 07:00 | `lp_system.yml` |
| `oracai-lp-evening.timer` | 19:00 | `lp_system.yml` |
| `oracai-dispatch@regime_check.timer` | 07:40 | `regime_check.yml` |

`Persistent=true` — если VPS был выключен в момент срабатывания, задание
выполнится сразу после загрузки.

## Установка

Токен нужен с правом **Actions: Read and write** на репозиторий `BRKME/OracAI`.

```bash
# 1. Скрипт
sudo mkdir -p /opt/oracai-dispatch
sudo cp dispatch.sh /opt/oracai-dispatch/
sudo chmod 755 /opt/oracai-dispatch/dispatch.sh

# 2. Токен — файл читает только root
sudo tee /etc/oracai-dispatch.env >/dev/null <<'EOF'
GH_TOKEN=github_pat_ВСТАВЬ_СЮДА
GH_REPO=BRKME/OracAI
GH_REF=main
EOF
sudo chmod 600 /etc/oracai-dispatch.env

# 3. Юниты
sudo cp oracai-dispatch@.service /etc/systemd/system/
sudo cp oracai-*.timer          /etc/systemd/system/
sudo systemctl daemon-reload

# 4. Включаем
sudo systemctl enable --now oracai-lp-morning.timer \
                            oracai-lp-evening.timer \
                            oracai-dispatch@regime_check.timer
```

## Проверка

```bash
# Разовый запуск прямо сейчас — в Telegram должен прийти LP-отчёт
sudo /opt/oracai-dispatch/dispatch.sh lp_system.yml

# Когда сработают таймеры
systemctl list-timers 'oracai-*'

# Логи
journalctl -u 'oracai-dispatch@*' -n 50 --no-pager
```

`list-timers` должен показывать NEXT в 07:00 / 19:00 / 07:40 по московскому
времени независимо от того, в какой зоне живёт сам сервер — зона зашита в
`OnCalendar`.

## Страховка

Расписание в GitHub не удалено: `lp_system.yml` по-прежнему крутится каждые
2 часа для алертов о выходе из диапазона. Если VPS не достучится, гейт в
`lp_system.py` отправит отчёт сам — утром после 9:30 МСК, вечером после 20:30.
То есть отчёт придёт в любом случае, просто позже.

Так что смещение времени доставки на полтора часа позже обычного — сигнал,
что таймер на VPS не отработал. Смотреть `journalctl` по юниту.

## Обслуживание

Токен протухает — при истечении `dispatch.sh` вернёт HTTP 401 и запишет это
в journal. Обновить: отредактировать `/etc/oracai-dispatch.env`, перезапуск
демона не нужен, файл читается при каждом запуске.
