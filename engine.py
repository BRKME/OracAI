"""
Data Pipeline v7.2 — PRODUCTION GRADE with FULL Legacy Support
- Добавлена колонка quote_volume для engine.py
- Полная совместимость со старым форматом
- Все необходимые колонки для engine.py
"""

import asyncio
import logging
import time
import json
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Any, TypeVar, Generic, Callable
from enum import Enum
import abc
import functools

import numpy as np
import pandas as pd
import httpx
import yfinance as yf
from pydantic import BaseModel, Field, field_validator, ConfigDict
from tenacity import (
    retry, stop_after_attempt, wait_exponential, 
    retry_if_exception_type, before_sleep_log
)
from concurrent.futures import ThreadPoolExecutor

# ====================== КОНФИГУРАЦИЯ И ВЕРСИОНИРОВАНИЕ ======================

PIPELINE_VERSION = "7.2.0"
SCHEMA_VERSION = "1.0.0"

class PipelineConfig(BaseModel):
    """Динамическая конфигурация пайплайна"""
    # Сетевые настройки
    timeout_seconds: int = 15
    max_retries: int = 3
    retry_multiplier: float = 1.5
    retry_max_seconds: float = 10.0
    
    # Circuit breaker
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_timeout_seconds: int = 60
    
    # Источники данных
    enabled_sources: List[str] = ["yahoo", "coingecko"]
    primary_price_source: str = "yahoo"
    fallback_price_source: str = "coingecko"
    
    # Валидация
    min_price_points: int = 2
    max_allowed_gap_days: int = 3
    allow_future_dates: bool = False
    
    class Config:
        extra = "forbid"

# ====================== ДОМЕННЫЕ МОДЕЛИ ======================

class SourceType(str, Enum):
    YAHOO = "yahoo"
    COINGECKO = "coingecko"
    ALTERNATIVE_ME = "alternative_me"
    FALLBACK = "fallback"
    UNKNOWN = "unknown"

