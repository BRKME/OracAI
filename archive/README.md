# Archive

Файлы отсюда **не участвуют в рантайме**: их никто не импортирует и ни один
workflow их не запускает. Перенесены сюда 08.08.2026, чтобы корень репозитория
отражал реально работающую систему.

Ничего не удалено — при необходимости просто верни файл обратно в корень
(`git mv archive/<file>.py .`).

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
