from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from app.config.settings import settings
from app.db import init_supabase
from app.services.vector_store import ensure_collection
from app.services.memory_store import ensure_collections as ensure_memory_collections
from app.routes import health, admin, session, chat, live, proctoring, onboarding, invitations
from app.routes import upload as upload_router
from app.routes.invitations import public_router as invitations_public
from app.routes.admin import require_admin, public_router as pipelines_public


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_supabase()
    try:
        await ensure_collection()
        await ensure_memory_collections()
    except Exception as e:
        print(f"WARNING: Qdrant not reachable at startup: {e}")
        print("Memory store and CV chunks will be unavailable until Qdrant is fixed.")
    yield


app = FastAPI(
    title="Masar API",
    description="AI Adaptive Assessment Platform",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.allowed_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["health"])
app.include_router(admin.router, prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])
app.include_router(pipelines_public, prefix="/pipelines", tags=["pipelines-public"])
app.include_router(session.router, prefix="/session", tags=["session"])
app.include_router(chat.router, prefix="/chat", tags=["chat"])
app.include_router(live.router, prefix="/live", tags=["live"])
app.include_router(proctoring.router, prefix="/proctor", tags=["proctoring"])
app.include_router(upload_router.router, prefix="/upload", tags=["upload"])
app.include_router(onboarding.router, prefix="/onboarding", tags=["onboarding"])
app.include_router(invitations.router, prefix="/admin/invitations", tags=["invitations"], dependencies=[Depends(require_admin)])
app.include_router(invitations_public, prefix="/invitations", tags=["invitations-public"])
