import os

# Configure the app for hermetic tests before anything imports settings.
os.environ.update(
    ENVIRONMENT="test",
    DATABASE_URL="sqlite://",
    QDRANT_URL=":memory:",
    LLM_PROVIDER="offline",
    LLM_API_KEY="",
    EMBEDDING_PROVIDER="hash",
    STT_PROVIDER="browser",
    RATE_LIMIT_PER_MINUTE="100000",
    AUTH_RATE_LIMIT_PER_MINUTE="100000",
    TURN_ENDPOINT_MS="250",
    LLM_JUDGE_ENABLED="false",
)

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.agents.llm import set_llm  # noqa: E402
from app.core.ratelimit import rate_limiter  # noqa: E402
from app.database.models import Base  # noqa: E402
from app.database.session import get_engine, init_engine, session_factory  # noqa: E402
from app.rag import keyword  # noqa: E402
from app.rag.vectorstore import reset_vector_store  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "app" / "evaluation" / "fixtures"
SAMPLE_RESUME = (FIXTURES / "sample_resume.txt").read_bytes()
CSRF = {"x-interviewos-csrf": "1"}


@pytest.fixture(autouse=True)
def _fresh_state():
    init_engine("sqlite://")
    Base.metadata.drop_all(get_engine())
    Base.metadata.create_all(get_engine())
    reset_vector_store(None)
    keyword._cache.clear()
    rate_limiter.reset()
    set_llm(None)
    yield
    set_llm(None)


@pytest.fixture
def db():
    s = session_factory()()
    yield s
    s.close()


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        c.headers.update(CSRF)
        yield c


def register(client, email="candidate@example.com", password="Passw0rd!x"):
    r = client.post("/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.json()


def upload_resume(client, data: bytes = SAMPLE_RESUME, name="resume.txt"):
    r = client.post("/resume/upload", files={"file": (name, data, "text/plain")})
    assert r.status_code == 201, r.text
    return r.json()


def confirm_all(client):
    r = client.patch("/resume", json={"operations": [{"action": "confirm_all"}]})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def onboarded(client):
    """Registered user with the sample resume uploaded and verified."""
    user = register(client)
    upload_resume(client)
    profile = confirm_all(client)["profile"]
    return {"user": user, "profile": profile}
