from alembic import context

from app.core.config import Settings
from app.core.database import Base, UTCDateTime, build_engine
from app.models import (  # noqa: F401
    CandidateEvent,
    EditSegment,
    NarrationAlignment,
    NarrationAsset,
    NarrationReview,
    RenderAsset,
    RenderReview,
    ResearchClaim,
    ResearchClaimEvidence,
    ResearchContradiction,
    ResearchDossier,
    ResearchSource,
    RightsRecord,
    ScoreEvaluation,
    ScriptBeat,
    ScriptDraft,
    ScriptReview,
    ScriptSentence,
    ScriptSentenceClaim,
    ScriptSentenceVisualRef,
    SearchCache,
    VideoCandidate,
    VideoEditPlan,
    VoiceProfile,
)

config = context.config
target_metadata = Base.metadata


def render_type(item_type, item, autogen_context):
    if item_type == "type" and isinstance(item, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False


def migrate(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
        render_item=render_type,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(
        url=Settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
elif config.attributes.get("connection") is not None:
    migrate(config.attributes["connection"])
else:
    engine = build_engine(Settings().database_url)
    with engine.connect() as connection:
        migrate(connection)
    engine.dispose()
