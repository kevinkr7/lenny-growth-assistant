"""Immutable initial schema snapshot; never import mutable application models here."""

from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = (
    """CREATE TABLE sources (
	id UUID NOT NULL,
	source_url TEXT NOT NULL,
	repository TEXT NOT NULL,
	path TEXT NOT NULL,
	revision VARCHAR(64) NOT NULL,
	episode_title TEXT NOT NULL,
	guest TEXT NOT NULL,
	publish_date VARCHAR(30),
	description TEXT NOT NULL,
	content_hash VARCHAR(64) NOT NULL,
	metadata JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	ingested_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (path)
)""",
    """CREATE TABLE users (
	id UUID NOT NULL,
	metadata JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id)
)""",
    """CREATE TABLE chunks (
	id UUID NOT NULL,
	source_id UUID NOT NULL,
	chunk_index INTEGER NOT NULL,
	content TEXT NOT NULL,
	embedding VECTOR(384) NOT NULL,
	search_vector TSVECTOR,
	content_hash VARCHAR(64) NOT NULL,
	metadata JSONB NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (source_id, chunk_index),
	FOREIGN KEY(source_id) REFERENCES sources (id) ON DELETE CASCADE
)""",
    """CREATE TABLE sessions (
	id UUID NOT NULL,
	user_id UUID NOT NULL,
	title VARCHAR(120) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (id)
)""",
    """CREATE TABLE messages (
	id UUID NOT NULL,
	session_id UUID NOT NULL,
	role VARCHAR(20) NOT NULL,
	content TEXT NOT NULL,
	provider VARCHAR(30),
	model VARCHAR(120),
	metadata JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE
)""",
    """CREATE TABLE artifacts (
	id UUID NOT NULL,
	session_id UUID NOT NULL,
	message_id UUID NOT NULL,
	type VARCHAR(20) NOT NULL,
	title VARCHAR(200) NOT NULL,
	content TEXT NOT NULL,
	source_ids JSONB NOT NULL,
	evidence JSONB NOT NULL,
	word_count INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE,
	FOREIGN KEY(message_id) REFERENCES messages (id) ON DELETE CASCADE
)""",
    """CREATE INDEX ix_chunks_source_id ON chunks (source_id)""",
    """CREATE INDEX ix_sessions_updated_at ON sessions (updated_at)""",
    """CREATE INDEX ix_messages_session_id ON messages (session_id)""",
    """CREATE INDEX ix_artifacts_session_id ON artifacts (session_id)""",
    """CREATE INDEX chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)""",
    """CREATE INDEX chunks_search_gin ON chunks USING gin (search_vector)""",
    """CREATE FUNCTION chunks_search_update() RETURNS trigger AS $$ BEGIN NEW.search_vector := to_tsvector('english', NEW.content); RETURN NEW; END $$ LANGUAGE plpgsql""",
    """CREATE TRIGGER chunks_search_trigger BEFORE INSERT OR UPDATE OF content ON chunks FOR EACH ROW EXECUTE FUNCTION chunks_search_update()""",
)


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    for statement in SCHEMA:
        op.execute(statement)


def downgrade():
    op.execute("DROP TABLE artifacts")
    op.execute("DROP TABLE messages")
    op.execute("DROP TABLE sessions")
    op.execute("DROP TABLE chunks")
    op.execute("DROP TABLE users")
    op.execute("DROP TABLE sources")
    op.execute("DROP FUNCTION IF EXISTS chunks_search_update()")
