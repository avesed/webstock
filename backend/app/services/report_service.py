"""Report service for business logic and report generation."""

import asyncio
import html
import logging
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.core.llm import get_llm_gateway, ChatRequest, Message, Role
from app.models.report import (
    Report,
    ReportFormat,
    ReportFrequency,
    ReportSchedule,
    ReportStatus,
)
from app.models.watchlist import Watchlist, WatchlistItem
from app.schemas.report import (
    NewsSummary,
    PortfolioSummaryForReport,
    ReportContent,
    ReportScheduleCreate,
    ReportScheduleUpdate,
    StockPerformanceSummary,
    TechnicalSummary,
)

logger = logging.getLogger(__name__)

# Limits
MAX_SCHEDULES_PER_USER = 5
MAX_REPORTS_PER_USER = 30


class ReportService:
    """Service for report operations."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ============== Schedule Operations ==============

    async def get_user_schedules(self, user_id: int) -> List[ReportSchedule]:
        """Get all schedules for a user."""
        query = (
            select(ReportSchedule)
            .where(ReportSchedule.user_id == user_id)
            .order_by(ReportSchedule.created_at.desc())
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_schedule_by_id(
        self, schedule_id: str, user_id: int
    ) -> Optional[ReportSchedule]:
        """Get a schedule by ID, ensuring it belongs to the user."""
        query = select(ReportSchedule).where(
            and_(
                ReportSchedule.id == schedule_id,
                ReportSchedule.user_id == user_id,
            )
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_user_schedule_count(self, user_id: int) -> int:
        """Get the count of schedules for a user."""
        query = select(func.count(ReportSchedule.id)).where(
            ReportSchedule.user_id == user_id
        )
        result = await self.db.execute(query)
        return result.scalar() or 0

    async def create_schedule(
        self, user_id: int, data: ReportScheduleCreate
    ) -> ReportSchedule:
        """Create a new report schedule."""
        # Check schedule limit
        schedule_count = await self.get_user_schedule_count(user_id)
        if schedule_count >= MAX_SCHEDULES_PER_USER:
            raise ValueError(
                f"Maximum schedule limit ({MAX_SCHEDULES_PER_USER}) reached. "
                "Please delete some schedules before creating new ones."
            )

        schedule = ReportSchedule(
            user_id=user_id,
            name=data.name,
            frequency=data.frequency.value,
            time_of_day=data.time_of_day,
            day_of_week=data.day_of_week,
            day_of_month=data.day_of_month,
            symbols=data.symbols,
            include_portfolio=data.include_portfolio,
            include_news=data.include_news,
        )

        self.db.add(schedule)
        await self.db.commit()
        await self.db.refresh(schedule)

        logger.info(f"Created schedule {schedule.id} for user {user_id}")
        return schedule

    async def update_schedule(
        self, schedule: ReportSchedule, data: ReportScheduleUpdate
    ) -> ReportSchedule:
        """Update an existing schedule."""
        if data.name is not None:
            schedule.name = data.name
        if data.frequency is not None:
            schedule.frequency = data.frequency.value
        if data.time_of_day is not None:
            schedule.time_of_day = data.time_of_day
        if data.day_of_week is not None:
            schedule.day_of_week = data.day_of_week
        if data.day_of_month is not None:
            schedule.day_of_month = data.day_of_month
        if data.symbols is not None:
            schedule.symbols = data.symbols
        if data.include_portfolio is not None:
            schedule.include_portfolio = data.include_portfolio
        if data.include_news is not None:
            schedule.include_news = data.include_news
        if data.is_active is not None:
            schedule.is_active = data.is_active

        await self.db.commit()
        await self.db.refresh(schedule)

        logger.info(f"Updated schedule {schedule.id}")
        return schedule

    async def delete_schedule(self, schedule: ReportSchedule) -> None:
        """Delete a schedule and its reports."""
        schedule_id = schedule.id
        await self.db.delete(schedule)
        await self.db.commit()
        logger.info(f"Deleted schedule {schedule_id}")

    # ============== Report Operations ==============

    async def get_user_reports(
        self,
        user_id: int,
        page: int = 1,
        page_size: int = 20,
        schedule_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> tuple[List[Report], int]:
        """Get reports for a user with pagination."""
        query = select(Report).where(Report.user_id == user_id)

        if schedule_id:
            query = query.where(Report.schedule_id == schedule_id)
        if status:
            query = query.where(Report.status == status)

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        count_result = await self.db.execute(count_query)
        total = count_result.scalar() or 0

        # Apply pagination
        offset = (page - 1) * page_size
        query = query.order_by(Report.created_at.desc()).offset(offset).limit(page_size)

        result = await self.db.execute(query)
        reports = list(result.scalars().all())

        return reports, total

    async def get_report_by_id(
        self, report_id: str, user_id: int
    ) -> Optional[Report]:
        """Get a report by ID, ensuring it belongs to the user."""
        query = select(Report).where(
            and_(
                Report.id == report_id,
                Report.user_id == user_id,
            )
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def create_report(
        self,
        user_id: int,
        title: str,
        schedule_id: Optional[str] = None,
        format: str = ReportFormat.JSON.value,
    ) -> Report:
        """Create a new pending report."""
        report = Report(
            user_id=user_id,
            schedule_id=schedule_id,
            title=title,
            format=format,
            status=ReportStatus.PENDING.value,
        )

        self.db.add(report)
        await self.db.commit()
        await self.db.refresh(report)

        logger.info(f"Created report {report.id} for user {user_id}")
        return report

    async def delete_report(self, report: Report) -> None:
        """Delete a report."""
        report_id = report.id
        await self.db.delete(report)
        await self.db.commit()
        logger.info(f"Deleted report {report_id}")

    async def cleanup_old_reports(self, user_id: int) -> int:
        """
        Cleanup old reports keeping only the most recent MAX_REPORTS_PER_USER.

        Returns the number of deleted reports.
        """
        # Get IDs of reports to keep
        keep_query = (
            select(Report.id)
            .where(Report.user_id == user_id)
            .order_by(Report.created_at.desc())
            .limit(MAX_REPORTS_PER_USER)
        )
        keep_result = await self.db.execute(keep_query)
        keep_ids = [row[0] for row in keep_result.fetchall()]

        if not keep_ids:
            return 0

        # Delete reports not in the keep list
        delete_query = delete(Report).where(
            and_(
                Report.user_id == user_id,
                Report.id.notin_(keep_ids),
            )
        )
        result = await self.db.execute(delete_query)
        await self.db.commit()

        deleted_count = result.rowcount
        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} old reports for user {user_id}")

        return deleted_count

    # ============== Schedule Checking ==============

    async def get_due_schedules(self) -> List[ReportSchedule]:
        """
        Get all schedules that are due to run at the current time.

        This checks:
        - Daily schedules: match hour and minute
        - Weekly schedules: match day of week, hour, and minute
        - Monthly schedules: match day of month, hour, and minute
        """
        now = datetime.now(timezone.utc)
        current_time = now.time().replace(second=0, microsecond=0)
        current_hour = current_time.hour
        current_minute = current_time.minute
        current_weekday = now.weekday()
        current_day = now.day

        # Get all active schedules
        query = select(ReportSchedule).where(ReportSchedule.is_active == True)
        result = await self.db.execute(query)
        schedules = result.scalars().all()

        due_schedules = []

        for schedule in schedules:
            schedule_hour = schedule.time_of_day.hour
            schedule_minute = schedule.time_of_day.minute

            # Check time match (within 1 minute window)
            time_matches = (
                schedule_hour == current_hour
                and schedule_minute == current_minute
            )

            if not time_matches:
                continue

            # Check if already run today (prevent duplicate runs)
            if schedule.last_run_at:
                last_run_date = schedule.last_run_at.date()
                if last_run_date == now.date():
                    continue

            # Check frequency-specific conditions
            if schedule.frequency == ReportFrequency.DAILY.value:
                due_schedules.append(schedule)
            elif schedule.frequency == ReportFrequency.WEEKLY.value:
                if schedule.day_of_week == current_weekday:
                    due_schedules.append(schedule)
            elif schedule.frequency == ReportFrequency.MONTHLY.value:
                if schedule.day_of_month == current_day:
                    due_schedules.append(schedule)

        return due_schedules

    async def mark_schedule_run(self, schedule: ReportSchedule) -> None:
        """Mark a schedule as run."""
        schedule.last_run_at = datetime.now(timezone.utc)
        await self.db.commit()


class ReportGenerator:
    """Service for generating report content."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self._api_key: Optional[str] = None
        self._base_url: Optional[str] = None
        self._model: Optional[str] = None
        self._provider_type: Optional[str] = None

    async def _load_ai_config(self) -> None:
        """Load AI config from provider table (with fallback to flat columns)."""
        from app.services.settings_service import get_settings_service

        try:
            settings_service = get_settings_service()
            resolved = await settings_service.resolve_model_provider(self.db, "chat")
            self._model = resolved.model
            self._api_key = resolved.api_key
            self._base_url = resolved.base_url
            self._provider_type = resolved.provider_type
        except Exception as e:
            logger.warning(f"Failed to load AI config via provider: {e}")
            self._api_key = None
            self._base_url = None
            self._model = None
            self._provider_type = None

    async def generate_report(
        self,
        report: Report,
        symbols: List[str],
        include_portfolio: bool = False,
        include_news: bool = True,
    ) -> Report:
        """
        Generate full report content.

        Args:
            report: The report model to populate
            symbols: List of stock symbols to analyze
            include_portfolio: Whether to include portfolio summary
            include_news: Whether to include news summary

        Returns:
            Updated report with content
        """
        try:
            # Mark as generating
            report.status = ReportStatus.GENERATING.value
            await self.db.commit()

            # Gather all data
            stock_performance = await self._gather_stock_performance(symbols)
            technical_analysis = await self._gather_technical_analysis(symbols)

            news_summary = []
            if include_news:
                news_summary = await self._gather_news_summary(symbols)

            portfolio_summary = None
            if include_portfolio:
                portfolio_summary = await self._gather_portfolio_summary(
                    report.user_id
                )

            # Generate AI summary
            ai_summary = await self._generate_ai_summary(
                symbols=symbols,
                stock_performance=stock_performance,
                technical_analysis=technical_analysis,
                news_summary=news_summary,
                portfolio_summary=portfolio_summary,
            )

            # Build report content
            content = ReportContent(
                generated_at=datetime.now(timezone.utc),
                period_start=datetime.now(timezone.utc) - timedelta(days=1),
                period_end=datetime.now(timezone.utc),
                symbols=symbols,
                stock_performance=stock_performance,
                technical_analysis=technical_analysis,
                news_summary=news_summary,
                portfolio_summary=portfolio_summary,
                ai_summary=ai_summary,
            )

            # Update report
            report.content = content.model_dump(mode="json")
            report.status = ReportStatus.COMPLETED.value
            report.completed_at = datetime.now(timezone.utc)

            await self.db.commit()
            await self.db.refresh(report)

            logger.info(f"Generated report {report.id} successfully")
            return report

        except Exception as e:
            logger.exception(f"Error generating report {report.id}: {e}")
            report.status = ReportStatus.FAILED.value
            report.error_message = str(e)[:500]  # Limit error message length
            await self.db.commit()
            await self.db.refresh(report)
            return report

    async def _gather_stock_performance(
        self, symbols: List[str]
    ) -> List[StockPerformanceSummary]:
        """Gather stock performance data for all symbols."""
        from app.services.stock_service import get_stock_service

        try:
            stock_service = await get_stock_service()
            quotes = await stock_service.get_batch_quotes(symbols)

            performance_list = []
            for symbol in symbols:
                quote = quotes.get(symbol)
                if quote:
                    performance_list.append(
                        StockPerformanceSummary(
                            symbol=symbol,
                            name=quote.get("name"),
                            current_price=quote.get("price"),
                            day_change=quote.get("change"),
                            day_change_percent=quote.get("change_percent"),
                            volume=quote.get("volume"),
                            market_cap=quote.get("market_cap"),
                        )
                    )
                else:
                    performance_list.append(
                        StockPerformanceSummary(symbol=symbol)
                    )

            return performance_list

        except Exception as e:
            logger.warning(f"Error gathering stock performance: {e}")
            return [StockPerformanceSummary(symbol=s) for s in symbols]

    async def _gather_technical_analysis(
        self, symbols: List[str]
    ) -> List[TechnicalSummary]:
        """Gather basic technical indicators for all symbols."""
        from app.services.stock_service import get_stock_service

        try:
            stock_service = await get_stock_service()
            technical_list = []

            for symbol in symbols:
                try:
                    # Get historical data for technical analysis
                    history = await stock_service.get_history(
                        symbol, period="1mo", interval="1d"
                    )

                    if history and len(history) > 14:
                        # Calculate basic RSI
                        prices = [h.get("close", 0) for h in history if h.get("close")]
                        rsi = self._calculate_rsi(prices)

                        # Determine trend
                        if len(prices) >= 20:
                            sma_20 = sum(prices[-20:]) / 20
                            current_price = prices[-1] if prices else 0
                            if current_price > sma_20 * 1.02:
                                trend = "bullish"
                            elif current_price < sma_20 * 0.98:
                                trend = "bearish"
                            else:
                                trend = "neutral"
                        else:
                            trend = "neutral"

                        # Support and resistance (simplified)
                        recent_prices = prices[-20:] if len(prices) >= 20 else prices
                        support = min(recent_prices) if recent_prices else None
                        resistance = max(recent_prices) if recent_prices else None

                        technical_list.append(
                            TechnicalSummary(
                                symbol=symbol,
                                trend=trend,
                                support_level=round(support, 2) if support else None,
                                resistance_level=round(resistance, 2) if resistance else None,
                                rsi=round(rsi, 2) if rsi else None,
                            )
                        )
                    else:
                        technical_list.append(TechnicalSummary(symbol=symbol))

                except Exception as e:
                    logger.warning(f"Error calculating technical for {symbol}: {e}")
                    technical_list.append(TechnicalSummary(symbol=symbol))

            return technical_list

        except Exception as e:
            logger.warning(f"Error gathering technical analysis: {e}")
            return [TechnicalSummary(symbol=s) for s in symbols]

    def _calculate_rsi(self, prices: List[float], period: int = 14) -> Optional[float]:
        """Calculate RSI indicator."""
        if len(prices) < period + 1:
            return None

        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        recent_deltas = deltas[-(period):]

        gains = [d if d > 0 else 0 for d in recent_deltas]
        losses = [-d if d < 0 else 0 for d in recent_deltas]

        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return rsi

    async def _gather_news_summary(
        self, symbols: List[str]
    ) -> List[NewsSummary]:
        """Gather news summary for all symbols."""
        from app.services.news_service import get_news_service

        try:
            news_service = await get_news_service()
            news_summaries = []

            for symbol in symbols:
                try:
                    news = await news_service.get_news_by_symbol(symbol)

                    positive_count = 0
                    negative_count = 0
                    neutral_count = 0
                    headlines = []

                    for article in news[:10]:  # Limit to recent 10 articles
                        headlines.append(article.get("title", "")[:100])

                        sentiment = article.get("sentiment_score")
                        if sentiment is not None:
                            if sentiment > 0.2:
                                positive_count += 1
                            elif sentiment < -0.2:
                                negative_count += 1
                            else:
                                neutral_count += 1
                        else:
                            neutral_count += 1

                    news_summaries.append(
                        NewsSummary(
                            symbol=symbol,
                            total_articles=len(news),
                            positive_count=positive_count,
                            negative_count=negative_count,
                            neutral_count=neutral_count,
                            top_headlines=headlines[:5],
                        )
                    )

                except Exception as e:
                    logger.warning(f"Error gathering news for {symbol}: {e}")
                    news_summaries.append(NewsSummary(symbol=symbol))

            return news_summaries

        except Exception as e:
            logger.warning(f"Error gathering news summary: {e}")
            return [NewsSummary(symbol=s) for s in symbols]

    async def _gather_portfolio_summary(
        self, user_id: int
    ) -> Optional[PortfolioSummaryForReport]:
        """Gather portfolio summary for user."""
        from app.models.portfolio import Portfolio
        from app.services.portfolio_service import PortfolioService

        try:
            # Get default portfolio
            query = select(Portfolio).where(
                and_(
                    Portfolio.user_id == user_id,
                    Portfolio.is_default == True,
                )
            )
            result = await self.db.execute(query)
            portfolio = result.scalar_one_or_none()

            if not portfolio:
                return None

            portfolio_service = PortfolioService(self.db)
            summary = await portfolio_service.get_portfolio_summary(portfolio)

            top_gainers = []
            top_losers = []

            if summary.best_performer:
                top_gainers.append(summary.best_performer.symbol)
            if summary.worst_performer:
                top_losers.append(summary.worst_performer.symbol)

            return PortfolioSummaryForReport(
                total_value=float(summary.total_market_value) if summary.total_market_value else None,
                total_cost=float(summary.total_cost),
                total_profit_loss=float(summary.total_profit_loss) if summary.total_profit_loss else None,
                total_profit_loss_percent=summary.total_profit_loss_percent,
                day_change=float(summary.day_change) if summary.day_change else None,
                day_change_percent=summary.day_change_percent,
                holdings_count=summary.holdings_count,
                top_gainers=top_gainers,
                top_losers=top_losers,
            )

        except Exception as e:
            logger.warning(f"Error gathering portfolio summary: {e}")
            return None

    async def _generate_ai_summary(
        self,
        symbols: List[str],
        stock_performance: List[StockPerformanceSummary],
        technical_analysis: List[TechnicalSummary],
        news_summary: List[NewsSummary],
        portfolio_summary: Optional[PortfolioSummaryForReport],
    ) -> Optional[str]:
        """Generate AI summary of the report."""
        # Load AI config if not already loaded
        if self._api_key is None and self._model is None:
            await self._load_ai_config()

        model = self._model
        if not model:
            logger.warning("No LLM model configured in Admin Settings, skipping AI summary")
            return None
        if not self._api_key:
            logger.warning("No API key configured for model %s, skipping AI summary", model)
            return None

        try:
            gateway = get_llm_gateway()

            # Build context
            context_parts = []

            # Stock performance
            perf_lines = []
            for perf in stock_performance:
                if perf.current_price:
                    change_str = ""
                    if perf.day_change_percent is not None:
                        change_str = f" ({perf.day_change_percent:+.2f}%)"
                    perf_lines.append(
                        f"- {perf.symbol}: ${perf.current_price:.2f}{change_str}"
                    )
            if perf_lines:
                context_parts.append("Stock Performance:\n" + "\n".join(perf_lines))

            # Technical analysis
            tech_lines = []
            for tech in technical_analysis:
                if tech.trend:
                    rsi_str = f", RSI: {tech.rsi}" if tech.rsi else ""
                    tech_lines.append(f"- {tech.symbol}: {tech.trend}{rsi_str}")
            if tech_lines:
                context_parts.append("Technical Signals:\n" + "\n".join(tech_lines))

            # News sentiment
            news_lines = []
            for news in news_summary:
                if news.total_articles > 0:
                    sentiment = "positive" if news.positive_count > news.negative_count else (
                        "negative" if news.negative_count > news.positive_count else "mixed"
                    )
                    news_lines.append(
                        f"- {news.symbol}: {news.total_articles} articles, sentiment: {sentiment}"
                    )
            if news_lines:
                context_parts.append("News Sentiment:\n" + "\n".join(news_lines))

            # Portfolio
            if portfolio_summary and portfolio_summary.total_value:
                pf_text = (
                    f"Portfolio Value: ${portfolio_summary.total_value:,.2f}, "
                    f"P/L: {portfolio_summary.total_profit_loss_percent or 0:.2f}%"
                )
                context_parts.append(f"Portfolio:\n{pf_text}")

            context = "\n\n".join(context_parts)

            # Generate summary
            chat_request = ChatRequest(
                model=model,
                messages=[
                    Message(
                        role=Role.SYSTEM,
                        content=(
                            "You are a financial analyst providing brief market summaries. "
                            "Provide a concise 2-3 sentence summary of the market data. "
                            "Focus on key trends and actionable insights. "
                            "Be objective and avoid making specific buy/sell recommendations."
                        ),
                    ),
                    Message(
                        role=Role.USER,
                        content=f"Summarize this market data:\n\n{context}",
                    ),
                ],
            )
            # Route to the correct gateway kwargs based on provider type
            gateway_kwargs: dict = {"use_user_config": False}
            if self._provider_type == "anthropic":
                gateway_kwargs["system_anthropic_key"] = self._api_key
                gateway_kwargs["system_anthropic_base_url"] = self._base_url
            else:
                gateway_kwargs["system_api_key"] = self._api_key
                gateway_kwargs["system_base_url"] = self._base_url
            response = await gateway.chat(
                chat_request,
                **gateway_kwargs,
                purpose="report",
            )

            return response.content

        except Exception as e:
            logger.warning(f"Error generating AI summary: {e}")
            return None

    async def get_symbols_for_schedule(
        self, schedule: ReportSchedule
    ) -> List[str]:
        """Get symbols for a schedule (from schedule or watchlist)."""
        if schedule.symbols:
            return schedule.symbols

        # Get symbols from user's default watchlist
        query = (
            select(Watchlist)
            .where(
                and_(
                    Watchlist.user_id == schedule.user_id,
                    Watchlist.is_default == True,
                )
            )
            .options(selectinload(Watchlist.items))
        )
        result = await self.db.execute(query)
        watchlist = result.scalar_one_or_none()

        if watchlist and watchlist.items:
            return [item.symbol for item in watchlist.items]

        return []


