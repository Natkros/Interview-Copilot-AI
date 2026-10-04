"""Deterministic interviewer-utterance classifier.

Weighted pattern scoring over the utterance plus conversational context
(current focus project, known project names). Deterministic by design: it
runs on every utterance in the real-time path (sub-millisecond), is fully
unit-testable, and its accuracy is measured by the evaluation benchmark.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models.domain import TECHNICAL_TYPES, Classification, FocusState, QuestionType
from app.services.taxonomy import category_of, find_technologies

Q = QuestionType

_PATTERNS: dict[QuestionType, list[tuple[str, float]]] = {
    Q.GREETING: [(r"^(hi|hello|hey|good (morning|afternoon|evening))\b", 3), (r"\bnice to meet you\b", 3),
                 (r"\bhow are you( doing)?\b", 3), (r"\bthanks for (joining|coming|taking the time)\b", 3),
                 (r"^welcome\b", 2), (r"\bcan you hear me\b", 3)],
    Q.INTRODUCTION: [(r"\btell (me|us) (a (little|bit) )?about yourself\b", 6), (r"\bintroduce yourself\b", 6),
                     (r"\bwalk (me|us) through your (resume|cv|background|profile)\b", 6),
                     (r"\b(your|a quick) (background|introduction)\b", 3), (r"\bwho are you\b", 4),
                     (r"\bdescribe yourself\b", 5)],
    Q.BEHAVIORAL: [(r"\btell (me|us) about a time\b", 6),
                   (r"\btell (me|us) about (a|an|the) (difficult|hard|challenging|tough|complex|tricky) (problem|situation|bug|issue|decision)\b", 6),
                   (r"\b(problem|challenge|bug|issue)s? (that )?you (solved|faced|overcame|fixed|handled)\b", 4), (r"\b(describe|share) (a|an) (situation|time|instance|example)\b", 5),
                   (r"\bgive (me|us) an example of\b", 4), (r"\b(conflict|disagree(d|ment)?|failure|failed|mistake|setback)\b", 2.5),
                   (r"\b(tight|missed) deadline\b", 3), (r"\bunder pressure\b", 3), (r"\bproud(est)? of\b", 3),
                   (r"\bworked (in|with) a team\b", 2.5), (r"\bdifficult (teammate|colleague|person|situation)\b", 3),
                   (r"\btook (the )?(initiative|ownership)\b", 3), (r"\bhad to learn\b", 2)],
    Q.HR: [(r"\bwhy do you want\b", 5), (r"\bwhy should we hire\b", 6), (r"\b(your )?(greatest )?strengths?\b", 3),
           (r"\b(your )?(greatest )?weakness(es)?\b", 4), (r"\bwhere do you see yourself\b", 6),
           (r"\bsalary\b", 5), (r"\bnotice period\b", 5), (r"\brelocat", 4), (r"\bcareer goals?\b", 4),
           (r"\bwhy (this|our) (company|role|team)\b", 5), (r"\bmotivat", 2.5), (r"\bhobbies\b", 4),
           (r"\bwhat do you know about (us|our company)\b", 5), (r"\bany questions for (me|us)\b", 5)],
    Q.SITUATIONAL: [(r"\bwhat would you do if\b", 6), (r"\bhow would you handle\b", 5), (r"\bimagine (that|you)\b", 4),
                    (r"\bsuppose\b", 3), (r"\bif you were (in|given|asked)\b", 4), (r"\bhypothetically\b", 4)],
    Q.RESUME: [(r"\b(your|the) (resume|cv)\b", 4), (r"\byour (educational|academic) (background|qualifications?)\b", 6),
               (r"\byour time (working )?(at|with|in)\b", 5), (r"\b(when|while) you (worked|were working|interned) at\b", 5), (r"\byour (\S+ ){1,5}(certification|certificate|degree|internship|award)s?\b", 6), (r"\byour internship\b", 4), (r"\bwhile you were at\b", 4),
               (r"\byour (time|work|role) at\b", 4), (r"\byour (certification|certificate|degree|gpa|cgpa|education|coursework)\b", 4),
               (r"\bi see (that )?you\b", 3), (r"\byou mentioned\b", 3), (r"\byour (previous|last|current) (job|role|company)\b", 4),
               (r"\byour experience (at|with|in)\b", 3), (r"\b(hackathon|achievement|award)s?\b", 2)],
    Q.PROJECT: [(r"\byour (\w+ ){0,3}projects?\b", 4.5), (r"\b(this|that|the) project\b", 3), (r"\bprojects? you\b", 4),
                (r"\byou (built|developed|created|designed|worked on|implemented)\b", 3),
                (r"\bwalk (me|us) through (the|your) (architecture|design|implementation)\b", 4),
                (r"\bwhat did you build\b", 4)],
    Q.CODING: [(r"\bwrite (a|an|the)? ?(function|program|code|method|query|script|class)\b", 6),
               (r"\bimplement (a|an|the)\b", 3.5), (r"\bcode (to|that|for)\b", 4), (r"\bpseudo-?code\b", 4),
               (r"\bgiven (an?|the) (array|string|list|integer|linked list|tree|graph|matrix)\b", 4),
               (r"\breverse (a|the) (string|linked list|array)\b", 4), (r"\bdebug this\b", 4)],
    Q.DSA: [(r"\b(array|linked list|binary tree|bst|graph|heap|stack|queue|trie|hash ?map|hash ?table)s?\b", 2.5),
            (r"\b(binary search|dynamic programming|recursion|backtracking|greedy|bfs|dfs|two pointers?|sliding window|memoi[sz]ation)\b", 3.5),
            (r"\b(time|space) complexity\b", 4), (r"\bbig[- ]?o\b", 4), (r"\b(sort(ing)?|merge sort|quick ?sort)\b", 2.5),
            (r"\b(dsa|data structures?|algorithms?)\b", 3), (r"\bshortest path\b", 3.5)],
    Q.SYSTEM_DESIGN: [(r"\b(how would you )?design (a|an|the)\b", 4.5), (r"\bsystem design\b", 6),
                      (r"\bscale (it|this|to|up)\b", 3.5), (r"\bmillions? of (users|requests)\b", 4),
                      (r"\bhigh(ly)? availab", 3.5), (r"\bload balanc", 3), (r"\b(sharding|replication|partitioning)\b", 3),
                      (r"\barchitecture (for|of a)\b", 3), (r"\b(url shortener|rate limiter|chat (system|app)|news feed)\b", 4),
                      (r"\bat scale\b", 3), (r"\bfault[- ]toleran", 3), (r"\bcap theorem\b", 4)],
    Q.MACHINE_LEARNING: [(r"\bmachine learning\b", 3), (r"\boverfitting|underfitting\b", 4),
                         (r"\b(linear|logistic) regression\b", 4), (r"\b(random forest|decision tree|xgboost|svm|k-?means|naive bayes|gradient boosting)\b", 4),
                         (r"\bfeature (engineering|selection|scaling)\b", 4), (r"\bcross[- ]validation\b", 4),
                         (r"\b(precision|recall|f1|roc|auc|confusion matrix)\b", 3.5), (r"\bbias[- ]variance\b", 4),
                         (r"\b(supervised|unsupervised) learning\b", 4), (r"\b(ml|machine learning) model\b", 3),
                         (r"\bclass imbalance|imbalanced\b", 3.5), (r"\bregularization|regularisation\b", 3.5),
                         (r"\btf-?idf\b", 3)],
    Q.DEEP_LEARNING: [(r"\bneural networks?\b", 4), (r"\b(cnn|rnn|lstm|gru|convolution)\w*\b", 4),
                      (r"\bback-?propagation\b", 4), (r"\bgradient descent\b", 3.5), (r"\bactivation function\b", 4),
                      (r"\b(dropout|batch norm(alization)?|vanishing gradient)\b", 4), (r"\bdeep learning\b", 3.5),
                      (r"\battention mechanism|self-attention\b", 4), (r"\btransformers?\b", 2.5), (r"\bclip\b", 2)],
    Q.GENERATIVE_AI: [(r"\b(llms?|large language models?)\b", 4), (r"\bprompt(ing| engineering)?\b", 3),
                      (r"\bfine-?tun(e|ing)\b", 4), (r"\bhallucinat", 4), (r"\b(gpt|claude|gemini|llama)\b", 3),
                      (r"\bgenerative ai|gen ?ai\b", 4), (r"\b(diffusion|tokeniz)", 3), (r"\b(ai )?agents?\b|agentic\b", 2.5),
                      (r"\blora\b", 4), (r"\bcontext window\b", 3.5), (r"\blangchain|llamaindex|langgraph\b", 3)],
    Q.RAG: [(r"\brag\b", 5), (r"\bretrieval[- ]augmented\b", 5), (r"\bretriev(al|e|er)\b", 3),
            (r"\bvector (databases?|dbs?|stores?|search|index(es)?)\b", 4.5), (r"\bembeddings?\b", 3.5), (r"\bchunk(ing|s)?\b", 3.5),
            (r"\b(qdrant|pinecone|faiss|weaviate|milvus|chroma|pgvector)\b", 4.5), (r"\bsemantic search\b", 4),
            (r"\bre-?rank(ing|er)?\b", 4), (r"\bhybrid search\b", 4), (r"\bbm25\b", 4), (r"\bhnsw\b", 4)],
    Q.DATABASE: [(r"\b(sql|nosql)\b", 3.5), (r"\bdatabases?\b", 2.5), (r"\bindex(es|ing)?\b", 2.5),
                 (r"\bnormali[sz]ation\b", 4), (r"\bjoins?\b", 2.5), (r"\btransactions?\b", 3), (r"\bacid\b", 4),
                 (r"\b(postgres(ql)?|mysql|mongodb|sqlite|cassandra|dynamodb|redis)\b", 3),
                 (r"\bquery optimi[sz]", 4), (r"\bschema\b", 2.5), (r"\bprimary key|foreign key\b", 4),
                 (r"\bstored procedure|trigger\b", 3)],
    Q.CLOUD: [(r"\b(aws|azure|gcp|google cloud)\b", 4), (r"\bcloud\b", 2.5), (r"\b(ec2|s3|lambda|sagemaker|rds|iam|cloudwatch)\b", 4),
              (r"\bserverless\b", 4), (r"\bauto-?scal", 3)],
    Q.DEVOPS: [(r"\bdocker(file)?\b", 4), (r"\bkubernetes|k8s\b", 4), (r"\bci ?/ ?cd\b", 4), (r"\b(jenkins|terraform|github actions|ansible)\b", 4),
               (r"\bcontaineri[sz]", 3.5), (r"\bdeployment pipeline\b", 4), (r"\bmonitoring|observability\b", 2.5),
               (r"\binfrastructure as code\b", 4)],
    Q.SECURITY: [(r"\bsecurity|secure\b", 3), (r"\bauthenticat|authori[sz]", 3.5), (r"\bencrypt", 3.5),
                 (r"\b(xss|csrf|sql injection|owasp)\b", 5), (r"\b(oauth|jwt)\b", 3.5), (r"\bvulnerab", 4),
                 (r"\b(hashing passwords|password hash)", 4)],
    Q.TECHNICAL: [(r"^what (is|are) (a |an |the )?\w+", 1.5), (r"\bexplain\b", 1.5), (r"\bdifference between\b", 3),
                  (r"\bhow does .+ work\b", 3), (r"\b(python|java|c\+\+|javascript|typescript)\b", 2),
                  (r"\b(oop|object[- ]oriented|polymorphism|inheritance|encapsulation)\b", 4),
                  (r"\b(rest(ful)? api|api|websocket|http|grpc|graphql)s?\b", 2.5), (r"\b(flask|fastapi|django|react|node)\b", 2.5),
                  (r"\b(multithreading|concurrency|async|gil)\b", 3.5), (r"\b(operating system|deadlock|process|thread)s?\b", 2.5),
                  (r"\bcompare\b", 2), (r"\bpros and cons|trade-?offs?\b", 2)],
    Q.CLARIFICATION: [(r"\bwhat do you mean\b", 6), (r"\b(could|can) you (clarify|repeat|rephrase)\b", 6),
                      (r"\bsorry,? (what|i didn'?t)\b", 4), (r"\bdid you mean\b", 5), (r"\bcome again\b", 5),
                      (r"\bcan you say that again\b", 6)],
    Q.FEEDBACK: [(r"^(ok(ay)?|great|good|nice|perfect|alright|cool|got it|i see|right)[,.!]?\s+(that|it|this)\s+(makes sense|sounds (good|great|right)|is (great|good|helpful|interesting|fair))[.!]?$", 6),
                 (r"^(ok(ay)?|great|good|nice|perfect|excellent|interesting|cool|alright|right|got it|makes sense|i see|sure)[.!]?$", 6),
                 (r"^(that'?s|that is) (a )?(great|good|nice|helpful|interesting|fair)( answer| point)?[.!]?$", 6),
                 (r"^(thank you|thanks)[.!]?$", 5)],
}

_COMPILED = {t: [(re.compile(p, re.I), w) for p, w in pats] for t, pats in _PATTERNS.items()}

_QUESTION_START = re.compile(
    r"^(tell|explain|describe|walk|can|could|would|will|how|what|why|when|where|which|who|whom|whose|do|does|did|"
    r"have|has|is|are|was|were|should|give|talk|share|design|implement|write|compare|define|list|name|"
    r"elaborate|go (deeper|on)|any|let'?s (talk|discuss|dive)|i'?d like (to hear|you to)|i want you to)\b",
    re.I,
)
_STATEMENT_START = re.compile(
    r"^(let'?s (move|start|begin|switch|wrap)|moving on|next,? (we|i)|i'?ll (now|start)|we (are|have|will)|"
    r"our (company|team)|so,? (we|our)|the (role|position) (is|involves)|i (am|work|lead)|this (role|position))\b",
    re.I,
)
_ANAPHORA = re.compile(
    r"\b(about|choose|chose|pick|use|used|improve|explain|do|did|build|built|change|test|tested|deploy|deployed|"
    r"with|on|of|in|like|scale|implement|implemented|handle|handled)\s+(that|it|this|those|them|these)\b|"
    r"\b(that|it|this|them)\s*[?.!]*$|^(it|that|this|those|they)\b|\bthe same\b",
    re.I,
)
_FOLLOW_UP_CUES = re.compile(
    r"^(why( not)?|how( so)?|and( then)?|so|what about|what else|then what|can you elaborate|elaborate|"
    r"go (deeper|on)|could you expand|tell me more|more on that|interesting,? (why|how|what))\b",
    re.I,
)
_ASPECT_ONLY = re.compile(
    r"\b(the )?(biggest|main|hardest|key|major|toughest) (challenge|problem|difficulty|issue|lesson)s?\b|"
    r"\bwhat (challenges?|problems?|difficulties) did you (face|encounter|run into)\b|"
    r"\b(the )?(hardest|most difficult|toughest|trickiest|most interesting) (part|thing|bit)\b|"
    r"\b(what was|what's) your (role|contribution|part)\b|\bhow (would|could) you improve\b|"
    r"\bwhat would you (change|do differently|improve)\b|\bhow did you test\b|\bwhat were the (results|limitations|outcomes)\b|"
    r"\bwhy did you (choose|pick|use|go with|select|decide)\b|\bwhat (did|would) you learn\b|\bany limitations\b|"
    r"\bhow did you (measure|evaluate|validate|deploy|scale)\b",
    re.I,
)

_HARD_CUES = re.compile(
    r"\b(trade-?offs?|at scale|scal(e|ing|ability)|millions?|optimi[sz]e|internals?|under the hood|why not|"
    r"compare|versus|vs\.?|bottleneck|distributed|consisten(cy|t)|concurren|edge cases?|failure modes?|"
    r"production|latency|design (a|an))\b", re.I)
_EASY_CUES = re.compile(r"^(what is|what are|define|name|list|do you know)\b|\bbasics?\b", re.I)

# Technology category -> topic label
_CATEGORY_TOPIC = {
    "database": "DATABASE", "cloud": "CLOUD", "devops": "DEVOPS", "ai_ml": "MACHINE_LEARNING",
}
_VECTOR_TECH = {"qdrant", "pinecone", "weaviate", "milvus", "chroma", "faiss", "pgvector", "rag", "embeddings",
                "vector search", "langchain", "llamaindex", "sentence-transformers"}
_ML_TECH = {"scikit-learn", "xgboost", "lightgbm", "pandas", "numpy", "machine learning", "natural language processing",
            "data analysis", "matplotlib", "seaborn", "spacy", "nltk", "mlflow"}
_GENAI_TECH = {"large language models", "generative ai", "prompt engineering", "openai api", "claude api",
               "fine-tuning", "ai agents", "langgraph"}
_DL_TECH = {"deep learning", "pytorch", "tensorflow", "keras", "transformers", "clip", "computer vision"}

TOPIC_TYPES = {
    Q.RAG, Q.DATABASE, Q.CLOUD, Q.DEVOPS, Q.SECURITY, Q.MACHINE_LEARNING, Q.DEEP_LEARNING,
    Q.GENERATIVE_AI, Q.SYSTEM_DESIGN, Q.DSA, Q.CODING,
}


def topic_for_technologies(techs: list[str]) -> str | None:
    """Dominant topic of a set of technologies (e.g. a project's stack)."""
    counts: dict[str, int] = {}
    for t in techs:
        topic = topic_for_technology(t)
        if topic and topic != "TECHNICAL":
            counts[topic] = counts.get(topic, 0) + 1
    if not counts:
        return "TECHNICAL" if techs else None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def topic_for_technology(tech: str) -> str | None:
    low = tech.lower()
    if low in _VECTOR_TECH:
        return "RAG"
    if low in _GENAI_TECH:
        return "GENERATIVE_AI"
    if low in _DL_TECH:
        return "DEEP_LEARNING"
    if low in _ML_TECH:
        return "MACHINE_LEARNING"
    return _CATEGORY_TOPIC.get(category_of(tech)) or ("TECHNICAL" if category_of(tech) != "other" else None)


_YOUR_PROJECT = re.compile(r"\byour ((?:[\w+#.-]+ ){0,3}?)(?:project|app|application|system|tool|platform)\b", re.I)


@dataclass
class ClassifierContext:
    focus: FocusState = field(default_factory=FocusState)
    project_names: list[str] = field(default_factory=list)
    experience_orgs: list[str] = field(default_factory=list)
    has_history: bool = False
    project_technologies: dict[str, list[str]] = field(default_factory=dict)  # name -> stack


_GENERIC_NAME_WORDS = {"the", "and", "for", "platform", "system", "project", "app", "application", "tool",
                       "based", "using", "analytics", "technologies", "solutions", "labs", "inc", "ltd", "pvt"}


def name_mentioned(text: str, name: str) -> bool:
    """True when `name` (a project / organisation) is referred to in `text`,
    either in full or by its distinctive words ("the bug analyzer")."""
    low = text.lower()
    nl = name.lower().strip()
    if not nl:
        return False
    if nl in low:
        return True
    words = [w for w in re.findall(r"[a-z0-9]+", nl) if len(w) > 2 and w not in _GENERIC_NAME_WORDS]
    hits = [w for w in words if re.search(rf"\b{re.escape(w)}\b", low)]
    if not words:
        return False
    if len(words) == 1:
        return bool(hits) and len(words[0]) >= 4
    return len(hits) >= 2 or (len(hits) == 1 and len(hits[0]) >= 6 and hits[0] not in {"multimodal", "machine", "learning"})


def _mentions(text: str, names: list[str]) -> list[str]:
    return [n for n in names if n and name_mentioned(text, n)]

def classify(text: str, ctx: ClassifierContext | None = None) -> Classification:
    ctx = ctx or ClassifierContext()
    raw = text.strip()
    t = re.sub(r"\s+", " ", raw)
    low = t.lower()
    signals: list[str] = []
    scores: dict[QuestionType, float] = {}
    for qtype, pats in _COMPILED.items():
        s = 0.0
        for pat, w in pats:
            if pat.search(t):
                s += w
        if s:
            scores[qtype] = s

    techs = find_technologies(t)
    projects = _mentions(t, ctx.project_names)
    if not projects:
        # "your RAG project" -> the project whose name or stack contains the qualifier
        m = _YOUR_PROJECT.search(t)
        if m and m.group(1).strip():
            qual = m.group(1).strip().lower()
            for name in ctx.project_names:
                stack = [x.lower() for x in ctx.project_technologies.get(name, [])]
                if qual in name.lower() or qual in stack:
                    projects = [name]
                    break
    orgs = _mentions(t, ctx.experience_orgs)
    if projects:
        scores[Q.PROJECT] = scores.get(Q.PROJECT, 0) + 5
        signals.append(f"project:{projects[0]}")
    if orgs:
        scores[Q.RESUME] = scores.get(Q.RESUME, 0) + 4
        signals.append(f"org:{orgs[0]}")

    is_question = raw.endswith("?") or bool(_QUESTION_START.match(t))
    word_count = len(t.split())
    has_focus = bool(ctx.focus.project_id or ctx.focus.experience_id or ctx.focus.topic)
    personal = bool(re.search(r"\b(you|your|you'?ve|you'?d)\b", low))

    # --- follow-up detection (needs prior context)
    follow_up = False
    if ctx.has_history and has_focus and not projects and Q.INTRODUCTION not in scores:
        aspect_only = bool(_ASPECT_ONLY.search(t))
        anaphoric = bool(_ANAPHORA.search(t)) and word_count <= 14
        cue = bool(_FOLLOW_UP_CUES.match(t)) and word_count <= 12
        focus_tech = any(x.lower() in {y.lower() for y in _focus_techs(ctx)} for x in techs)
        if aspect_only and (personal or not techs) and (not techs or focus_tech):
            follow_up = True
            signals.append("aspect-without-subject")
        elif focus_tech and personal and re.search(r"\b(choose|chose|pick|use|used|select|go with|why)\b", low):
            follow_up = True
            signals.append("technology-from-focus-project")
        elif anaphoric and personal and re.search(r"\bwhy did you (choose|chose|pick|use|go with|select)\b", low):
            # "why did you choose Kubernetes for it?" - a choice about the project in focus
            follow_up = True
            signals.append("choice-about-focus")
        elif (anaphoric or cue) and is_question and not (techs and not focus_tech) and max(scores.values(), default=0) < 5:
            follow_up = True
            signals.append("anaphora" if anaphoric else "follow-up-cue")

    if Q.FEEDBACK in scores and not is_question:
        qtype = Q.FEEDBACK
    elif Q.CLARIFICATION in scores:
        qtype = Q.CLARIFICATION
    elif follow_up:
        qtype = Q.FOLLOW_UP
    elif not is_question and (_STATEMENT_START.match(t) or not scores):
        qtype = Q.STATEMENT
    elif not scores:
        qtype = Q.UNKNOWN if is_question else Q.STATEMENT
    else:
        # Personal questions about a domain ("how did you use Docker in your project")
        # are project/resume questions, not textbook questions.
        if personal and (Q.PROJECT in scores or Q.RESUME in scores):
            for tt in TECHNICAL_TYPES:
                if tt in scores and tt not in (Q.SYSTEM_DESIGN, Q.CODING):
                    scores[tt] *= 0.6
        qtype = max(scores.items(), key=lambda kv: kv[1])[0]
        # GREETING followed by a real question: classify the question
        if qtype == Q.GREETING and len(scores) > 1 and word_count > 8:
            scores.pop(Q.GREETING)
            qtype = max(scores.items(), key=lambda kv: kv[1])[0]
        if qtype == Q.TECHNICAL:
            # promote to a specialised technical domain when one is present
            specialised = {k: v for k, v in scores.items() if k in TOPIC_TYPES}
            if specialised:
                best, val = max(specialised.items(), key=lambda kv: kv[1])
                if val >= scores[Q.TECHNICAL] * 0.6:
                    qtype = best

    # --- topic
    topic: str | None = None
    domain_scores = {k: v for k, v in scores.items() if k in TOPIC_TYPES}
    if qtype in TOPIC_TYPES:
        topic = qtype.value
    elif domain_scores:
        topic = max(domain_scores.items(), key=lambda kv: kv[1])[0].value
    elif techs:
        topic = topic_for_technology(techs[0])
    if qtype == Q.FOLLOW_UP and not topic:
        topic = ctx.focus.topic
    if qtype in (Q.BEHAVIORAL, Q.HR, Q.INTRODUCTION, Q.GREETING) and not topic:
        topic = qtype.value

    # --- difficulty
    if qtype in (Q.GREETING, Q.INTRODUCTION, Q.FEEDBACK, Q.STATEMENT, Q.CLARIFICATION):
        difficulty = "easy"
    elif qtype == Q.SYSTEM_DESIGN or len(_HARD_CUES.findall(t)) >= 2:
        difficulty = "hard"
    elif _HARD_CUES.search(t) and qtype in TECHNICAL_TYPES:
        difficulty = "hard"
    elif _EASY_CUES.search(t) and word_count <= 10:
        difficulty = "easy"
    else:
        difficulty = "medium"

    resume_types = {Q.INTRODUCTION, Q.RESUME, Q.PROJECT, Q.BEHAVIORAL, Q.HR, Q.SITUATIONAL}
    requires_resume = qtype in resume_types or (
        qtype == Q.FOLLOW_UP and bool(ctx.focus.project_id or ctx.focus.experience_id)
    ) or (personal and qtype in TECHNICAL_TYPES and bool(re.search(r"\b(did you|have you|you used|your)\b", low)))
    requires_tech = qtype in TECHNICAL_TYPES or qtype == Q.SITUATIONAL or (
        qtype in (Q.FOLLOW_UP, Q.PROJECT) and (bool(techs) or bool(re.search(r"\bwhy\b", low)))
    )
    requires_prev = qtype in (Q.FOLLOW_UP, Q.CLARIFICATION) or (
        bool(_ANAPHORA.search(t)) and ctx.has_history and word_count <= 10 and qtype not in (Q.FEEDBACK, Q.STATEMENT)
    )

    top = sorted(scores.values(), reverse=True)
    if qtype == Q.FOLLOW_UP:
        confidence = 0.8
    elif not top:
        confidence = 0.4
    else:
        margin = top[0] - (top[1] if len(top) > 1 else 0)
        confidence = round(min(0.97, 0.5 + 0.08 * top[0] + 0.05 * margin), 2)

    return Classification(
        type=qtype, topic=topic, difficulty=difficulty,  # type: ignore[arg-type]
        is_question=qtype not in (Q.STATEMENT, Q.FEEDBACK) and (is_question or qtype != Q.UNKNOWN),
        requires_resume_context=requires_resume, requires_technical_context=requires_tech,
        requires_previous_turn_context=requires_prev, confidence=confidence, signals=signals,
        entities=list(dict.fromkeys([*projects, *orgs, *techs])),
    )


def _focus_techs(ctx: ClassifierContext) -> list[str]:
    return ctx.focus.project_technologies
