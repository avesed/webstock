"""Portfolio service for business logic and calculations."""

import asyncio
import logging
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.portfolio import Holding, Portfolio, Transaction, TransactionType
from app.schemas.portfolio import (
    HoldingWithQuote,
    PortfolioSummary,
    TransactionCreate,
)
from app.services.stock_service import get_stock_service

logger = logging.getLogger(__name__)


class PortfolioService:
    """Service for portfolio operations and calculations."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ============== Portfolio Operations ==============

    async def get_user_portfolios(
        self, user_id: int
    ) -> List[tuple[Portfolio, int]]:
        """
        Get all portfolios for a user with holdings count.

        Returns:
            List of (portfolio, holdings_count) tuples
        """
        query = (
            select(
                Portfolio,
                func.count(Holding.id).label("holdings_count"),
            )
            .outerjoin(Holding, Holding.portfolio_id == Portfolio.id)
            .where(Portfolio.user_id == user_id)
            .group_by(Portfolio.id)
            .order_by(Portfolio.is_default.desc(), Portfolio.created_at)
        )

        result = await self.db.execute(query)
        return result.all()

    async def get_portfolio_by_id(
        self,
        portfolio_id: str,
        user_id: int,
        load_holdings: bool = False,
        load_transactions: bool = False,
    ) -> Optional[Portfolio]:
        """Get a portfolio by ID, ensuring it belongs to the user."""
        query = select(Portfolio).where(
            and_(
                Portfolio.id == portfolio_id,
                Portfolio.user_id == user_id,
            )
        )

        if load_holdings:
            query = query.options(selectinload(Portfolio.holdings))
        if load_transactions:
            query = query.options(selectinload(Portfolio.transactions))

        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def create_portfolio(
        self,
        user_id: int,
        name: str,
        description: Optional[str] = None,
        currency: str = "USD",
    ) -> Portfolio:
        """Create a new portfolio."""
        # Check if this is the user's first portfolio
        count_query = select(func.count(Portfolio.id)).where(
            Portfolio.user_id == user_id
        )
        result = await self.db.execute(count_query)
        existing_count = result.scalar()

        portfolio = Portfolio(
            user_id=user_id,
            name=name,
            description=description,
            currency=currency,
            is_default=existing_count == 0,
        )

        self.db.add(portfolio)
        await self.db.commit()
        await self.db.refresh(portfolio)

        logger.info(f"Created portfolio {portfolio.id} for user {user_id}")
        return portfolio

    async def update_portfolio(
        self,
        portfolio: Portfolio,
        name: Optional[str] = None,
        description: Optional[str] = None,
        currency: Optional[str] = None,
    ) -> Portfolio:
        """Update portfolio details."""
        if name is not None:
            portfolio.name = name
        if description is not None:
            portfolio.description = description
        if currency is not None:
            portfolio.currency = currency

        await self.db.commit()
        await self.db.refresh(portfolio)

        logger.info(f"Updated portfolio {portfolio.id}")
        return portfolio

    async def delete_portfolio(self, portfolio: Portfolio) -> None:
        """Delete a portfolio and all related data."""
        portfolio_id = portfolio.id
        user_id = portfolio.user_id

        # If deleting default portfolio, make another one default
        if portfolio.is_default:
            other_query = (
                select(Portfolio)
                .where(
                    and_(
                        Portfolio.user_id == user_id,
                        Portfolio.id != portfolio_id,
                    )
                )
                .limit(1)
            )
            result = await self.db.execute(other_query)
            other_portfolio = result.scalar_one_or_none()
            if other_portfolio:
                other_portfolio.is_default = True

        await self.db.delete(portfolio)
        await self.db.commit()

        logger.info(f"Deleted portfolio {portfolio_id}")

    async def set_default_portfolio(
        self, portfolio: Portfolio, user_id: int
    ) -> Portfolio:
        """Set a portfolio as the user's default."""
        if portfolio.is_default:
            return portfolio

        # Clear default from other portfolios
        clear_query = select(Portfolio).where(
            and_(
                Portfolio.user_id == user_id,
                Portfolio.is_default == True,
            )
        )
        result = await self.db.execute(clear_query)
        old_default = result.scalar_one_or_none()
        if old_default:
            old_default.is_default = False

        # Set new default
        portfolio.is_default = True

        await self.db.commit()
        await self.db.refresh(portfolio)

        logger.info(f"Set portfolio {portfolio.id} as default for user {user_id}")
        return portfolio

    # ============== Holding Operations ==============

    async def get_holding(
        self, portfolio_id: str, symbol: str
    ) -> Optional[Holding]:
        """Get a specific holding by portfolio and symbol."""
        query = select(Holding).where(
            and_(
                Holding.portfolio_id == portfolio_id,
                Holding.symbol == symbol.upper(),
            )
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_holdings_with_quotes(
        self, portfolio_id: str
    ) -> List[HoldingWithQuote]:
        """
        Get all holdings for a portfolio with live price data.

        Returns:
            List of HoldingWithQuote objects
        """
        query = select(Holding).where(Holding.portfolio_id == portfolio_id)
        result = await self.db.execute(query)
        holdings = result.scalars().all()

        if not holdings:
            return []

        # Fetch quotes for all symbols
        symbols = [h.symbol for h in holdings]
        stock_service = await get_stock_service()
        quotes = await stock_service.get_batch_quotes(symbols)

        # Build holdings with quotes
        holdings_with_quotes = []
        for holding in holdings:
            quote = quotes.get(holding.symbol)

            # Calculate market value and profit/loss
            current_price = quote.get("price") if quote else None
            market_value = None
            profit_loss = None
            profit_loss_percent = None

            if current_price is not None and holding.quantity > 0:
                market_value = Decimal(str(current_price)) * holding.quantity
                market_value = market_value.quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                profit_loss = market_value - holding.total_cost
                if holding.total_cost > 0:
                    profit_loss_percent = float(
                        (profit_loss / holding.total_cost) * 100
                    )

            holding_dict = {
                "id": holding.id,
                "portfolio_id": holding.portfolio_id,
                "symbol": holding.symbol,
                "quantity": holding.quantity,
                "average_cost": holding.average_cost,
                "total_cost": holding.total_cost,
                "created_at": holding.created_at,
                "updated_at": holding.updated_at,
                "name": quote.get("name") if quote else None,
                "current_price": current_price,
                "market_value": market_value,
                "profit_loss": profit_loss,
                "profit_loss_percent": (
                    round(profit_loss_percent, 2) if profit_loss_percent else None
                ),
                "day_change": quote.get("change") if quote else None,
                "day_change_percent": quote.get("change_percent") if quote else None,
            }
            holdings_with_quotes.append(HoldingWithQuote(**holding_dict))

        return holdings_with_quotes

    async def get_holding_detail(
        self, portfolio_id: str, symbol: str
    ) -> Optional[HoldingWithQuote]:
        """Get detailed holding information with quotes."""
        holding = await self.get_holding(portfolio_id, symbol)
        if not holding:
            return None

        stock_service = await get_stock_service()
        quote = await stock_service.get_quote(symbol)

        # Calculate market value and profit/loss
        current_price = quote.get("price") if quote else None
        market_value = None
        profit_loss = None
        profit_loss_percent = None

        if current_price is not None and holding.quantity > 0:
            market_value = Decimal(str(current_price)) * holding.quantity
            market_value = market_value.quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            profit_loss = market_value - holding.total_cost
            if holding.total_cost > 0:
                profit_loss_percent = float(
                    (profit_loss / holding.total_cost) * 100
                )

        return HoldingWithQuote(
            id=holding.id,
            portfolio_id=holding.portfolio_id,
            symbol=holding.symbol,
            quantity=holding.quantity,
            average_cost=holding.average_cost,
            total_cost=holding.total_cost,
            created_at=holding.created_at,
            updated_at=holding.updated_at,
            name=quote.get("name") if quote else None,
            current_price=current_price,
            market_value=market_value,
            profit_loss=profit_loss,
            profit_loss_percent=(
                round(profit_loss_percent, 2) if profit_loss_percent else None
            ),
            day_change=quote.get("change") if quote else None,
            day_change_percent=quote.get("change_percent") if quote else None,
        )

    # ============== Transaction Operations ==============

    async def create_transaction(
        self,
        portfolio_id: str,
        data: TransactionCreate,
    ) -> Transaction:
        """
        Create a transaction and update holdings accordingly.

        For BUY: Add to quantity, recalculate average cost
        For SELL: Reduce quantity, keep average cost unchanged
        For DIVIDEND: No change to holdings (just record the transaction)
        """
        symbol = data.symbol.upper()

        # Calculate total based on transaction type
        if data.type == TransactionType.BUY:
            total = (data.quantity * data.price) + data.fee
        elif data.type == TransactionType.SELL:
            total = (data.quantity * data.price) - data.fee
        else:  # DIVIDEND
            total = data.quantity * data.price  # dividend amount

        total = total.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

        # Create transaction record
        transaction = Transaction(
            portfolio_id=portfolio_id,
            symbol=symbol,
            type=data.type.value,
            quantity=data.quantity,
            price=data.price,
            fee=data.fee,
            total=total,
            date=data.date,
            notes=data.notes,
        )

        self.db.add(transaction)

        # Update holdings for BUY/SELL
        if data.type in (TransactionType.BUY, TransactionType.SELL):
            await self._update_holding_on_transaction(
                portfolio_id, symbol, data.type, data.quantity, data.price
            )

        await self.db.commit()
        await self.db.refresh(transaction)

        logger.info(
            f"Created {data.type.value} transaction for {symbol} "
            f"in portfolio {portfolio_id}"
        )
        return transaction

    async def _update_holding_on_transaction(
        self,
        portfolio_id: str,
        symbol: str,
        txn_type: TransactionType,
        quantity: Decimal,
        price: Decimal,
    ) -> None:
        """Update holding based on transaction."""
        holding = await self.get_holding(portfolio_id, symbol)

        if txn_type == TransactionType.BUY:
            if holding is None:
                # Create new holding
                holding = Holding(
                    portfolio_id=portfolio_id,
                    symbol=symbol,
                    quantity=quantity,
                    average_cost=price,
                    total_cost=quantity * price,
                )
                self.db.add(holding)
            else:
                # Update existing holding with new average cost
                # Average cost = (old_total_cost + new_purchase) / (old_qty + new_qty)
                new_total_cost = holding.total_cost + (quantity * price)
                new_quantity = holding.quantity + quantity
                new_average_cost = new_total_cost / new_quantity

                holding.quantity = new_quantity
                holding.average_cost = new_average_cost.quantize(
                    Decimal("0.00000001"), rounding=ROUND_HALF_UP
                )
                holding.total_cost = new_total_cost.quantize(
                    Decimal("0.0001"), rounding=ROUND_HALF_UP
                )

        elif txn_type == TransactionType.SELL:
            if holding is None or holding.quantity < quantity:
                raise ValueError(
                    f"Insufficient quantity for {symbol}. "
                    f"Available: {holding.quantity if holding else 0}, "
                    f"Requested: {quantity}"
                )

            # Reduce quantity, keep average cost unchanged
            new_quantity = holding.quantity - quantity
            if new_quantity <= 0:
                # Remove holding entirely
                await self.db.delete(holding)
            else:
                holding.quantity = new_quantity
                # Adjust total_cost proportionally
                holding.total_cost = (new_quantity * holding.average_cost).quantize(
                    Decimal("0.0001"), rounding=ROUND_HALF_UP
                )

    async def get_transactions(
        self,
        portfolio_id: str,
        page: int = 1,
        page_size: int = 20,
        symbol: Optional[str] = None,
        txn_type: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> tuple[List[Transaction], int]:
        """
        Get transactions with pagination and filters.

        Returns:
            Tuple of (transactions, total_count)
        """
        # Build base query
        query = select(Transaction).where(Transaction.portfolio_id == portfolio_id)

        # Apply filters
        if symbol:
            query = query.where(Transaction.symbol == symbol.upper())
        if txn_type:
            query = query.where(Transaction.type == txn_type)
        if start_date:
            query = query.where(Transaction.date >= start_date)
        if end_date:
            query = query.where(Transaction.date <= end_date)

        # Get total count
        from sqlalchemy import func as sql_func

        count_query = select(sql_func.count()).select_from(query.subquery())
        count_result = await self.db.execute(count_query)
        total = count_result.scalar()

        # Apply pagination and ordering
        offset = (page - 1) * page_size
        query = query.order_by(Transaction.date.desc()).offset(offset).limit(page_size)

        result = await self.db.execute(query)
        transactions = result.scalars().all()

        return list(transactions), total

    async def get_transaction_by_id(
        self, portfolio_id: str, transaction_id: str
    ) -> Optional[Transaction]:
        """Get a specific transaction."""
        query = select(Transaction).where(
            and_(
                Transaction.id == transaction_id,
                Transaction.portfolio_id == portfolio_id,
            )
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def delete_transaction(
        self, transaction: Transaction
    ) -> None:
        """
        Delete a transaction and recalculate holdings.

        This reverses the effect of the transaction:
        - For BUY: Reduce quantity
        - For SELL: Increase quantity
        """
        portfolio_id = transaction.portfolio_id
        symbol = transaction.symbol

        await self.db.delete(transaction)

        # Recalculate holdings from remaining transactions
        await self._recalculate_holding(portfolio_id, symbol)

        await self.db.commit()
        logger.info(f"Deleted transaction {transaction.id}")

    async def _recalculate_holding(
        self, portfolio_id: str, symbol: str
    ) -> None:
        """Recalculate holding from all transactions."""
        # Get all transactions for this symbol
        query = (
            select(Transaction)
            .where(
                and_(
                    Transaction.portfolio_id == portfolio_id,
                    Transaction.symbol == symbol,
                )
            )
            .order_by(Transaction.date)
        )
        result = await self.db.execute(query)
        transactions = result.scalars().all()

        # Delete existing holding
        delete_query = delete(Holding).where(
            and_(
                Holding.portfolio_id == portfolio_id,
                Holding.symbol == symbol,
            )
        )
        await self.db.execute(delete_query)

        if not transactions:
            return

        # Recalculate from scratch
        quantity = Decimal("0")
        total_cost = Decimal("0")

        for txn in transactions:
            if txn.type == TransactionType.BUY.value:
                new_cost = Decimal(str(txn.quantity)) * Decimal(str(txn.price))
                total_cost += new_cost
                quantity += Decimal(str(txn.quantity))
            elif txn.type == TransactionType.SELL.value:
                if quantity > 0:
                    # Reduce total_cost proportionally
                    sell_qty = Decimal(str(txn.quantity))
                    cost_per_share = total_cost / quantity if quantity > 0 else 0
                    total_cost -= sell_qty * cost_per_share
                    quantity -= sell_qty

        if quantity > 0:
            average_cost = total_cost / quantity
            holding = Holding(
                portfolio_id=portfolio_id,
                symbol=symbol,
                quantity=quantity.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP),
                average_cost=average_cost.quantize(
                    Decimal("0.00000001"), rounding=ROUND_HALF_UP
                ),
                total_cost=total_cost.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
            )
            self.db.add(holding)

    # ============== Portfolio Summary ==============

    async def get_portfolio_summary(
        self, portfolio: Portfolio
    ) -> PortfolioSummary:
        """
        Calculate portfolio summary with live prices.

        This is the real-time calculation strategy.
        """
        holdings_with_quotes = await self.get_holdings_with_quotes(portfolio.id)

        total_cost = Decimal("0")
        total_market_value = Decimal("0")
        total_day_change = Decimal("0")
        has_prices = False

        best_performer: Optional[HoldingWithQuote] = None
        worst_performer: Optional[HoldingWithQuote] = None

        for holding in holdings_with_quotes:
            total_cost += holding.total_cost

            if holding.market_value is not None:
                has_prices = True
                total_market_value += holding.market_value

                if holding.day_change is not None and holding.quantity > 0:
                    # Day change in dollar terms
                    day_change_amount = Decimal(str(holding.day_change)) * holding.quantity
                    total_day_change += day_change_amount

                # Track best/worst performers by profit/loss percentage
                if holding.profit_loss_percent is not None:
                    if (
                        best_performer is None
                        or (
                            holding.profit_loss_percent
                            > (best_performer.profit_loss_percent or float("-inf"))
                        )
                    ):
                        best_performer = holding
                    if (
                        worst_performer is None
                        or (
                            holding.profit_loss_percent
                            < (worst_performer.profit_loss_percent or float("inf"))
                        )
                    ):
                        worst_performer = holding

        # Calculate totals
        total_profit_loss = None
        total_profit_loss_percent = None
        day_change_percent = None

        if has_prices:
            total_profit_loss = total_market_value - total_cost
            if total_cost > 0:
                total_profit_loss_percent = float(
                    (total_profit_loss / total_cost) * 100
                )

            # Day change as percentage of previous day's market value
            prev_market_value = total_market_value - total_day_change
            if prev_market_value > 0:
                day_change_percent = float(
                    (total_day_change / prev_market_value) * 100
                )

        return PortfolioSummary(
            portfolio_id=portfolio.id,
            portfolio_name=portfolio.name,
            currency=portfolio.currency,
            total_cost=total_cost,
            total_market_value=total_market_value if has_prices else None,
            total_profit_loss=total_profit_loss,
            total_profit_loss_percent=(
                round(total_profit_loss_percent, 2)
                if total_profit_loss_percent is not None
                else None
            ),
            day_change=total_day_change if has_prices else None,
            day_change_percent=(
                round(day_change_percent, 2) if day_change_percent is not None else None
            ),
            holdings_count=len(holdings_with_quotes),
            best_performer=best_performer,
            worst_performer=worst_performer,
        )


# Import datetime for type hints
from datetime import datetime

# For TransactionType
TransactionType = TransactionType
