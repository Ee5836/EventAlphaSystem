"""Market prediction web and API routes."""
import json
import logging
from datetime import datetime, timedelta, timezone

from flask import Blueprint, render_template, request, jsonify

from agents.market_data import MarketDataAgent
from models.market import StockInfo, PriceSnapshot, PriceHistory
from app.utils.industry_stocks import get_stocks_for_industries

logger = logging.getLogger(__name__)

web_bp = Blueprint("web_prediction", __name__)
api_bp = Blueprint("api_prediction", __name__, url_prefix="/api/v1/prediction")


# ── Web ─────────────────────────────────────────────────────────────
@web_bp.route("/prediction")
def prediction_page():
    """Render the prediction page."""
    # Get available stocks for dropdown
    stocks = StockInfo.query.order_by(StockInfo.symbol).limit(100).all()
    snapshots = {p.symbol: p for p in PriceSnapshot.query.all()}
    return render_template("prediction.html", stocks=stocks, snapshots=snapshots)


# ── API ─────────────────────────────────────────────────────────────
@api_bp.route("/stocks/search")
def search_stocks():
    """Search stocks by symbol or name."""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"success": True, "data": []})

    stocks = (
        StockInfo.query
        .filter(
            (StockInfo.symbol.like(f"%{q}%")) |
            (StockInfo.name.like(f"%{q}%"))
        )
        .limit(20)
        .all()
    )
    return jsonify({
        "success": True,
        "data": [s.to_dict() for s in stocks],
    })


