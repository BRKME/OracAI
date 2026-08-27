"""
capitulation_check.py — счётчик сигналов капитуляции по методике процентилей.

Идея заимствована из VanEck Bitcoin ChainCheck (mid-August 2026): метрика
"срабатывает", когда её последнее значение попадает в экстремальный хвост
СОБСТВЕННОЙ истории. Плюс подхода в том, что он самокалибруется — не нужно
руками подбирать пороги под текущий режим волатильности, как в bottom_prox.

ЧТО ЭТО НЕ:
Это НЕ реплика 12 сигналов VanEck. Их точный состав не опубликован (таблица в
отчёте — картинка). Здесь 8 сигналов, которые считаются из данных, уже лежащих
в data/btc.csv (CoinMetrics). Совпадение с VanEck частичное и неполное.

ГОРИЗОНТ ПРИМЕНЕНИЯ — ВАЖНО:
Собственный бэктест VanEck на кластере 8-12 сработавших сигналов:
    90 дней:  +12.8% против базовых +15.2%   → ХУЖЕ среднего
    180 дней: +32.0% против базовых +36.3%   → ХУЖЕ среднего
    1 год:    выше базового, но на 115 сильно пересекающихся наблюдениях
Цитата авторов: "Inside of six months, the historical record gives us no edge."

НАШ СОБСТВЕННЫЙ ТЕСТ (data/btc.csv, 2010-2026, expanding percentile) —
результат ХУЖЕ, чем у VanEck, и это причина не подключать счётчик к позиции.

Средняя форвардная доходность по корзинам счётчика:
    счётчик   90д      180д      365д     n(дней)
    0-1     +61.9%   +120.0%   +399.0%    2750
    2-3     +32.8%    +53.6%   +258.4%    1845
    4-5     +12.3%    +26.0%   +237.9%     589
    6+      +41.0%   +125.6%   +177.5%     166
На годовом горизонте зависимость МОНОТОННО УБЫВАЮЩАЯ: чем больше сигналов
капитуляции, тем ХУЖЕ последующая доходность. Это ровно обратное тому, ради
чего счётчик строился.

Оговорка в обе стороны: низкие корзины набиты ранними годами (2011-2013),
когда BTC рос в разы на любом входе, поэтому "база" завышена. По медиане
картина U-образная (4-5 — худшая зона, 6+ — лучшая), что осмысленно:
"часть метрик в экстремуме" = середина падения, "почти все" = настоящее дно.

Но корзина 6+ держится на 166 днях, которые складываются всего в 7 эпизодов,
причём два из них (ноябрь 2011 и октябрь 2018 - март 2019) дают 153 дня из
166. То есть фактическое n ≈ 2, а не 166. Это тот же дефект, который VanEck
признаёт за своей годовой колонкой, только у нас он выражен сильнее.

ВЫВОД: счётчик остаётся ОПИСАТЕЛЬНЫМ ("где мы в цикле"), а не предсказательным.
Не подключать ни к target_pos, ни к ступеням cycle_ladder, пока не появится
честный тест вне выборки. Проверка на известных точках работает и этого
достаточно для информационной строки:
    днища:  2015 → 4/8,  2018 → 7/8,  2022 → 4/8
    пики:   2017 → 0/8,  2021 → 1/8,  2025 → 0/8

ПОЧЕМУ ЗДЕСЬ НЕТ NUPL:
NUPL = 1 - 1/MVRV, то есть монотонное преобразование MVRV. Отдельным сигналом
он не является: добавить его — значит посчитать MVRV дважды и получить
завышенный счётчик. VanEck перечисляет обе метрики, но у них 12 слотов и
неизвестные веса; у нас пусть лучше будет 8 честных, чем 9 с дублем.
"""

import logging
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

HISTORY_CSV = "data/btc.csv"

# Порог срабатывания: 15-й процентиль собственной истории (как у VanEck)
PERCENTILE_THRESHOLD = 15.0

