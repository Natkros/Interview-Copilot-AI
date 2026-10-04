"""Technology taxonomy used for skill extraction, JD parsing, classification
and grounding (technology names are high-value entities for claim checks)."""

from __future__ import annotations

import re
from functools import lru_cache

# canonical name -> (category, [aliases])
_TAXONOMY: dict[str, tuple[str, list[str]]] = {
    # programming languages
    "Python": ("programming_language", ["python3"]),
    "Java": ("programming_language", []),
    "JavaScript": ("programming_language", ["js", "ecmascript"]),
    "TypeScript": ("programming_language", ["ts"]),
    "C": ("programming_language", []),
    "C++": ("programming_language", ["cpp"]),
    "C#": ("programming_language", ["csharp"]),
    "Go": ("programming_language", ["golang"]),
    "Rust": ("programming_language", []),
    "Kotlin": ("programming_language", []),
    "Swift": ("programming_language", []),
    "Ruby": ("programming_language", []),
    "PHP": ("programming_language", []),
    "Scala": ("programming_language", []),
    "R": ("programming_language", []),
    "MATLAB": ("programming_language", []),
    "SQL": ("programming_language", []),
    "Bash": ("programming_language", ["shell scripting", "shell"]),
    "HTML": ("programming_language", ["html5"]),
    "CSS": ("programming_language", ["css3"]),
    "Dart": ("programming_language", []),
    "Solidity": ("programming_language", []),
    # frameworks
    "Flask": ("framework", []),
    "Django": ("framework", []),
    "FastAPI": ("framework", ["fast api"]),
    "Spring Boot": ("framework", ["spring", "springboot"]),
    "Express": ("framework", ["express.js", "expressjs"]),
    "Node.js": ("framework", ["node", "nodejs"]),
    "React": ("framework", ["react.js", "reactjs"]),
    "Next.js": ("framework", ["nextjs", "next"]),
    "Angular": ("framework", []),
    "Vue": ("framework", ["vue.js", "vuejs"]),
    "Svelte": ("framework", []),
    "Tailwind CSS": ("framework", ["tailwind", "tailwindcss"]),
    "Flutter": ("framework", []),
    "React Native": ("framework", []),
    ".NET": ("framework", ["dotnet", "asp.net"]),
    "Streamlit": ("framework", []),
    "Gradio": ("framework", []),
    "Ruby on Rails": ("framework", ["rails"]),
    "Laravel": ("framework", []),
    # ML / AI
    "Machine Learning": ("ai_ml", ["ml"]),
    "Deep Learning": ("ai_ml", ["dl"]),
    "Natural Language Processing": ("ai_ml", ["nlp"]),
    "Computer Vision": ("ai_ml", ["cv"]),
    "Generative AI": ("ai_ml", ["genai", "gen ai"]),
    "Large Language Models": ("ai_ml", ["llm", "llms", "large language model"]),
    "RAG": ("ai_ml", ["retrieval-augmented generation", "retrieval augmented generation"]),
    "Prompt Engineering": ("ai_ml", []),
    "Fine-tuning": ("ai_ml", ["fine tuning", "finetuning", "lora", "qlora"]),
    "Transformers": ("ai_ml", ["transformer"]),
    "Embeddings": ("ai_ml", ["embedding", "vector embeddings"]),
    "Reinforcement Learning": ("ai_ml", []),
    "Multimodal": ("ai_ml", ["multi-modal"]),
    "AI Agents": ("ai_ml", ["agentic ai", "multi-agent", "agents"]),
    "TensorFlow": ("library", ["tf"]),
    "PyTorch": ("library", ["torch"]),
    "Keras": ("library", []),
    "scikit-learn": ("library", ["sklearn", "scikit learn"]),
    "XGBoost": ("library", []),
    "LightGBM": ("library", []),
    "Hugging Face": ("library", ["huggingface", "hf transformers"]),
    "LangChain": ("library", []),
    "LlamaIndex": ("library", ["llama index", "llama-index"]),
    "LangGraph": ("library", []),
    "OpenCV": ("library", []),
    "NumPy": ("library", ["numpy"]),
    "Pandas": ("library", []),
    "Matplotlib": ("library", []),
    "Seaborn": ("library", []),
    "spaCy": ("library", ["spacy"]),
    "NLTK": ("library", []),
    "sentence-transformers": ("library", ["sentence transformers", "sbert"]),
    "OpenAI API": ("library", ["openai", "gpt-4", "gpt"]),
    "Claude API": ("library", ["anthropic", "claude"]),
    "CLIP": ("library", []),
    "Whisper": ("library", []),
    "YOLO": ("library", []),
    "Pydantic": ("library", []),
    "SQLAlchemy": ("library", []),
    "Celery": ("library", []),
    "Redux": ("library", []),
    "Jest": ("library", []),
    "pytest": ("library", []),
    # databases / vector stores
    "PostgreSQL": ("database", ["postgres", "psql"]),
    "MySQL": ("database", []),
    "SQLite": ("database", []),
    "MongoDB": ("database", ["mongo"]),
    "Redis": ("database", []),
    "Cassandra": ("database", []),
    "DynamoDB": ("database", []),
    "Elasticsearch": ("database", ["elastic search", "opensearch"]),
    "Firebase": ("database", ["firestore"]),
    "Oracle": ("database", []),
    "Neo4j": ("database", []),
    "Qdrant": ("database", []),
    "Pinecone": ("database", []),
    "Weaviate": ("database", []),
    "Milvus": ("database", []),
    "Chroma": ("database", ["chromadb"]),
    "FAISS": ("database", ["faiss"]),
    "pgvector": ("database", []),
    "Supabase": ("database", []),
    # cloud
    "AWS": ("cloud", ["amazon web services"]),
    "EC2": ("cloud", ["aws ec2"]),
    "S3": ("cloud", ["aws s3"]),
    "AWS Lambda": ("cloud", ["lambda"]),
    "SageMaker": ("cloud", ["aws sagemaker"]),
    "Azure": ("cloud", ["microsoft azure"]),
    "GCP": ("cloud", ["google cloud", "google cloud platform"]),
    "Vercel": ("cloud", []),
    "Heroku": ("cloud", []),
    # devops / tools
    "Docker": ("devops", []),
    "Kubernetes": ("devops", ["k8s"]),
    "Terraform": ("devops", []),
    "Jenkins": ("devops", []),
    "GitHub Actions": ("devops", []),
    "CI/CD": ("devops", ["ci cd", "continuous integration"]),
    "Linux": ("devops", ["unix"]),
    "Nginx": ("devops", []),
    "Kafka": ("devops", ["apache kafka"]),
    "RabbitMQ": ("devops", []),
    "Spark": ("devops", ["apache spark", "pyspark"]),
    "Airflow": ("devops", ["apache airflow"]),
    "MLflow": ("devops", []),
    "Prometheus": ("devops", []),
    "Grafana": ("devops", []),
    "Git": ("tool", []),
    "GitHub": ("tool", []),
    "Jira": ("tool", []),
    "Postman": ("tool", []),
    "Figma": ("tool", []),
    "Tableau": ("tool", []),
    "Power BI": ("tool", ["powerbi"]),
    "Excel": ("tool", []),
    "Jupyter": ("tool", ["jupyter notebook"]),
    # concepts
    "REST APIs": ("concept", ["rest", "restful", "rest api", "restful apis"]),
    "GraphQL": ("concept", []),
    "WebSockets": ("concept", ["websocket"]),
    "gRPC": ("concept", []),
    "Microservices": ("concept", []),
    "System Design": ("concept", []),
    "Data Structures": ("concept", ["dsa", "data structures and algorithms"]),
    "Algorithms": ("concept", []),
    "OOP": ("concept", ["object-oriented programming", "object oriented programming"]),
    "Operating Systems": ("concept", []),
    "Computer Networks": ("concept", ["networking"]),
    "DBMS": ("concept", ["database management systems"]),
    "Agile": ("concept", ["scrum"]),
    "Unit Testing": ("concept", ["testing", "tdd"]),
    "OAuth": ("concept", ["oauth2", "jwt"]),
    "Data Analysis": ("concept", ["data analytics"]),
    "ETL": ("concept", []),
    "Vector Search": ("concept", ["semantic search", "vector database", "vector db", "similarity search"]),
    "OCR": ("concept", ["tesseract"]),
    "Speech Recognition": ("concept", ["speech-to-text", "stt", "asr"]),
}