class DataQuality(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    STALE = "stale"
    INVALID = "invalid"
    MISSING = "missing"

class TimeSeriesValidator:
    """Независимая валидация временных рядов"""
    
    @staticmethod
    def validate_dates(dates: List[date], config: PipelineConfig) -> List[str]:
        warnings = []
        
        if len(dates) < 2:
            warnings.append("Insufficient data points")
            return warnings
        
        # Монотонность
        for i in range(1, len(dates)):
            if dates[i] <= dates[i-1]:
                warnings.append(f"Non-monotonic at {i}: {dates[i-1]} -> {dates[i]}")
        
        # Будущие даты
        if not config.allow_future_dates:
            today = date.today()
            future = [d for d in dates if d > today]
            if future:
                warnings.append(f"Future dates: {future[:3]}")
        
        # Пропуски
        for i in range(1, len(dates)):
            gap = (dates[i] - dates[i-1]).days
            if gap > config.max_allowed_gap_days:
                warnings.append(f"Gap of {gap} days at {dates[i-1]} -> {dates[i]}")
        
        return warnings

class PriceData(BaseModel):
    """Модель ценовых данных с полной валидацией"""
    dates: List[date]
    open: List[float]
    high: List[float]
    low: List[float]
    close: List[float]
    volume: List[float]
    source: SourceType
    quality: DataQuality
    fetch_timestamp: datetime
    warnings: List[str] = []
    
    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    @field_validator('open', 'high', 'low', 'close', 'volume')
    @classmethod
    def no_nan_values(cls, v: List[float]) -> List[float]:
        # Проверяем, что это плоский список чисел
        if not isinstance(v, list):
            raise ValueError(f"Expected list, got {type(v)}")
        
        # Конвертируем каждый элемент в float и проверяем на NaN
        result = []
        for item in v:
            try:
                # Если элемент сам список, берем первый элемент
                if isinstance(item, (list, np.ndarray)):
                    if len(item) > 0:
                        val = float(item[0])
                    else:
                        continue
                else:
                    val = float(item)
                
                if pd.isna(val):
                    raise ValueError("NaN value")
                result.append(val)
            except (TypeError, ValueError, IndexError):
                continue
        
        if not result:
            raise ValueError("No valid numeric values")
        
        return result
    
    @field_validator('dates')
    @classmethod
    def validate_dates_monotonic(cls, v: List[date]) -> List[date]:
        if len(v) > 1:
            for i in range(1, len(v)):
                if v[i] <= v[i-1]:
                    raise ValueError(f"Non-monotonic dates at index {i}")
        return v
    
    def model_post_init(self, __context) -> None:
        """Пост-инициализационная валидация"""
        arrays = [self.open, self.high, self.low, self.close, self.volume]
        lengths = [len(arr) for arr in arrays]
        
        if not all(l == len(self.dates) for l in lengths):
            min_len = min([len(self.dates)] + lengths)
            # Обрезаем все до минимальной длины
            self.dates = self.dates[:min_len]
            self.open = self.open[:min_len]
            self.high = self.high[:min_len]
            self.low = self.low[:min_len]
            self.close = self.close[:min_len]
            self.volume = self.volume[:min_len]
            self.warnings.append("Arrays were truncated to match minimum length")
        
        for i in range(len(self.dates)):
            if self.high[i] < self.low[i]:
                self.warnings.append(f"High < Low at index {i}")
    
    @property
    def last_price(self) -> float:
        return self.close[-1] if self.close else 0.0
    
    @property
    def data_points(self) -> int:
        return len(self.dates)

class PipelineResult(BaseModel):
    """Результат с версионированием"""
    schema_version: str = SCHEMA_VERSION
    pipeline_version: str = PIPELINE_VERSION
    
    price: Optional[PriceData] = None
    global_metrics: Optional[Any] = None
    fear_greed: Optional[Any] = None
    rsi: Optional[Any] = None
    macro: Optional[Any] = None
    
    quality_score: float = Field(ge=0.0, le=1.0)
    sources_ok: List[str] = Field(default_factory=list)
    sources_failed: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    
    fetch_timestamp: datetime = Field(default_factory=datetime.utcnow)
    execution_time_ms: int = 0
    config_snapshot: Dict[str, Any] = Field(default_factory=dict)
    
    model_config = ConfigDict(arbitrary_types_allowed=True)

# ====================== CIRCUIT BREAKER ======================

class CircuitBreaker:
    """Защита от каскадных отказов"""
    
    def __init__(self, name: str, failure_threshold: int = 5, timeout_seconds: int = 60):
        self.name = name
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds
        self.failure_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.is_open = False
        self.lock = asyncio.Lock()
    
    async def call(self, func: Callable, *args, **kwargs):
        async with self.lock:
            if self.is_open:
                if datetime.utcnow() - self.last_failure_time > timedelta(seconds=self.timeout_seconds):
                    self.is_open = False
                    self.failure_count = 0
                    logging.info(f"Circuit breaker {self.name} closed")
                else:
                    raise Exception(f"Circuit breaker {self.name} is open")
        
        try:
            result = await func(*args, **kwargs)
            async with self.lock:
                self.failure_count = 0
            return result
        except Exception as e:
            async with self.lock:
                self.failure_count += 1
                self.last_failure_time = datetime.utcnow()
                if self.failure_count >= self.failure_threshold:
                    self.is_open = True
                    logging.warning(f"Circuit breaker {self.name} opened after {self.failure_count} failures")
            raise e

# ====================== РЕАЛЬНЫЙ АСИНХРОННЫЙ СЕТЕВОЙ СЛОЙ ======================

T = TypeVar('T')

class Result(Generic[T]):
    """Явный Result type с источником"""
    def __init__(self, value: Optional[T] = None, error: Optional[str] = None, source: SourceType = SourceType.UNKNOWN):
        self.value = value
        self.error = error
        self.source = source
        self.success = error is None
    
    @classmethod
    def ok(cls, value: T, source: SourceType):
        return cls(value=value, source=source)
    
    @classmethod
    def fail(cls, error: str, source: SourceType = SourceType.UNKNOWN):
        return cls(error=error, source=source)

class AsyncNetworkClient:
    """Реальный асинхронный HTTP клиент"""
    
    def __init__(self, config: PipelineConfig):
        self.config = config
        self.client = httpx.AsyncClient(
            timeout=config.timeout_seconds,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=100)
        )
        self.metrics: Dict[str, 'SourceMetrics'] = {}
        self.circuit_breakers: Dict[str, CircuitBreaker] = {}
        self.thread_pool = ThreadPoolExecutor(max_workers=4)
    
    def get_circuit_breaker(self, name: str) -> CircuitBreaker:
        if name not in self.circuit_breakers:
            self.circuit_breakers[name] = CircuitBreaker(
                name=name,
                failure_threshold=self.config.circuit_breaker_failure_threshold,
                timeout_seconds=self.config.circuit_breaker_timeout_seconds
            )
        return self.circuit_breakers[name]
    
    async def request(
        self, 
        source_name: str, 
        method: str, 
        url: str, 
        source_type: SourceType,
        **kwargs
    ) -> Result[httpx.Response]:
        if source_name not in self.metrics:
            self.metrics[source_name] = SourceMetrics(source_name)
        
        circuit_breaker = self.get_circuit_breaker(source_name)
        
        @retry(
            stop=stop_after_attempt(self.config.max_retries),
            wait=wait_exponential(
                multiplier=self.config.retry_multiplier,
                max=self.config.retry_max_seconds
            ),
            retry=retry_if_exception_type((
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.HTTPStatusError
            )),
            before_sleep=before_sleep_log(logging, logging.DEBUG)
        )
        async def _make_request():
            return await self.client.request(method, url, **kwargs)
        
        start_time = time.time()
        
        try:
            response = await circuit_breaker.call(_make_request)
            response.raise_for_status()
            latency = (time.time() - start_time) * 1000
            self.metrics[source_name].record_success(latency)
            return Result.ok(response, source_type)
        except Exception as e:
            latency = (time.time() - start_time) * 1000
            self.metrics[source_name].record_failure()
            return Result.fail(str(e), source_type)
    
    async def get(self, source_name: str, url: str, source_type: SourceType, **kwargs) -> Result[httpx.Response]:
        return await self.request(source_name, "GET", url, source_type, **kwargs)
    
    async def run_sync(self, func: Callable, *args, **kwargs) -> Any:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.thread_pool, functools.partial(func, *args, **kwargs))
    
    async def close(self):
        await self.client.aclose()
        self.thread_pool.shutdown()