# Просадка цены считается по АБСОЛЮТНОМУ порогу, а не по процентилю.
# VanEck делает так же и честно объясняет почему: прошлые днища (-94%, -85%,
# -84%, -78%) ставились на рынке без спотовых ETF и с кредитным комплексом,
# который рушился каждый цикл (Celsius, 3AC, FTX). Ожидание более мелкого дна
# — это ДОПУЩЕНИЕ, и лучше держать его на виду, чем прятать внутри порога.
DRAWDOWN_ABSOLUTE_THRESHOLD = -35.0


@dataclass
class Signal:
    name: str
    value: float
    percentile: float
    fired: bool
    note: str = ""


def _percentile_of(series: pd.Series, value: float) -> float:
    """Доля исторических значений ниже текущего, в процентах."""
    s = series.dropna()
    if len(s) == 0:
        return 50.0
    return float((s < value).sum()) / len(s) * 100.0


def compute_signals(df: pd.DataFrame) -> List[Signal]:
    """Посчитать сигналы капитуляции по датафрейму CoinMetrics.

    df: колонки time, PriceUSD, CapMVRVCur, IssTotUSD, HashRate,
        AdrActCnt, volume_reported_spot_usd_1d (oldest→newest)
    """
    signals: List[Signal] = []
    df = df.sort_values("time").reset_index(drop=True)

    def add(name: str, series: pd.Series, note: str = "",
            inverted: bool = False):
        """inverted=True — капитуляция в ВЕРХНЕМ хвосте (85-й процентиль+)."""
        s = series.dropna()
        if len(s) < 100:
            logger.warning(f"{name}: недостаточно истории ({len(s)}), пропуск")
            return
        latest = float(s.iloc[-1])
        pct = _percentile_of(s, latest)
        fired = (pct >= 100 - PERCENTILE_THRESHOLD) if inverted else (pct <= PERCENTILE_THRESHOLD)
        signals.append(Signal(name, round(latest, 4), round(pct, 1), fired, note))

    # 1. MVRV — рыночная цена против средней цены владения
    if "CapMVRVCur" in df:
        add("MVRV", df["CapMVRVCur"], "цена против средней цены владения")

    # 2. Puell Multiple — дневная эмиссия в USD против своей годовой средней.
    #    Низкий = майнеры получают мало относительно нормы.
    if "IssTotUSD" in df:
        puell = df["IssTotUSD"] / df["IssTotUSD"].rolling(365, min_periods=200).mean()
        add("Puell", puell, "доход майнеров против годовой нормы")

    # 3. Mayer Multiple — цена против 200-дневной средней
    if "PriceUSD" in df:
        mayer = df["PriceUSD"] / df["PriceUSD"].rolling(200, min_periods=200).mean()
        add("Mayer", mayer, "цена против 200d MA")

    # 4. Реализованная волатильность 30d против своей годовой нормы.
    #    Вола падала секулярно по мере взросления рынка, поэтому сырое
    #    значение сравнивать с историей 2013 года нельзя — см. блок ниже.
    if "PriceUSD" in df:
        rv = df["PriceUSD"].pct_change().rolling(30).std() * (365 ** 0.5) * 100
        add("RealizedVol", rv / rv.rolling(365, min_periods=200).mean(),
            "30d вола против годовой нормы")

    # ─────────────────────────────────────────────────────────────────
    # НОРМИРОВКА РАСТУЩИХ РЯДОВ.
    # Процентиль от полной истории работает только для метрик, которые
    # возвращаются к среднему (MVRV: 3→2→1→1→2 по годам — годится).
    # Объём торгов вырос с $16M/день в 2013 до $15B в 2025, то есть в
    # ~1000 раз. Сырой процентиль такого ряда всегда будет высоким просто
    # потому, что рынок стал больше, и сигнал не загорится НИКОГДА.
    # Проверено: на всех трёх известных днищах сырые SpotVolume и
    # ActiveAddr не срабатывали ни разу.
    # Лечится делением на собственную годовую среднюю — тот же приём,
    # на котором построен Puell Multiple.
    # ─────────────────────────────────────────────────────────────────

    # 5. Спотовый объём 30d против годовой нормы — интерес рынка
    if "volume_reported_spot_usd_1d" in df:
        vol = df["volume_reported_spot_usd_1d"].rolling(30).mean()
        add("SpotVolume", vol / vol.rolling(365, min_periods=200).mean(),
            "объём торгов против годовой нормы")

    # 6. Просадка хешрейта от пика — экономика майнинга.
    #    Майнеры выключают железо, когда добыча убыточна.
    #    Это уже отношение (доля от максимума), нормировка не нужна.
    if "HashRate" in df:
        hr_dd = (df["HashRate"] / df["HashRate"].cummax() - 1) * 100
        add("HashDrawdown", hr_dd, "хешрейт против своего пика")

    # 7. Активные адреса против годовой нормы — использование сети
    if "AdrActCnt" in df:
        aa = df["AdrActCnt"].rolling(30).mean()
        add("ActiveAddr", aa / aa.rolling(365, min_periods=200).mean(),
            "активность адресов против годовой нормы")

    # 8. Просадка цены от ATH — АБСОЛЮТНЫЙ порог, см. комментарий выше
    if "PriceUSD" in df:
        price = df["PriceUSD"].dropna()
        dd = float(price.iloc[-1] / price.cummax().iloc[-1] - 1) * 100
        pct = _percentile_of((price / price.cummax() - 1) * 100, dd)
        signals.append(Signal(
            "PriceDrawdown", round(dd, 1), round(pct, 1),
            fired=dd <= DRAWDOWN_ABSOLUTE_THRESHOLD,
            note=f"абсолютный порог {DRAWDOWN_ABSOLUTE_THRESHOLD}%, не процентиль",
        ))

    return signals