# Ambiguous short aliases that must appear with original casing to count.
_CASE_SENSITIVE = {"R", "C", "Go", "ts", "js", "ml", "dl", "cv", "tf", "next", "node", "spring", "lambda",
                   "rest", "shell", "testing", "agents", "jwt", "gpt", "claude", "networking", "embedding"}

CATEGORY_LABELS = {
    "programming_language": "Programming Languages",
    "framework": "Frameworks",
    "library": "Libraries",
    "database": "Databases",
    "cloud": "Cloud",
    "ai_ml": "AI/ML",
    "devops": "DevOps",
    "tool": "Tools",
    "concept": "Concepts",
    "other": "Other",
}


@lru_cache
def _patterns() -> list[tuple[re.Pattern[str], str]]:
    pats: list[tuple[re.Pattern[str], str]] = []
    for canonical, (_cat, aliases) in _TAXONOMY.items():
        for term in [canonical, *aliases]:
            escaped = re.escape(term)
            pattern = rf"(?<![\w+#.-]){escaped}(?![\w+#]|\.\w)"
            if term in _CASE_SENSITIVE or (len(term) <= 2):
                # short/ambiguous: exact case only, and for single letters require
                # list-like context (comma/slash/colon separated) to count
                if len(term) == 1:
                    pattern = rf"(?:(?<=[,:/|(])|(?<=^)|(?<=, )|(?<=: )){escaped}(?=\s*(?:[,/|)]|$))"
                pats.append((re.compile(pattern, re.M), canonical))
            else:
                pats.append((re.compile(pattern, re.I), canonical))
    # longest terms first so "Spring Boot" wins over "Spring"
    pats.sort(key=lambda p: -len(p[0].pattern))
    return pats


def find_technologies(text: str) -> list[str]:
    """Canonical technology names mentioned in `text`, in order of first appearance."""
    found: dict[str, int] = {}
    for pattern, canonical in _patterns():
        m = pattern.search(text)
        if m and canonical not in found:
            found[canonical] = m.start()
    return [k for k, _ in sorted(found.items(), key=lambda kv: kv[1])]


def category_of(name: str) -> str:
    canon = canonicalise(name)
    if canon in _TAXONOMY:
        return _TAXONOMY[canon][0]
    return "other"


@lru_cache
def _alias_index() -> dict[str, str]:
    idx: dict[str, str] = {}
    for canonical, (_cat, aliases) in _TAXONOMY.items():
        idx[canonical.lower()] = canonical
        for a in aliases:
            idx.setdefault(a.lower(), canonical)
    return idx


def canonicalise(name: str) -> str:
    return _alias_index().get(name.strip().lower(), name.strip())


def all_terms_lower() -> set[str]:
    return set(_alias_index().keys())
