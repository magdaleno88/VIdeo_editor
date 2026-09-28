from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine
from sqlalchemy.orm.exc import StaleDataError

from app.api.routes import router
from app.core.config import Settings
from app.core.database import build_engine, session_factory
from app.core.errors import ApplicationError
from app.core.logging import configure_logging
from app.core.schema import check_schema


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.log_level)
    database = engine if engine is not None else build_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        check_schema(database)
        try:
            yield
        finally:
            if engine is None:
                database.dispose()

    application = FastAPI(
        title="Industrial Content Factory",
        version="0.1.0",
        lifespan=lifespan,
        description=(
            "Local discovery, transparent visual scoring, source-backed research, "
            "verified scripts, "
            "approved narration, deterministic video assembly and human review."
        ),
    )
    application.state.settings = settings
    application.state.engine = database
    application.state.session_factory = session_factory(database)

    @application.exception_handler(ApplicationError)
    async def application_error(request: Request, exc: ApplicationError):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @application.exception_handler(StaleDataError)
    async def concurrent_update(request: Request, exc: StaleDataError):
        return JSONResponse(
            status_code=409, content={"detail": "Candidate changed concurrently; reload and retry"}
        )

    application.include_router(router)
    return application


app = create_app()