@api_bp.route("/stocks/<string:symbol>/history")
def get_price_history(symbol: str):
    """Get historical price data for a stock."""
    period = request.args.get("period", "daily")

    # Try DB first
    rows = (
        PriceHistory.query
        .filter_by(symbol=symbol, period=period)
        .order_by(PriceHistory.date.asc())
        .all()
    )

    if rows:
        return jsonify({
            "success": True,
            "data": {
                "symbol": symbol,
                "period": period,
                "prices": [
                    {
                        "date": str(r.date),
                        "open": r.open,
                        "high": r.high,
                        "low": r.low,
                        "close": r.close,
                        "volume": r.volume,
                    }
                    for r in rows
                ],
            },
        })

    # Fetch from AKShare
    try:
        agent = MarketDataAgent()
        prices = agent.fetch_price_history(symbol, period=period)
        return jsonify({
            "success": True,
            "data": {
                "symbol": symbol,
                "period": period,
                "prices": prices,
            },
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/stocks/<string:symbol>/snapshot")
def get_snapshot(symbol: str):
    """Get real-time snapshot for a stock."""
    # Try DB first
    snap = PriceSnapshot.query.get(symbol)
    if snap and snap.updated_at:
        from datetime import datetime, timezone, timedelta
        # Ensure timezone-aware for comparison
        snap_time = snap.updated_at
        if snap_time.tzinfo is None:
            snap_time = snap_time.replace(tzinfo=timezone.utc)
        # Use cached if < 5 min old
        if (datetime.now(timezone.utc) - snap_time).total_seconds() < 300:
            return jsonify({
                "success": True,
                "data": snap.to_dict(),
            })

    # Fetch fresh
    try:
        agent = MarketDataAgent()
        data = agent.fetch_snapshot(symbol)
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/predict/<string:symbol>")
def predict_stock(symbol: str):
    """Run prediction for a stock and persist data to DB.

    Query params:
        horizon: T+1 / T+3 / T+7 (default: T+3)
        mode:   full (LLM fusion, default) / quick (rule-based, <50ms — no LLM)
        event_id: optional linked event for context
    """
    time_horizon = request.args.get("horizon", "T+3")
    mode = request.args.get("mode", "full")
    if time_horizon not in ("T+1", "T+3", "T+7"):
        return jsonify({"success": False, "error": "horizon must be T+1, T+3, or T+7"}), 400
    if mode not in ("full", "quick"):
        return jsonify({"success": False, "error": "mode must be full or quick"}), 400

    try:
        from agents.prediction import PredictionAgent
        agent = PredictionAgent()

        # Optionally pass event context
        event_id = request.args.get("event_id")
        event_context = None
        if event_id:
            from models.event import Event
            event = Event.query.get(event_id)
            if event:
                event_context = [{
                    "title": event.title,
                    "event_type": event.event_type,
                    "affected_industries": event.affected_industries_json,
                    "confidence": event.confidence,
                    "timestamp": event.created_at.isoformat() if event.created_at else None,
                }]

        result = agent.predict(symbol, time_horizon, event_context, mode=mode)

        # ── Persist stock info and price data to DB ──
        _persist_prediction_data(result)

        return jsonify({
            "success": True,
            "data": {
                "symbol": result.symbol,
                "name": result.name,
                "direction": result.direction,
                "confidence": result.confidence,
                "time_horizon": result.time_horizon,
                "target_low": result.target_low,
                "target_high": result.target_high,
                "key_factors": result.key_factors,
                "risk_flags": result.risk_flags,
                "technical_signals": result.technical_signals,
                "event_impact_score": result.event_impact_score,
                "reasoning_chain": result.reasoning_chain,
                "chart_data": result.chart_data,
            },
        })
    except Exception as e:
        logger.error(f"Prediction failed for {symbol}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def _persist_prediction_data(result) -> None:
    """Save stock info, snapshot, and price history from a prediction result to DB."""
    from app.extensions import db
    try:
        # Upsert StockInfo
        stock = StockInfo.query.get(result.symbol)
        if not stock:
            stock = StockInfo(symbol=result.symbol, name=result.name)
            db.session.add(stock)
        else:
            stock.name = result.name or stock.name

        # Upsert PriceSnapshot from chart_data if available
        cd = result.chart_data or {}
        kline = cd.get("kline", {})
        dates = kline.get("dates", [])
        closes = kline.get("close", [])
        opens = kline.get("open", [])
        highs = kline.get("high", [])
        lows = kline.get("low", [])
        volumes = kline.get("volume", [])

        if closes and len(closes) > 0:
            latest_close = float(closes[-1]) if closes[-1] is not None else None
            prev_close = float(closes[-2]) if len(closes) >= 2 and closes[-2] is not None else latest_close
            change_pct = ((latest_close - prev_close) / prev_close * 100) if (latest_close is not None and prev_close is not None and prev_close != 0) else 0

            snap = PriceSnapshot.query.get(result.symbol)
            if not snap:
                snap = PriceSnapshot(symbol=result.symbol)
                db.session.add(snap)
            snap.name = result.name or snap.name
            snap.latest_price = latest_close
            snap.change_pct = round(change_pct, 2)
            if highs and len(highs) > 0: snap.high = float(highs[-1]) if highs[-1] else None
            if lows and len(lows) > 0: snap.low = float(lows[-1]) if lows[-1] else None
            if opens and len(opens) > 0: snap.open = float(opens[-1]) if opens[-1] else None
            snap.pre_close = prev_close

        # Save PriceHistory entries (batch upsert)
        if dates and closes and len(dates) == len(closes):
            from datetime import date as date_type
            # Get existing dates for this symbol to avoid duplicates
            existing_dates = set(
                row[0] for row in
                db.session.query(PriceHistory.date)
                .filter(PriceHistory.symbol == result.symbol, PriceHistory.period == "daily")
                .all()
            )
            new_count = 0
            for i in range(len(dates)):
                try:
                    d = date_type.fromisoformat(dates[i])
                except (ValueError, TypeError):
                    continue
                if d in existing_dates:
                    continue
                ph = PriceHistory(
                    symbol=result.symbol,
                    date=d,
                    open=float(opens[i]) if i < len(opens) and opens[i] else None,
                    high=float(highs[i]) if i < len(highs) and highs[i] else None,
                    low=float(lows[i]) if i < len(lows) and lows[i] is not None else None,
                    close=float(closes[i]) if closes[i] is not None else None,
                    volume=int(volumes[i]) if i < len(volumes) and volumes[i] is not None else None,
                    period="daily",
                )
                db.session.add(ph)
                new_count += 1
            if new_count > 0:
                logger.info(f"Persisted {new_count} new price rows for {result.symbol}")

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.warning(f"Failed to persist prediction data for {result.symbol}: {e}")


@api_bp.route("/hot")
def hot_stocks():
    """Return stocks most relevant to current hot events.

    Analyzes S/A-level EventCards from the last 7 days, extracts their
    affected industries, ranks them by event frequency × severity,
    and maps them to representative A-share stocks.

    Returns top 8 stocks with event context for immediate prediction.
    """
    try:
        from models.card import EventCard

        cutoff = datetime.now(timezone.utc) - timedelta(days=7)

        hot_cards = (
            EventCard.query
            .filter(EventCard.created_at >= cutoff)
            .filter(EventCard.level.in_(["S", "A"]))
            .order_by(EventCard.level.asc(), EventCard.created_at.desc())
            .all()
        )

        if not hot_cards:
            # No hot events → return top market-cap stocks as fallback
            fallback = [
                {"symbol": "600519", "name": "贵州茅台", "reason": "市场权重股"},
                {"symbol": "000001", "name": "平安银行", "reason": "市场权重股"},
                {"symbol": "300750", "name": "宁德时代", "reason": "市场权重股"},
                {"symbol": "002594", "name": "比亚迪", "reason": "市场权重股"},
            ]
            return jsonify({"success": True, "data": {"stocks": fallback, "event_count": 0}})

        # ── Score industries by event frequency & level ──
        # industry_score[industry] = weighted count (S=3, A=1)
        industry_events: dict[str, list[dict]] = {}
        industry_scores: dict[str, float] = {}

        for card in hot_cards:
            weight = 3.0 if card.level == "S" else 1.0
            industries = card.affected_industries or []
            for ind in industries:
                ind = ind.strip()
                if not ind:
                    continue
                industry_scores[ind] = industry_scores.get(ind, 0) + weight
                if ind not in industry_events:
                    industry_events[ind] = []
                if len(industry_events[ind]) < 3:
                    industry_events[ind].append({
                        "title": card.title[:100],
                        "level": card.level,
                        "event_type": card.event_type,
                    })

        # Sort industries by score (descending)
        ranked_industries = sorted(industry_scores.items(), key=lambda x: -x[1])

        # ── Map industries to stocks ──
        industry_names = [ind for ind, _ in ranked_industries]
        industry_stock_map = get_stocks_for_industries(industry_names)

        if not industry_stock_map:
            return jsonify({
                "success": True,
                "data": {"stocks": [], "event_count": len(hot_cards),
                         "message": "当前热门事件暂无匹配股票"}
            })

        # ── Build ranked stock list ──
        stock_score: dict[str, dict] = {}  # symbol → {name, score, industry, events}
        for ind, stocks in industry_stock_map.items():
            ind_score = industry_scores.get(ind, 0)
            for symbol, name in stocks:
                if symbol not in stock_score or stock_score[symbol]["score"] < ind_score:
                    stock_score[symbol] = {
                        "symbol": symbol,
                        "name": name,
                        "score": ind_score,
                        "industry": ind,
                        "events": industry_events.get(ind, [])[:2],
                    }

        # Sort by score, take top 8
        ranked_stocks = sorted(stock_score.values(), key=lambda x: -x["score"])[:8]

        return jsonify({
            "success": True,
            "data": {
                "stocks": [
                    {
                        "symbol": s["symbol"],
                        "name": s["name"],
                        "reason": f"热门行业「{s['industry']}」· {len(s['events'])}个相关事件",
                        "industry": s["industry"],
                        "related_events": s["events"],
                    }
                    for s in ranked_stocks
                ],
                "event_count": len(hot_cards),
                "top_industries": [ind for ind, _ in ranked_industries[:10]],
            },
        })
    except Exception as e:
        logger.error(f"Hot stock recommendation failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/sectors")
def sector_performance():
    """Get sector performance data for heatmap."""
    try:
        agent = MarketDataAgent()
        sectors = agent.fetch_sector_performance()
        return jsonify({"success": True, "data": sectors})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/index/<string:code>")
def index_data(code: str):
    """Get index data."""
    try:
        agent = MarketDataAgent()
        data = agent.fetch_index_data(code)
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
