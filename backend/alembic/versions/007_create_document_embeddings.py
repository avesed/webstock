"""Create document_embeddings table for RAG.

Creates the document_embeddings table with pgvector support for
vector similarity search and pg_trgm for keyword search.

Revision ID: 007_create_document_embeddings
Revises: 006_add_feature_toggles
Create Date: 2026-02-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = '007_create_document_embeddings'
down_revision: Union[str, None] = '006_add_feature_toggles'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create document_embeddings table with vector support."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Skip if table already exists
    if 'document_embeddings' in inspector.get_table_names():
        return

    # Ensure pgvector extension is available
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')
    # Ensure pg_trgm extension for text similarity
    op.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')

    # Create the table with raw SQL to properly handle vector type
    op.execute('''
        CREATE TABLE document_embeddings (
            id UUID PRIMARY KEY,
            source_type VARCHAR(50) NOT NULL,
            source_id VARCHAR(255) NOT NULL,
            symbol VARCHAR(20),
            chunk_text TEXT NOT NULL,
            chunk_index INTEGER NOT NULL DEFAULT 0,
            embedding vector(1536) NOT NULL,
            model VARCHAR(100) NOT NULL DEFAULT 'text-embedding-3-small',
            token_count INTEGER,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Create basic indexes
    op.execute('CREATE INDEX ix_document_embeddings_source_type ON document_embeddings(source_type)')
    op.execute('CREATE INDEX ix_document_embeddings_source_id ON document_embeddings(source_id)')
    op.execute('CREATE INDEX ix_document_embeddings_symbol ON document_embeddings(symbol)')

    # Create HNSW index for vector similarity search
    op.execute('''
        CREATE INDEX ix_document_embeddings_embedding_hnsw
        ON document_embeddings
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    ''')

    # Create composite index for source lookups
    op.create_index(
        'ix_document_embeddings_source',
        'document_embeddings',
        ['source_type', 'source_id'],
    )

    # Create GIN trigram index for text similarity search
    op.execute('''
        CREATE INDEX ix_document_embeddings_chunk_text_trgm
        ON document_embeddings
        USING gin (chunk_text gin_trgm_ops)
    ''')


def downgrade() -> None:
    """Drop document_embeddings table."""
    op.drop_table('document_embeddings')