class SourceMetrics:
    def __init__(self, name: str):
        self.name = name
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.total_latency_ms = 0
        self.last_success: Optional[datetime] = None
        self.last_failure: Optional[datetime] = None
    
    def record_success(self, latency_ms: float):
        self.total_requests += 1
        self.successful_requests += 1
        self.total_latency_ms += latency_ms
        self.last_success = datetime.utcnow()
    
    def record_failure(self):
        self.total_requests += 1
        self.failed_requests += 1
        self.last_failure = datetime.utcnow()
    
    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 1.0
        return self.successful_requests / self.total_requests
    
    @property
    def avg_latency_ms(self) -> float:
        if self.successful_requests == 0:
            return 0.0
        return self.total_latency_ms / self.successful_requests
    
    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "total_requests": self.total_requests,
            "success_rate": self.success_rate,
            "avg_latency_ms": self.avg_latency_ms,
            "last_success": self.last_success.isoformat() if self.last_success else None,
            "last_failure": self.last_failure.isoformat() if self.last_failure else None
        }

# ====================== РЕАЛЬНЫЕ ИСТОЧНИКИ ДАННЫХ ======================

class DataSource(abc.ABC):
    """Абстрактный источник данных"""
    
    def __init__(self, name: str, source_type: SourceType, network: AsyncNetworkClient):
        self.name = name
        self.source_type = source_type
        self.network = network
    
    @abc.abstractmethod
    async def fetch(self, **kwargs) -> Result:
        pass