def generate_html_report(report: Report) -> str:
    """Generate HTML version of the report."""
    content = report.content or {}

    # Escape helper function
    def esc(text: Any) -> str:
        return html.escape(str(text)) if text else ""

    # Build HTML
    html_parts = [
        "<!DOCTYPE html>",
        "<html lang='en'>",
        "<head>",
        "<meta charset='UTF-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1.0'>",
        f"<title>{esc(report.title)}</title>",
        "<style>",
        "body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; ",
        "max-width: 800px; margin: 0 auto; padding: 20px; color: #333; }",
        "h1 { color: #1a1a1a; border-bottom: 2px solid #3b82f6; padding-bottom: 10px; }",
        "h2 { color: #374151; margin-top: 30px; }",
        "table { width: 100%; border-collapse: collapse; margin: 15px 0; }",
        "th, td { padding: 10px; text-align: left; border-bottom: 1px solid #e5e7eb; }",
        "th { background: #f9fafb; font-weight: 600; }",
        ".positive { color: #10b981; }",
        ".negative { color: #ef4444; }",
        ".summary-box { background: #f3f4f6; padding: 15px; border-radius: 8px; margin: 20px 0; }",
        ".footer { margin-top: 40px; padding-top: 20px; border-top: 1px solid #e5e7eb; ",
        "font-size: 12px; color: #6b7280; }",
        "</style>",
        "</head>",
        "<body>",
        f"<h1>{esc(report.title)}</h1>",
        f"<p>Generated: {content.get('generated_at', '')}</p>",
    ]

    # Stock Performance Section
    stock_perf = content.get("stock_performance", [])
    if stock_perf:
        html_parts.append("<h2>Stock Performance</h2>")
        html_parts.append("<table>")
        html_parts.append(
            "<tr><th>Symbol</th><th>Price</th><th>Day Change</th></tr>"
        )
        for stock in stock_perf:
            price = stock.get("current_price")
            change_pct = stock.get("day_change_percent")
            change_class = ""
            if change_pct is not None:
                change_class = "positive" if change_pct >= 0 else "negative"
            html_parts.append(
                f"<tr><td>{esc(stock.get('symbol', ''))}</td>"
                f"<td>${esc(price) if price else 'N/A'}</td>"
                f"<td class='{change_class}'>"
                f"{f'{change_pct:+.2f}%' if change_pct is not None else 'N/A'}</td></tr>"
            )
        html_parts.append("</table>")

    # Technical Analysis Section
    tech_analysis = content.get("technical_analysis", [])
    if tech_analysis:
        html_parts.append("<h2>Technical Analysis</h2>")
        html_parts.append("<table>")
        html_parts.append(
            "<tr><th>Symbol</th><th>Trend</th><th>RSI</th><th>Support</th><th>Resistance</th></tr>"
        )
        for tech in tech_analysis:
            html_parts.append(
                f"<tr><td>{esc(tech.get('symbol', ''))}</td>"
                f"<td>{esc(tech.get('trend', 'N/A'))}</td>"
                f"<td>{esc(tech.get('rsi', 'N/A'))}</td>"
                f"<td>${esc(tech.get('support_level', 'N/A'))}</td>"
                f"<td>${esc(tech.get('resistance_level', 'N/A'))}</td></tr>"
            )
        html_parts.append("</table>")

    # News Summary Section
    news_summary = content.get("news_summary", [])
    if news_summary:
        html_parts.append("<h2>News Summary</h2>")
        for news in news_summary:
            html_parts.append(f"<h3>{esc(news.get('symbol', ''))}</h3>")
            html_parts.append(
                f"<p>Articles: {news.get('total_articles', 0)} "
                f"(Positive: {news.get('positive_count', 0)}, "
                f"Negative: {news.get('negative_count', 0)})</p>"
            )
            headlines = news.get("top_headlines", [])
            if headlines:
                html_parts.append("<ul>")
                for headline in headlines[:3]:
                    html_parts.append(f"<li>{esc(headline)}</li>")
                html_parts.append("</ul>")

    # Portfolio Summary Section
    portfolio = content.get("portfolio_summary")
    if portfolio:
        html_parts.append("<h2>Portfolio Summary</h2>")
        pl_pct = portfolio.get("total_profit_loss_percent")
        pl_class = ""
        if pl_pct is not None:
            pl_class = "positive" if pl_pct >= 0 else "negative"
        html_parts.append("<div class='summary-box'>")
        html_parts.append(
            f"<p><strong>Total Value:</strong> "
            f"${portfolio.get('total_value', 0):,.2f}</p>"
        )
        html_parts.append(
            f"<p><strong>Total P/L:</strong> "
            f"<span class='{pl_class}'>"
            f"${portfolio.get('total_profit_loss', 0):,.2f} "
            f"({f'{pl_pct:+.2f}%' if pl_pct else 'N/A'})</span></p>"
        )
        html_parts.append(
            f"<p><strong>Holdings:</strong> {portfolio.get('holdings_count', 0)}</p>"
        )
        html_parts.append("</div>")

    # AI Summary Section
    ai_summary = content.get("ai_summary")
    if ai_summary:
        html_parts.append("<h2>AI Analysis Summary</h2>")
        html_parts.append(f"<div class='summary-box'><p>{esc(ai_summary)}</p></div>")

    # Footer
    html_parts.append("<div class='footer'>")
    html_parts.append(
        "<p>This report is for informational purposes only and should not be "
        "considered as investment advice. Past performance is not indicative "
        "of future results.</p>"
    )
    html_parts.append("<p>Generated by WebStock Report System</p>")
    html_parts.append("</div>")
    html_parts.append("</body>")
    html_parts.append("</html>")

    return "\n".join(html_parts)
