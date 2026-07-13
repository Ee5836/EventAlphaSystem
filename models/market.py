"""Market data models."""
import uuid
from datetime import datetime, timezone

from app.extensions import db


class StockInfo(db.Model):
    """Basic stock information."""
    __tablename__ = "stock_info"

    symbol = db.Column(db.String(16), primary_key=True)  # "000001"
    name = db.Column(db.String(64))  # "平安银行"
    market = db.Column(db.String(8))  # SH / SZ / HK / US
    industry = db.Column(db.String(64))
    list_date = db.Column(db.Date)
    updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        from app.utils.serializers import to_iso
        return {
            "symbol": self.symbol,
            "name": self.name,
            "market": self.market,
            "industry": self.industry,
            "list_date": to_iso(self.list_date),
            "updated_at": to_iso(self.updated_at),
        }


class PriceSnapshot(db.Model):
    """Real-time price snapshot."""
    __tablename__ = "price_snapshots"

    symbol = db.Column(db.String(16), primary_key=True)
    name = db.Column(db.String(64))
    latest_price = db.Column(db.Float)
    change_pct = db.Column(db.Float)  # %
    volume = db.Column(db.BigInteger)
    turnover = db.Column(db.Float)
    high = db.Column(db.Float)
    low = db.Column(db.Float)
    open = db.Column(db.Float)
    pre_close = db.Column(db.Float)
    updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        from app.utils.serializers import to_iso
        return {
            "symbol": self.symbol,
            "name": self.name,
            "latest_price": self.latest_price,
            "change_pct": self.change_pct,
            "volume": self.volume,
            "turnover": self.turnover,
            "high": self.high,
            "low": self.low,
            "open": self.open,
            "pre_close": self.pre_close,
            "updated_at": to_iso(self.updated_at),
        }


class PriceHistory(db.Model):
    """Historical K-line data."""
    __tablename__ = "price_history"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    symbol = db.Column(db.String(16), index=True)
    date = db.Column(db.Date, index=True)
    open = db.Column(db.Float)
    high = db.Column(db.Float)
    low = db.Column(db.Float)
    close = db.Column(db.Float)
    volume = db.Column(db.BigInteger)
    period = db.Column(db.String(8))  # daily / weekly / 60min
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        from app.utils.serializers import to_iso
        return {
            "id": self.id,
            "symbol": self.symbol,
            "date": to_iso(self.date),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "period": self.period,
            "created_at": to_iso(self.created_at),
        }