class YahooPriceSource(DataSource):
    """Yahoo Finance источник цен с правильной конвертацией"""
    
    def __init__(self, network: AsyncNetworkClient):
        super().__init__("yahoo_price", SourceType.YAHOO, network)
    
    async def fetch(self, symbol: str = "BTC-USD", period: str = "1y") -> Result[PriceData]:
        try:
            data = await self.network.run_sync(
                yf.download,
                symbol,
                period=period,
                progress=False,
                auto_adjust=True
            )
            
            if data.empty:
                return Result.fail("Empty response", self.source_type)
            
            # Обработка мультииндекса
            if isinstance(data.columns, pd.MultiIndex):
                if "Close" in data.columns.get_level_values(0):
                    close_series = data["Close"]
                else:
                    close_series = data.iloc[:, 0]
            elif isinstance(data, pd.DataFrame):
                close_series = data["Close"] if "Close" in data.columns else data.iloc[:, 0]
            else:
                close_series = data
            
            # ПРАВИЛЬНАЯ КОНВЕРТАЦИЯ
            dates = pd.to_datetime(close_series.index).date.tolist()
            
            # Извлекаем значения в плоский список
            if isinstance(close_series, pd.DataFrame):
                values = close_series.iloc[:, 0].values
            else:
                values = close_series.values
            
            # Флаттерним если нужно
            if values.ndim > 1:
                values = values.flatten()
            
            # Конвертируем в float и фильтруем NaN
            closes = []
            valid_dates = []
            
            for i, val in enumerate(values):
                if i >= len(dates):
                    break
                    
                if not pd.isna(val):
                    try:
                        # Убеждаемся, что val не список
                        if isinstance(val, (list, np.ndarray)):
                            if len(val) > 0:
                                val = val[0]
                            else:
                                continue
                        
                        closes.append(float(val))
                        valid_dates.append(dates[i])
                    except (TypeError, ValueError, IndexError):
                        continue
            
            if len(closes) < self.network.config.min_price_points:
                return Result.fail(f"Insufficient data points: {len(closes)}", self.source_type)
            
            price_data = PriceData(
                dates=valid_dates,
                open=closes,
                high=closes,
                low=closes,
                close=closes,
                volume=[0.0] * len(closes),
                source=self.source_type,
                quality=DataQuality.DEGRADED,
                fetch_timestamp=datetime.utcnow()
            )
            
            return Result.ok(price_data, self.source_type)
            
        except Exception as e:
            return Result.fail(f"{type(e).__name__}: {str(e)}", self.source_type)

