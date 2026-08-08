# Archive

Файлы отсюда **не участвуют в рантайме**: их никто не импортирует и ни один
workflow их не запускает. Перенесены сюда 08.08.2026, чтобы корень репозитория
отражал реально работающую систему.

Ничего не удалено — при необходимости просто верни файл обратно в корень
(`git mv archive/<file>.py .`).

## Отключённый weekly-дайджест

| Файл | Почему |
|---|---|
| `lp_weekly_digest.py` | Воркфлоу отключён 14.06.2026 — unlock-инфо переехала в ежедневный отчёт `lp_system`. Скрипт полгода не запускался и дублировал `check_unlocks_ai()` из `lp_system.py` |
| `lp-weekly-digest.yml.disabled` | Тот самый воркфлоу с закомментированным cron. Убран из `.github/workflows/`, чтобы не мозолил глаза |
| `lp_unlock_monitor.py` | Отдельный скрипт разовой проверки разлоков. Логика целиком живёт в `lp_system.py` (Stage 4), на него никто не ссылался |

Модуль был полностью изолирован: никто его не импортировал, и файлы
`state/lp_weekly_digest.json` / `state/lp_digest_history.json` никто, кроме
него, не читал. Эти файлы оставлены в `state/` — там исторические понедельные
цифры, включая пересчёт с учётом выводов $300/нед.

Если понадобится вернуть:
```
git mv archive/lp_weekly_digest.py .
git mv archive/lp-weekly-digest.yml.disabled .github/workflows/lp-weekly-digest.yml
# и раскомментировать cron внутри
```

## Осиротели после рефакторинга ежедневного отчёта

| Файл | Почему |
|---|---|
| `lp_advisor.py` | AI-саммари убрано из daily-отчёта; `run_advisor()` в `lp_system.py` больше не вызывалась и удалена |
| `lp_hedge_engine.py` | Hedge-блок убран из daily-отчёта. Строка «хедж ОБЯЗАТЕЛЕН / рекомендован» в сообщении OracAI приходит из `telegram_bot.py` (lp_policy), а не отсюда |

## Поколения бэктестов, вытесненные `backtest_v5.py` / `backtest_honest.py`

`backtest.py`, `backtest_cfo.py`, `backtest_combined.py`, `backtest_lp.py`,
`backtest_v16.py` — последние правки февраль 2026.

Актуальные бэктесты остались в корне:
- `backtest_v5.py` — основной (запускается из `backtest.yml`)
- `backtest_honest.py` — используется как модуль
- `backtest_engine_real.py` — используется как модуль

## Одноразовые research-скрипты

`research_conflict.py`, `research_real_prod.py`, `research_tuning_sweep.py`,
`walk_forward_test.py`, `ablation_audit.py`, `analyze_prod_log.py`,
`make_charts.py` — писались под конкретные исследования (апрель–июнь 2026),
их выводы уже зашиты в комментарии к рабочему коду. Например, обоснование
gating'а drawdown defender в `telegram_bot.py` ссылается на
`research_real_prod.py` и `research_conflict.py`.