def check(csv_path: str = HISTORY_CSV) -> Optional[Dict]:
    """Вернуть {fired, total, signals, verdict} или None при ошибке."""
    try:
        df = pd.read_csv(csv_path, parse_dates=["time"])
    except Exception as e:
        logger.error(f"Не читается {csv_path}: {e}")
        return None

    signals = compute_signals(df)
    if not signals:
        return None

    fired = sum(1 for s in signals if s.fired)
    total = len(signals)

    # Вердикт намеренно НЕ говорит "покупать" — см. блок про горизонт вверху.
    if fired >= total * 0.66:
        verdict = "поздняя стадия просадки; горизонт сигнала — год, не квартал"
    elif fired >= total * 0.33:
        verdict = "часть метрик в экстремуме, картина смешанная"
    else:
        verdict = "экстремумов почти нет"

    return {
        "fired": fired,
        "total": total,
        "as_of": str(df["time"].max().date()),
        "verdict": verdict,
        "signals": [asdict(s) for s in signals],
    }


def format_line(result: Optional[Dict]) -> str:
    """Одна строка для Telegram."""
    if not result:
        return ""
    fired, total = result["fired"], result["total"]
    names = [s["name"] for s in result["signals"] if s["fired"]]
    line = f"Капитуляция: {fired}/{total}"
    if names:
        line += " · " + ", ".join(names)
    return line


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    r = check()
    if not r:
        raise SystemExit("нет данных")

    print(f"\nКапитуляция: {r['fired']}/{r['total']}  (данные на {r['as_of']})")
    print(f"{r['verdict']}\n")
    print(f"{'метрика':<15}{'значение':>14}{'проц.':>8}   ")
    print("-" * 52)
    for s in r["signals"]:
        mark = "🔴 горит" if s["fired"] else "  —"
        print(f"{s['name']:<15}{s['value']:>14,.2f}{s['percentile']:>7.0f}%   {mark}")
    print()
    for s in r["signals"]:
        print(f"  {s['name']:<15} {s['note']}")