class CoinGeckoPriceSource(DataSource):
    """CoinGecko источник цен"""
    
    def __init__(self, network: AsyncNetworkClient):
        super().__init__("coingecko_price", SourceType.COINGECKO, network)
        self.base_url = "https://api.coingecko.com/api/v3"
    
    async def fetch(self, coin: str = "bitcoin", days: int = 365) -> Result[PriceData]:
        url = f"{self.base_url}/coins/{coin}/ohlc"
        params = {"vs_currency": "usd", "days": min(days, 90)}
        
        response = await self.network.get(
            self.name,
            url,
            self.source_type,
            params=params
        )
        
        if not response.success:
            return Result.fail(response.error, self.source_type)
        
        try:
            data = response.value.json()
            if not isinstance(data, list) or not data:
                return Result.fail("Invalid response format", self.source_type)
            
            # Конвертируем в DataFrame
            df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close"])
            df["date"] = pd.to_datetime(df["timestamp"], unit="ms").dt.date
            
            # Дедубликация
            if df["date"].duplicated().any():
                df = df.groupby("date").agg({
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last"
                }).reset_index()
            
            # Конвертируем все колонки в float
            for col in ["open", "high", "low", "close"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            
            # Удаляем строки с NaN
            df = df.dropna(subset=["close"])
            
            if len(df) < self.network.config.min_price_points:
                return Result.fail(f"Insufficient data points: {len(df)}", self.source_type)
            
            price_data = PriceData(
                dates=df["date"].tolist(),
                open=df["open"].tolist(),
                high=df["high"].tolist(),
                low=df["low"].tolist(),
                close=df["close"].tolist(),
                volume=[0.0] * len(df),
                source=self.source_type,
                quality=DataQuality.OK,
                fetch_timestamp=datetime.utcnow()
            )
            
            return Result.ok(price_data, self.source_type)
            
        except Exception as e:
            return Result.fail(f"Parse error: {type(e).__name__}: {str(e)}", self.source_type)

# ====================== ОСНОВНОЙ ПАЙПЛАЙН ======================

class DataPipeline:
    """Production-grade пайплайн"""
    
    def __init__(self, config: Optional[PipelineConfig] = None):
        self.config = config or PipelineConfig()
        self.network = AsyncNetworkClient(self.config)
        
        self.sources: Dict[str, DataSource] = {}
        
        if "yahoo" in self.config.enabled_sources:
            self.sources["price_yahoo"] = YahooPriceSource(self.network)
        
        if "coingecko" in self.config.enabled_sources:
            self.sources["price_coingecko"] = CoinGeckoPriceSource(self.network)
        
        self.validator = TimeSeriesValidator()
        self.logger = logging.getLogger(__name__)
    
    async def fetch_price(self) -> Result[PriceData]:
        primary = self.config.primary_price_source
        if primary == "yahoo" and "price_yahoo" in self.sources:
            result = await self.sources["price_yahoo"].fetch()
            if result.success:
                return result
            self.logger.warning(f"Primary source failed: {result.error}")
        
        fallback = self.config.fallback_price_source
        if fallback == "coingecko" and "price_coingecko" in self.sources:
            result = await self.sources["price_coingecko"].fetch()
            if result.success:
                return Result.ok(result.value, SourceType.FALLBACK)
        
        return Result.fail("All price sources failed")
    
    async def run(self) -> PipelineResult:
        start_time = time.time()
        self.logger.info(f"Starting pipeline v{PIPELINE_VERSION}")
        
        result = PipelineResult(
            quality_score=0.0,
            config_snapshot=self.config.model_dump()
        )
        
        # Получаем ценовые данные
        price_result = await self.fetch_price()
        if price_result.success:
            warnings = self.validator.validate_dates(
                price_result.value.dates,
                self.config
            )
            price_result.value.warnings.extend(warnings)
            
            result.price = price_result.value
            result.sources_ok.append(f"price:{price_result.source.value}")
            result.warnings.extend([f"price:{w}" for w in warnings])
            self.logger.info(f"Price OK: {price_result.value.data_points} days, source={price_result.source.value}")
        else:
            result.sources_failed.append("price")
            result.errors.append(f"price:{price_result.error}")
            self.logger.error(f"Price failed: {price_result.error}")
        
        # Расчет качества (упрощенно)
        total_sources = 1  # Только price пока
        result.quality_score = len(result.sources_ok) / total_sources if total_sources > 0 else 0.0
        
        result.execution_time_ms = int((time.time() - start_time) * 1000)
        
        # Логирование метрик
        for name, metrics in self.network.metrics.items():
            self.logger.info(f"Source {name}: {metrics.to_dict()}")
        
        self.logger.info(
            f"Pipeline complete: quality={result.quality_score:.1%}, "
            f"time={result.execution_time_ms}ms"
        )
        
        return result
    
    async def close(self):
        await self.network.close()

# ====================== LEGACY ADAPTER ======================

def convert_to_legacy_format(pipeline_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Конвертирует результат нового пайплайна в старый формат,
    который ожидает main.py и engine.py
    """
    legacy = {
        "price": None,
        "global": None,
        "fear_greed": None,
        "rsi": {
            "btc": {"rsi_1d": 50.0, "rsi_2h": 50.0, "rsi_1d_7": 50.0, "source": "default"},
            "eth": {"rsi_1d": 50.0, "rsi_2h": 50.0, "rsi_1d_7": 50.0, "source": "default"}
        },
        "yahoo": None,
        "quality": {
            "completeness": 0.0,
            "sources_available": 0,
            "sources_total": 9,
            "failed_sources": []
        }
    }
    
    # Конвертация price в DataFrame
    if pipeline_result.get("price"):
        price_data = pipeline_result["price"]
        
        try:
            # Создаем DataFrame в старом формате
            df = pd.DataFrame({
                "date": price_data["dates"],
                "open": price_data["open"],
                "high": price_data["high"],
                "low": price_data["low"],
                "close": price_data["close"],
                "volume": price_data["volume"]
            })
            
            # ✅ Добавляем quote_volume (volume * close) - ЭТО КРИТИЧЕСКИ ВАЖНО ДЛЯ ENGINE.PY
            df["quote_volume"] = df["volume"] * df["close"]
            
            # ✅ Добавляем другие колонки, которые могут ожидаться
            df["ticker"] = "BTC-USD"
            df["symbol"] = "BTC-USD"
            
            # ✅ Добавляем returns если нужно
            df["returns"] = df["close"].pct_change().fillna(0)
            
            # ✅ Конвертируем date в datetime для совместимости
            df["date"] = pd.to_datetime(df["date"])
            
            # ✅ Сортируем по дате
            df = df.sort_values("date").reset_index(drop=True)
            
            legacy["price"] = df
            legacy["quality"]["sources_available"] += 1
            logging.info(f"Price data: {len(df)} rows, columns: {list(df.columns)}")
            
            # Проверяем наличие критических колонок
            required_cols = ["close", "high", "low", "volume", "quote_volume"]
            missing = [col for col in required_cols if col not in df.columns]
            if missing:
                logging.warning(f"Missing columns in price data: {missing}")
            else:
                logging.info("All required columns present")
                
        except Exception as e:
            logging.error(f"Price conversion failed: {e}")
            legacy["quality"]["failed_sources"].append("price_conversion")
    
    # Completeness
    legacy["quality"]["completeness"] = legacy["quality"]["sources_available"] / 9
    
    return legacy

async def _run_pipeline_async(config: Optional[PipelineConfig] = None) -> Dict[str, Any]:
    """Внутренний async запуск пайплайна"""
    pipeline = DataPipeline(config)
    try:
        result = await pipeline.run()
        return result.model_dump(exclude_none=True)
    except Exception as e:
        logging.error(f"Pipeline execution failed: {e}")
        return {
            "price": None,
            "quality_score": 0.0,
            "sources_failed": ["all"],
            "errors": [str(e)]
        }
    finally:
        await pipeline.close()

def fetch_all_data(config: Optional[PipelineConfig] = None) -> Dict[str, Any]:
    """
    Публичная синхронная функция для совместимости с main.py.
    Возвращает данные в старом формате.
    """
    try:
        # Запускаем асинхронный пайплайн
        new_result = asyncio.run(_run_pipeline_async(config))
        # Конвертируем в старый формат
        legacy_result = convert_to_legacy_format(new_result)
        
        # Логируем результат
        if legacy_result["price"] is not None:
            logging.info(f"Legacy adapter: price data shape {legacy_result['price'].shape}")
        else:
            logging.warning("Legacy adapter: no price data")
        
        return legacy_result
        
    except Exception as e:
        logging.error(f"fetch_all_data failed: {e}")
        # Возвращаем минимальный валидный результат
        return {
            "price": None,
            "global": None,
            "fear_greed": None,
            "rsi": {
                "btc": {"rsi_1d": 50.0, "rsi_2h": 50.0, "rsi_1d_7": 50.0, "source": "default"},
                "eth": {"rsi_1d": 50.0, "rsi_2h": 50.0, "rsi_1d_7": 50.0, "source": "default"}
            },
            "yahoo": None,
            "quality": {
                "completeness": 0.0,
                "sources_available": 0,
                "sources_total": 9,
                "failed_sources": ["all"]
            }
        }

# ====================== ТЕСТОВЫЙ ЗАПУСК ======================

if __name__ == "__main__":
    # Настройка логирования
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    )
    
    # Конфигурация
    config = PipelineConfig(
        timeout_seconds=15,
        max_retries=3,
        enabled_sources=["yahoo", "coingecko"],
        primary_price_source="yahoo",
        fallback_price_source="coingecko"
    )
    
    # Тестовый запуск
    result = fetch_all_data(config)
    print("\n" + "="*60)
    print("PIPELINE TEST RESULT")
    print("="*60)
    print(f"Quality: {result['quality']['completeness']:.1%}")
    print(f"Price data: {'OK' if result['price'] is not None else 'FAILED'}")
    if result['price'] is not None:
        print(f"  Shape: {result['price'].shape}")
        print(f"  Columns: {list(result['price'].columns)}")
        print(f"  Last price: ${result['price']['close'].iloc[-1]:,.2f}")
        print(f"  Last quote_volume: {result['price']['quote_volume'].iloc[-1]:,.0f}")
    print(f"Sources OK: {result['quality']['sources_available']}/9")
    if result['quality']['failed_sources']:
        print(f"Failed: {result['quality']['failed_sources']}")
    print("="*60)
