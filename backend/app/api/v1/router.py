"""API v1 router aggregation."""

from fastapi import APIRouter

from app.api.v1.admin import router as admin_router
from app.api.v1.alerts import router as alerts_router
from app.api.v1.alerts import push_router
from app.api.v1.analysis import router as analysis_router
from app.api.v1.chat import router as chat_router
from app.api.v1.auth import router as auth_router
from app.api.v1.health import router as health_router
from app.api.v1.news import router as news_router
from app.api.v1.portfolio import router as portfolio_router
from app.api.v1.reports import router as reports_router
from app.api.v1.settings import router as settings_router
from app.api.v1.stocks import router as stocks_router
from app.api.v1.transactions import router as transactions_router
from app.api.v1.watchlist import router as watchlist_router

api_router = APIRouter(prefix="/api/v1")

# Include all routers
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(stocks_router)
api_router.include_router(watchlist_router)
api_router.include_router(analysis_router)
api_router.include_router(chat_router)
api_router.include_router(news_router)
api_router.include_router(portfolio_router)
api_router.include_router(transactions_router)
api_router.include_router(alerts_router)
api_router.include_router(push_router)
api_router.include_router(reports_router)
api_router.include_router(settings_router)
api_router.include_router(admin_router)
