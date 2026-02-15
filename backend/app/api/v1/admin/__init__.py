"""Admin API endpoints package."""

from fastapi import APIRouter, Depends

from app.core.security import require_admin

from app.api.v1.admin.users import router as users_router
from app.api.v1.admin.settings import router as settings_router
from app.api.v1.admin.news_pipeline import router as news_pipeline_router
from app.api.v1.admin.rss_feeds import router as rss_feeds_router
from app.api.v1.admin.llm_costs import router as llm_costs_router

router = APIRouter(prefix="/admin", tags=["Admin"], dependencies=[Depends(require_admin)])

router.include_router(users_router)
router.include_router(settings_router)
router.include_router(news_pipeline_router)
router.include_router(rss_feeds_router)
router.include_router(llm_costs_router)
