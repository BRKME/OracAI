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
| `oracai-lp-monitor.timer` | ежечасно 07:00–23:00 | `lp_system.yml` |

`oracai-lp-monitor` ловит выход позиции из диапазона. Прогон молчит, если все
позиции в диапазоне — гейт шлёт только по алерту. Плановые отчёты в 07:00 и
19:00 не задваиваются: их защищает отметка в `state/lp_send_state.json`.
Ночью не запускается: в 03:00 с выпавшей позицией всё равно ничего не
сделать, а первый прогон в 07:00 покажет картину.

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
                            oracai-lp-monitor.timer \
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

В GitHub остались четыре разреженных слота (09:40, 12:40, 20:40, 23:40 МСК)
— чисто на случай, если VPS лёг: без них при мёртвом сервере не запустится
вообще ничего. Они стоят позже порогов страховки в `_should_send_report`
(утро 9:30, вечер 20:30 МСК), поэтому точный диспатч всегда успевает первым,
а при живом VPS эти прогоны молчат.

То есть отчёт придёт в любом случае, просто позже.

Так что смещение времени доставки на полтора часа позже обычного — сигнал,
что таймер на VPS не отработал. Смотреть `journalctl` по юниту.

## Обслуживание

Токен протухает — при истечении `dispatch.sh` вернёт HTTP 401 и запишет это
в journal. Обновить: отредактировать `/etc/oracai-dispatch.env`, перезапуск
демона не нужен, файл читается при каждом запуске.
