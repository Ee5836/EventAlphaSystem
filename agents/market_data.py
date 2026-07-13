"""Market Data Agent — fetches A-stock data via AKShare."""
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from app.extensions import db
from models.market import StockInfo, PriceSnapshot, PriceHistory

logger = logging.getLogger(__name__)

# Retry config for flaky AKShare upstream connections
MAX_RETRIES = 3
RETRY_DELAY = 1.5  # seconds between retries


def _is_connection_error(exc: Exception) -> bool:
    """Check if an exception is a transient connection error."""
    msg = str(exc).lower()
    return any(kw in msg for kw in (
        'connection', 'remote', 'timeout', 'reset', 'aborted',
        'refused', 'nodata', 'network',
    ))


class MarketDataAgent:
    """Fetches and stores market data for stocks, indices, and ETFs."""

    def __init__(self):
        self.ak = None

    def _get_ak(self):
        """Lazy-load AKShare."""
        if self.ak is None:
            try:
                import akshare as ak
                self.ak = ak
            except ImportError:
                raise RuntimeError("AKShare not installed. Run: pip install akshare")
        return self.ak

    # ── Stock info ──────────────────────────────────────────────────
    def sync_stock_info(self, symbol: str) -> Optional[StockInfo]:
        """Sync basic info for a single stock. Retries on connection errors."""
        for attempt in range(MAX_RETRIES):
            try:
                ak = self._get_ak()
                df = ak.stock_individual_info_em(symbol=symbol)

                if df is None or df.empty:
                    return None

                info_dict = {}
                for _, row in df.iterrows():
                    info_dict[str(row["item"])] = str(row["value"])

                stock = StockInfo.query.get(symbol) or StockInfo(symbol=symbol)
                stock.name = info_dict.get("股票简称", stock.name or symbol)
                market_code = str(symbol)
                stock.market = "SH" if market_code.startswith("6") else "SZ"
                stock.industry = info_dict.get("行业", stock.industry)
                stock.updated_at = datetime.now(timezone.utc)
                db.session.add(stock)
                db.session.commit()
                return stock
            except Exception as e:
                logger.warning(f"sync_stock_info attempt {attempt+1}/{MAX_RETRIES} for {symbol}: {e}")
                if attempt < MAX_RETRIES - 1 and _is_connection_error(e):
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                return None

    # ── Price history (DB-first with AKShare fallback) ───────────────
    def fetch_price_history(
        self,
        symbol: str,
        period: str = "daily",
        start_date: str = "20250101",
        end_date: Optional[str] = None,
        force_refresh: bool = False,
    ) -> list[dict]:
        """Fetch historical K-line data. DB-first, AKShare as fallback.

        Returns cached data from PriceHistory table if >= 30 rows exist.
        Only calls AKShare when DB is empty or force_refresh=True.
        """
        if end_date is None:
            end_date = date.today().strftime("%Y%m%d")

        # Step 1: Return DB cache if available
        if not force_refresh:
            cached = (
                PriceHistory.query
                .filter_by(symbol=symbol, period=period)
                .order_by(PriceHistory.date.asc())
                .all()
            )
            if len(cached) >= 30:
                logger.info(f"Using {len(cached)} cached bars for {symbol}")
                return [
                    {"date": str(r.date), "open": r.open, "high": r.high,
                     "low": r.low, "close": r.close, "volume": r.volume}
                    for r in cached
                ]

        # Step 2: Fetch from AKShare with retries
        for attempt in range(MAX_RETRIES):
            try:
                ak = self._get_ak()
                df = ak.stock_zh_a_hist(
                    symbol=symbol, period=period,
                    start_date=start_date, end_date=end_date, adjust="qfq",
                )

                if df is None or df.empty:
                    logger.warning(f"No price history for {symbol}")
                    return self._fallback_db(symbol, period)

                results = []
                for _, row in df.iterrows():
                    trade_date = row["日期"]
                    if isinstance(trade_date, str):
                        trade_date = datetime.strptime(trade_date, "%Y-%m-%d").date()

                    # Best-effort DB upsert
                    try:
                        existing = PriceHistory.query.filter_by(
                            symbol=symbol, date=trade_date, period=period
                        ).first()
                        if not existing:
                            db.session.add(PriceHistory(
                                symbol=symbol, date=trade_date,
                                open=float(row["开盘"]), high=float(row["最高"]),
                                low=float(row["最低"]), close=float(row["收盘"]),
                                volume=int(row["成交量"]), period=period,
                            ))
                    except Exception:
                        pass

                    results.append({
                        "date": str(trade_date), "open": float(row["开盘"]),
                        "high": float(row["最高"]), "low": float(row["最低"]),
                        "close": float(row["收盘"]), "volume": int(row["成交量"]),
                    })

                db.session.commit()
                logger.info(f"Fetched {len(results)} bars for {symbol} from AKShare")
                return results

            except Exception as e:
                logger.warning(f"fetch_price_history attempt {attempt+1}/{MAX_RETRIES} for {symbol}: {e}")
                try:
                    db.session.rollback()
                except Exception:
                    pass
                if attempt < MAX_RETRIES - 1 and _is_connection_error(e):
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                return self._fallback_db(symbol, period)

        return self._fallback_db(symbol, period)

    def _fallback_db(self, symbol: str, period: str) -> list[dict]:
        """Return whatever we have in DB, even if < 30 rows."""
        try:
            cached = (
                PriceHistory.query
                .filter_by(symbol=symbol, period=period)
                .order_by(PriceHistory.date.asc())
                .all()
            )
            if cached:
                logger.info(f"Returning {len(cached)} cached bars for {symbol} (fallback)")
                return [
                    {"date": str(r.date), "open": r.open, "high": r.high,
                     "low": r.low, "close": r.close, "volume": r.volume}
                    for r in cached
                ]
        except Exception:
            pass
        return []

    @staticmethod
    def generate_synthetic_history(
        symbol: str, days: int = 120,
        base_price: float = 50.0, volatility: float = 0.02,
    ) -> list[dict]:
        """Generate synthetic OHLCV data when real data is unavailable.

        Uses a random walk with mean-reversion to produce realistic-looking
        price series. Clearly marked as synthetic via logger warning.
        """
        import random
        random.seed(hash(symbol) % (2**31))  # deterministic per symbol

        results = []
        price = base_price
        today = date.today()

        for i in range(days, 0, -1):
            trade_date = today - timedelta(days=i)
            # Skip weekends
            if trade_date.weekday() >= 5:
                continue

            # Random walk with slight mean-reversion
            daily_return = random.gauss(0.0003, volatility)
            mean_reversion = (base_price - price) * 0.001
            price = price * (1 + daily_return + mean_reversion)

            intraday_range = price * volatility * random.uniform(0.5, 1.5)
            open_price = price * (1 + random.uniform(-0.005, 0.005))
            close_price = price
            high_price = max(open_price, close_price) + intraday_range * random.uniform(0.1, 0.5)
            low_price = min(open_price, close_price) - intraday_range * random.uniform(0.1, 0.5)
            volume = int(abs(random.gauss(5000000, 2000000)))

            results.append({
                "date": str(trade_date),
                "open": round(open_price, 2),
                "high": round(high_price, 2),
                "low": round(low_price, 2),
                "close": round(close_price, 2),
                "volume": volume,
            })

        logger.warning(f"Generated {len(results)} synthetic bars for {symbol} (AKShare unavailable)")
        return results

    # ── Real-time snapshot ──────────────────────────────────────────
    def fetch_snapshot(self, symbol: str) -> Optional[dict]:
        """Fetch real-time price snapshot for a stock."""
        try:
            ak = self._get_ak()
            df = ak.stock_zh_a_spot_em()

            if df is None or df.empty:
                return None

            row = df[df["代码"] == symbol]
            if row.empty:
                return None

            row = row.iloc[0]
            snapshot = PriceSnapshot.query.get(symbol) or PriceSnapshot(symbol=symbol)

            snapshot.name = str(row.get("名称", ""))
            snapshot.latest_price = float(row.get("最新价", 0))
            snapshot.change_pct = float(row.get("涨跌幅", 0))
            snapshot.volume = int(row.get("成交量", 0))
            snapshot.turnover = float(row.get("成交额", 0))
            snapshot.high = float(row.get("最高", 0))
            snapshot.low = float(row.get("最低", 0))
            snapshot.open = float(row.get("今开", 0))
            snapshot.pre_close = float(row.get("昨收", 0))
            snapshot.updated_at = datetime.now(timezone.utc)

            db.session.add(snapshot)
            db.session.commit()

            return {
                "symbol": symbol,
                "name": snapshot.name,
                "latest_price": snapshot.latest_price,
                "change_pct": snapshot.change_pct,
                "volume": snapshot.volume,
                "turnover": snapshot.turnover,
                "high": snapshot.high,
                "low": snapshot.low,
                "open": snapshot.open,
                "pre_close": snapshot.pre_close,
            }

        except Exception as e:
            logger.error(f"Failed to fetch snapshot for {symbol}: {e}")
            return None

    # ── Technical indicators ────────────────────────────────────────
    @staticmethod
    def calc_ma(prices: list[float], period: int) -> list[Optional[float]]:
        """Calculate Simple Moving Average."""
        result = []
        for i in range(len(prices)):
            if i < period - 1:
                result.append(None)
            else:
                result.append(sum(prices[i - period + 1:i + 1]) / period)
        return result

    @staticmethod
    def calc_macd(closes: list[float], fast=12, slow=26, signal=9):
        """Calculate MACD indicator. Returns (dif, dea, macd_hist)."""
        ema_fast = MarketDataAgent._calc_ema(closes, fast)
        ema_slow = MarketDataAgent._calc_ema(closes, slow)

        dif = [f - s if f is not None and s is not None else None
               for f, s in zip(ema_fast, ema_slow)]
        dea = MarketDataAgent._calc_ema([d for d in dif if d is not None], signal)
        pad = len(dif) - len(dea)
        dea = [None] * pad + dea

        macd_hist = [(d - e) * 2 if d is not None and e is not None else None
                     for d, e in zip(dif, dea)]

        return dif, dea, macd_hist

    @staticmethod
    def calc_rsi(closes: list[float], period: int = 14) -> list[Optional[float]]:
        """Calculate RSI indicator."""
        if len(closes) < period + 1:
            return [None] * len(closes)

        gains, losses = [], []
        for i in range(1, len(closes)):
            chg = closes[i] - closes[i - 1]
            gains.append(max(chg, 0))
            losses.append(max(-chg, 0))

        rsi = [None]
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

            if avg_loss == 0:
                rsi.append(100.0)
            else:
                rs = avg_gain / avg_loss
                rsi.append(100.0 - 100.0 / (1.0 + rs))

        rsi = [None] * (len(closes) - len(rsi)) + rsi
        return rsi

    @staticmethod
    def _calc_ema(data: list[float], period: int) -> list[float]:
        """Calculate EMA."""
        if len(data) < period:
            return []
        multiplier = 2 / (period + 1)
        ema = [sum(data[:period]) / period]
        for price in data[period:]:
            ema.append((price - ema[-1]) * multiplier + ema[-1])
        return ema

    # ── Index data ──────────────────────────────────────────────────
    def fetch_index_data(self, index_code: str = "000001") -> list[dict]:
        """Fetch index daily data.

        Args:
            index_code: Numeric code (e.g. "000001"), or with prefix
                        (e.g. "sh000001", "sz399001").  Auto-detects
                        Shanghai vs Shenzhen by leading digits if no
                        prefix given (0→sh, 3/4→sz).
        """
        try:
            ak = self._get_ak()
            # Auto-detect exchange prefix if not already present
            if index_code.startswith(("sh", "sz")):
                symbol = index_code
            elif index_code.startswith("0"):
                symbol = f"sh{index_code}"
            else:
                symbol = f"sz{index_code}"
            df = ak.stock_zh_index_daily(symbol=symbol)

            if df is None or df.empty:
                return []

            results = []
            for _, row in df.tail(60).iterrows():
                results.append({
                    "date": str(row["date"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": int(row["volume"]),
                })
            return results
        except Exception as e:
            logger.error(f"Failed to fetch index {index_code}: {e}")
            return []

    # ── Sector heatmap data ─────────────────────────────────────────
    def fetch_sector_performance(self) -> list[dict]:
        """Fetch sector/industry performance for heatmap.

        Tries East Money (EM) first with retries, falls back to
        TongHuaShun (THS) if EM is unavailable.
        """
        # ── Attempt 1: East Money with retries ─────────────────
        for attempt in range(MAX_RETRIES):
            try:
                ak = self._get_ak()
                df = ak.stock_board_industry_name_em()

                if df is not None and not df.empty:
                    results = []
                    for _, row in df.head(30).iterrows():
                        results.append({
                            "name": str(row.get("板块名称", "")),
                            "change_pct": float(row.get("涨跌幅", 0)),
                            "up_count": int(row.get("上涨家数", 0)),
                            "down_count": int(row.get("下跌家数", 0)),
                        })
                    logger.info(f"Fetched {len(results)} sectors from EM")
                    return results
            except Exception as e:
                logger.warning(
                    f"EM sector fetch attempt {attempt+1}/{MAX_RETRIES}: {e}"
                )
                if attempt < MAX_RETRIES - 1 and _is_connection_error(e):
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    continue

        # ── Attempt 2: TongHuaShun fallback ────────────────────
        try:
            ak = self._get_ak()
            df = ak.stock_board_industry_summary_ths()
            if df is not None and not df.empty:
                # Detect sector name column: EM uses "板块名称", THS uses "板块"
                name_col = None
                for candidate in ("板块名称", "板块"):
                    if candidate in df.columns:
                        name_col = candidate
                        break
                # Fallback: use second column by position
                if name_col is None and len(df.columns) > 1:
                    name_col = df.columns[1]

                results = []
                for _, row in df.head(30).iterrows():
                    sector_name = str(row.get(name_col, "")) if name_col else ""
                    results.append({
                        "name": sector_name,
                        "change_pct": float(row.get("涨跌幅", 0)),
                        "up_count": int(row.get("上涨家数", 0)),
                        "down_count": int(row.get("下跌家数", 0)),
                    })
                logger.info(f"Fetched {len(results)} sectors from THS (fallback)")
                return results
        except Exception as e:
            logger.error(f"THS sector fallback also failed: {e}")

        return []
