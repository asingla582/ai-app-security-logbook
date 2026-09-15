import os

from dotenv import load_dotenv

load_dotenv()

# Defaults are the Supabase CLI's well-known local values; the hosted project overrides via .env.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "http://127.0.0.1:54321")
SUPABASE_JWT_SECRET = os.environ.get(
    "SUPABASE_JWT_SECRET",
    "super-secret-jwt-token-with-at-least-32-characters-long",
)
SUPABASE_DB_URL = os.environ.get(
    "SUPABASE_DB_URL",
    "postgresql://postgres:postgres@127.0.0.1:54322/postgres",
)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
CHAT_MODEL = os.environ.get("CHAT_MODEL", "claude-opus-4-8")

# The browser origin allowed to call the API. Scoped, not a wildcard.
WEB_ORIGIN = os.environ.get("WEB_ORIGIN", "http://localhost:3000")

# Per-call cost bounds; per-user rate limiting is Week 7.
MAX_OUTPUT_TOKENS = int(os.environ.get("MAX_OUTPUT_TOKENS", "1024"))
MAX_INPUT_CHARS = int(os.environ.get("MAX_INPUT_CHARS", "8000"))
HISTORY_WINDOW = int(os.environ.get("HISTORY_WINDOW", "20"))

# Week 5: RAG. Embeddings are hosted (OpenAI); document text leaves the app at
# ingest, a third-party data-egress recorded in the threat model. Tests use a
# deterministic fake, so no key is needed there.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "1536"))  # must match migration 0005
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "1000"))
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", "150"))
RETRIEVAL_TOP_K = int(os.environ.get("RETRIEVAL_TOP_K", "5"))
MAX_DOCUMENT_CHARS = int(os.environ.get("MAX_DOCUMENT_CHARS", "200000"))
