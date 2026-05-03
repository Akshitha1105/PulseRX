import os
import re
import io
import csv
import json
import random
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

try:
    from textblob import TextBlob
    TEXTBLOB_AVAILABLE = True
except ImportError:
    TEXTBLOB_AVAILABLE = False

try:
    import praw
    PRAW_AVAILABLE = True
except ImportError:
    PRAW_AVAILABLE = False

# ─────────────────────────────────────────────
# Database Setup
# ─────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "pulserx.db")
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    keywords = Column(Text, nullable=False)   # JSON list stored as string
    sources = Column(Text, nullable=False)    # JSON list stored as string
    created_at = Column(DateTime, default=datetime.utcnow)
    posts = relationship("Post", back_populates="project")


class Post(Base):
    __tablename__ = "posts"
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"))
    source = Column(String)
    title = Column(Text)
    body = Column(Text)
    author = Column(String)
    url = Column(String)
    sentiment_score = Column(Float, default=0.0)
    sentiment_label = Column(String, default="neutral")
    entities = Column(Text, default="[]")         # JSON list
    is_adverse_event = Column(Boolean, default=False)
    ingested_at = Column(DateTime, default=datetime.utcnow)
    project = relationship("Project", back_populates="posts")
    alert = relationship("Alert", back_populates="post", uselist=False)


class Alert(Base):
    __tablename__ = "alerts"
    id = Column(Integer, primary_key=True, index=True)
    post_id = Column(Integer, ForeignKey("posts.id"))
    reason = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    post = relationship("Post", back_populates="alert")


Base.metadata.create_all(bind=engine)


# ─────────────────────────────────────────────
# NLP Helpers
# ─────────────────────────────────────────────
ADVERSE_KEYWORDS = [
    "side effect", "adverse", "reaction", "stopped taking", "rash", "pain",
    "nausea", "dizziness", "vomiting", "headache", "fatigue", "swelling",
    "allergic", "overdose", "toxicity", "hospitalized", "emergency",
    "worsened", "complications", "severe", "unbearable", "dangerous"
]

DRUG_NAMES = [
    "metformin", "lisinopril", "atorvastatin", "amlodipine", "omeprazole",
    "losartan", "levothyroxine", "azithromycin", "metoprolol", "albuterol",
    "gabapentin", "sertraline", "hydrochlorothiazide", "furosemide",
    "pantoprazole", "montelukast", "tramadol", "oxycodone", "ibuprofen",
    "acetaminophen", "aspirin", "prednisone", "insulin", "glipizide",
    "insulin glargine", "bupropion", "duloxetine", "clonazepam", "alprazolam",
    "zolpidem", "warfarin", "clopidogrel", "simvastatin", "rosuvastatin",
    "amoxicillin", "doxycycline", "ciprofloxacin", "fluoxetine", "escitalopram"
]

SYMPTOM_WORDS = [
    "pain", "ache", "nausea", "fatigue", "tired", "dizzy", "dizziness",
    "rash", "itching", "swelling", "shortness of breath", "chest pain",
    "palpitations", "anxiety", "depression", "insomnia", "constipation",
    "diarrhea", "headache", "migraine", "blurred vision", "dry mouth",
    "weight gain", "weight loss", "numbness", "tingling", "cramps"
]

EMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Z|a-z]{2,}\b')
PHONE_RE = re.compile(r'(\+?1?\s?)?(\(?\d{3}\)?[\s.\-]?)(\d{3}[\s.\-]?\d{4})')


def mask_pii(text: str) -> str:
    text = EMAIL_RE.sub("[MASKED]", text)
    text = PHONE_RE.sub("[MASKED]", text)
    return text


def analyze_sentiment(text: str):
    if not TEXTBLOB_AVAILABLE or not text:
        return 0.0, "neutral"
    blob = TextBlob(text)
    score = blob.sentiment.polarity
    if score > 0.1:
        label = "positive"
    elif score < -0.1:
        label = "negative"
    else:
        label = "neutral"
    return round(score, 4), label


def extract_entities(text: str) -> list:
    text_lower = text.lower()
    found = []
    for drug in DRUG_NAMES:
        if drug in text_lower:
            found.append({"type": "drug", "value": drug})
    for symptom in SYMPTOM_WORDS:
        if symptom in text_lower:
            found.append({"type": "symptom", "value": symptom})
    return found


def is_adverse_event(text: str) -> tuple:
    text_lower = text.lower()
    triggered = [kw for kw in ADVERSE_KEYWORDS if kw in text_lower]
    return bool(triggered), ", ".join(triggered)


# ─────────────────────────────────────────────
# Simulated Reddit Data
# ─────────────────────────────────────────────
SIMULATED_POSTS = [
    {
        "title": "Metformin side effects after 3 months",
        "body": "I've been on metformin for diabetes for 3 months now and the nausea is unbearable. My doctor said to push through but I stopped taking it last week. Anyone else had this reaction?",
        "author": "user_diab42",
        "subreddit": "diabetes",
        "url": "https://reddit.com/r/diabetes/simulated1"
    },
    {
        "title": "Lisinopril causing dizziness — is this normal?",
        "body": "Started lisinopril for hypertension 2 weeks ago. I feel dizzy every morning and had a rash on my arms. Called my doctor but wanted to hear from others too.",
        "author": "htn_patient",
        "subreddit": "hypertension",
        "url": "https://reddit.com/r/hypertension/simulated2"
    },
    {
        "title": "Finally found relief with gabapentin",
        "body": "After years of chronic pain, gabapentin has been a game changer. Slight fatigue at first but it faded. Overall very positive experience with this medication.",
        "author": "chronic_fighter",
        "subreddit": "ChronicPain",
        "url": "https://reddit.com/r/ChronicPain/simulated3"
    },
    {
        "title": "Question about atorvastatin and muscle pain",
        "body": "My doctor prescribed atorvastatin for cholesterol. I've been experiencing severe muscle pain and weakness. Is this a known side effect? Should I go to the emergency room?",
        "author": "worried_patient",
        "subreddit": "AskDocs",
        "url": "https://reddit.com/r/AskDocs/simulated4"
    },
    {
        "title": "Insulin glargine — best time to inject?",
        "body": "Just started insulin glargine and feeling good about it. No adverse reactions so far. My blood sugar has been much more stable. Very happy with the results.",
        "author": "t1d_life",
        "subreddit": "diabetes",
        "url": "https://reddit.com/r/diabetes/simulated5"
    },
    {
        "title": "Losartan allergic reaction — hospitalized",
        "body": "I had a severe allergic reaction to losartan and ended up hospitalized for 2 days. The swelling in my face and throat was dangerous. Never taking it again. Please be careful.",
        "author": "recovered_patient",
        "subreddit": "hypertension",
        "url": "https://reddit.com/r/hypertension/simulated6"
    },
    {
        "title": "Sertraline helping my anxiety",
        "body": "Been on sertraline for 6 months. Initially had headache and insomnia but those went away. My anxiety is much better now. Feeling hopeful.",
        "author": "anxiety_warrior",
        "subreddit": "AskDocs",
        "url": "https://reddit.com/r/AskDocs/simulated7"
    },
    {
        "title": "Prednisone weight gain and complications",
        "body": "6 weeks on prednisone for inflammation. I gained 15 pounds, have severe mood swings, and my blood pressure worsened. The complications are making life very difficult.",
        "author": "steroids_suck",
        "subreddit": "ChronicPain",
        "url": "https://reddit.com/r/ChronicPain/simulated8"
    },
    {
        "title": "Omeprazole long-term use concerns",
        "body": "Been on omeprazole for 2 years. Recent bloodwork showed low magnesium. Doctor says long-term use can cause this. Anyone else dealing with this side effect?",
        "author": "gerd_patient",
        "subreddit": "AskDocs",
        "url": "https://reddit.com/r/AskDocs/simulated9"
    },
    {
        "title": "Amlodipine — swollen ankles after 1 week",
        "body": "Started amlodipine for blood pressure. My ankles are very swollen now. Is this a reaction I should be worried about? Should I stop taking it?",
        "author": "bp_newbie",
        "subreddit": "hypertension",
        "url": "https://reddit.com/r/hypertension/simulated10"
    },
]


def get_simulated_posts(keywords: list, count: int = 5) -> list:
    scored = []
    for post in SIMULATED_POSTS:
        combined = (post["title"] + " " + post["body"]).lower()
        score = sum(1 for kw in keywords if kw.lower() in combined)
        scored.append((score, post))
    scored.sort(key=lambda x: x[0], reverse=True)
    results = [p for _, p in scored[:count]]
    if len(results) < count:
        extras = [p for _, p in scored if p not in results]
        results += extras[:count - len(results)]
    # Randomize ingested_at slightly for chart variety
    for post in results:
        post["ingested_at"] = (
            datetime.utcnow() - timedelta(hours=random.randint(0, 72))
        ).isoformat()
    return results


def fetch_reddit_posts(keywords: list, sources: list) -> list:
    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    user_agent = os.getenv("REDDIT_USER_AGENT", "PulseRx/1.0 drug-safety-monitor")

    if not (PRAW_AVAILABLE and client_id and client_secret):
        return get_simulated_posts(keywords)

    try:
        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent
        )
        posts = []
        subreddits = [s.strip().lstrip("r/") for s in sources if s.strip()]
        if not subreddits:
            subreddits = ["diabetes", "hypertension", "ChronicPain", "AskDocs"]

        for subreddit_name in subreddits[:3]:
            try:
                subreddit = reddit.subreddit(subreddit_name)
                for keyword in keywords[:2]:
                    for submission in subreddit.search(keyword, limit=3, sort="new"):
                        posts.append({
                            "title": submission.title,
                            "body": submission.selftext or "",
                            "author": str(submission.author) if submission.author else "deleted",
                            "subreddit": subreddit_name,
                            "url": f"https://reddit.com{submission.permalink}",
                            "ingested_at": datetime.utcnow().isoformat()
                        })
            except Exception:
                continue
        return posts if posts else get_simulated_posts(keywords)
    except Exception:
        return get_simulated_posts(keywords)


# ─────────────────────────────────────────────
# FastAPI App
# ─────────────────────────────────────────────
app = FastAPI(title="PulseRx", description="Social Listening for Drug Safety")


# ─────────────────────────────────────────────
# Pydantic Schemas
# ─────────────────────────────────────────────
class ProjectCreate(BaseModel):
    name: str
    keywords: List[str]
    sources: List[str]


class IngestRequest(BaseModel):
    project_id: int


# ─────────────────────────────────────────────
# API Routes
# ─────────────────────────────────────────────
@app.post("/projects")
def create_project(payload: ProjectCreate):
    db = SessionLocal()
    try:
        project = Project(
            name=payload.name,
            keywords=json.dumps(payload.keywords),
            sources=json.dumps(payload.sources)
        )
        db.add(project)
        db.commit()
        db.refresh(project)
        return {
            "id": project.id,
            "name": project.name,
            "keywords": json.loads(project.keywords),
            "sources": json.loads(project.sources),
            "created_at": project.created_at.isoformat()
        }
    finally:
        db.close()


@app.get("/projects")
def list_projects():
    db = SessionLocal()
    try:
        projects = db.query(Project).all()
        return [
            {
                "id": p.id,
                "name": p.name,
                "keywords": json.loads(p.keywords),
                "sources": json.loads(p.sources),
                "created_at": p.created_at.isoformat(),
                "post_count": len(p.posts)
            }
            for p in projects
        ]
    finally:
        db.close()


@app.get("/projects/{project_id}")
def get_project(project_id: int):
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        return {
            "id": project.id,
            "name": project.name,
            "keywords": json.loads(project.keywords),
            "sources": json.loads(project.sources),
            "created_at": project.created_at.isoformat(),
            "post_count": len(project.posts)
        }
    finally:
        db.close()


@app.post("/ingest")
def ingest_posts(payload: IngestRequest):
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == payload.project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        keywords = json.loads(project.keywords)
        sources = json.loads(project.sources)

        raw_posts = fetch_reddit_posts(keywords, sources)

        ingested = 0
        alerts_created = 0

        for raw in raw_posts:
            title = mask_pii(raw.get("title", ""))
            body = mask_pii(raw.get("body", ""))
            combined = f"{title} {body}"

            score, label = analyze_sentiment(combined)
            entities = extract_entities(combined)
            adverse, reason = is_adverse_event(combined)

            post = Post(
                project_id=project.id,
                source=f"r/{raw.get('subreddit', 'reddit')}",
                title=title,
                body=body,
                author=raw.get("author", "unknown"),
                url=raw.get("url", ""),
                sentiment_score=score,
                sentiment_label=label,
                entities=json.dumps(entities),
                is_adverse_event=adverse,
                ingested_at=datetime.utcnow()
            )
            db.add(post)
            db.flush()

            if adverse:
                alert = Alert(post_id=post.id, reason=reason)
                db.add(alert)
                alerts_created += 1

            ingested += 1

        db.commit()
        return {
            "status": "success",
            "posts_ingested": ingested,
            "alerts_created": alerts_created,
            "mode": "live" if (PRAW_AVAILABLE and os.getenv("REDDIT_CLIENT_ID")) else "simulated"
        }
    finally:
        db.close()


@app.get("/posts")
def get_posts(project_id: Optional[int] = None, limit: int = 100):
    db = SessionLocal()
    try:
        query = db.query(Post)
        if project_id:
            query = query.filter(Post.project_id == project_id)
        posts = query.order_by(Post.ingested_at.desc()).limit(limit).all()
        return [
            {
                "id": p.id,
                "project_id": p.project_id,
                "source": p.source,
                "title": p.title,
                "body": p.body[:300] + "..." if len(p.body) > 300 else p.body,
                "author": p.author,
                "url": p.url,
                "sentiment_score": p.sentiment_score,
                "sentiment_label": p.sentiment_label,
                "entities": json.loads(p.entities),
                "is_adverse_event": p.is_adverse_event,
                "ingested_at": p.ingested_at.isoformat()
            }
            for p in posts
        ]
    finally:
        db.close()


@app.get("/alerts")
def get_alerts(project_id: Optional[int] = None):
    db = SessionLocal()
    try:
        query = db.query(Alert).join(Post)
        if project_id:
            query = query.filter(Post.project_id == project_id)
        alerts = query.order_by(Alert.created_at.desc()).all()
        return [
            {
                "id": a.id,
                "post_id": a.post_id,
                "reason": a.reason,
                "created_at": a.created_at.isoformat(),
                "post": {
                    "title": a.post.title,
                    "body": a.post.body[:300] + "..." if len(a.post.body) > 300 else a.post.body,
                    "source": a.post.source,
                    "author": a.post.author,
                    "url": a.post.url,
                    "sentiment_score": a.post.sentiment_score,
                    "sentiment_label": a.post.sentiment_label,
                    "ingested_at": a.post.ingested_at.isoformat()
                }
            }
            for a in alerts
        ]
    finally:
        db.close()


@app.get("/stats")
def get_stats(project_id: Optional[int] = None):
    db = SessionLocal()
    try:
        query = db.query(Post)
        if project_id:
            query = query.filter(Post.project_id == project_id)
        posts = query.order_by(Post.ingested_at).all()
        chart_data = [
            {
                "time": p.ingested_at.strftime("%m/%d %H:%M"),
                "sentiment": p.sentiment_score
            }
            for p in posts
        ]
        total = len(posts)
        adverse = sum(1 for p in posts if p.is_adverse_event)
        pos = sum(1 for p in posts if p.sentiment_label == "positive")
        neg = sum(1 for p in posts if p.sentiment_label == "negative")
        return {
            "total_posts": total,
            "adverse_events": adverse,
            "positive_posts": pos,
            "negative_posts": neg,
            "chart_data": chart_data
        }
    finally:
        db.close()


@app.get("/export/posts")
def export_posts_csv(project_id: Optional[int] = None):
    db = SessionLocal()
    try:
        query = db.query(Post)
        if project_id:
            query = query.filter(Post.project_id == project_id)
        posts = query.order_by(Post.ingested_at.desc()).all()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "post_id", "project_id", "source", "author", "title", "body",
            "sentiment_score", "sentiment_label", "entities",
            "is_adverse_event", "url", "ingested_at"
        ])
        for p in posts:
            entities = json.loads(p.entities)
            entity_str = "; ".join(f"{e['type']}:{e['value']}" for e in entities)
            writer.writerow([
                p.id, p.project_id, p.source, p.author,
                p.title, p.body,
                p.sentiment_score, p.sentiment_label,
                entity_str, "Yes" if p.is_adverse_event else "No",
                p.url, p.ingested_at.isoformat()
            ])

        output.seek(0)
        filename = f"pulserx_posts_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    finally:
        db.close()


@app.get("/export/alerts")
def export_alerts_csv(project_id: Optional[int] = None):
    db = SessionLocal()
    try:
        query = db.query(Alert).join(Post)
        if project_id:
            query = query.filter(Post.project_id == project_id)
        alerts = query.order_by(Alert.created_at.desc()).all()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "alert_id", "post_id", "source", "author", "title", "body",
            "adverse_event_triggers", "sentiment_score", "sentiment_label",
            "url", "ingested_at", "alert_created_at"
        ])
        for a in alerts:
            writer.writerow([
                a.id, a.post_id, a.post.source, a.post.author,
                a.post.title, a.post.body,
                a.reason, a.post.sentiment_score, a.post.sentiment_label,
                a.post.url, a.post.ingested_at.isoformat(),
                a.created_at.isoformat()
            ])

        output.seek(0)
        filename = f"pulserx_alerts_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    finally:
        db.close()


# ─────────────────────────────────────────────
# HTML Frontend
# ─────────────────────────────────────────────
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>PulseRx — Drug Safety Monitor</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #0f1117;
    --surface: #1a1d27;
    --surface2: #22263a;
    --accent: #6c63ff;
    --accent2: #00d4aa;
    --danger: #ff4d6d;
    --warn: #ffb347;
    --text: #e8eaf0;
    --muted: #8b90a7;
    --border: #2e3354;
    --positive: #00d4aa;
    --negative: #ff4d6d;
    --neutral: #ffb347;
    --radius: 10px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; min-height: 100vh; }

  header {
    background: linear-gradient(135deg, #1a1d27 0%, #13162b 100%);
    border-bottom: 1px solid var(--border);
    padding: 18px 32px;
    display: flex;
    align-items: center;
    gap: 16px;
    position: sticky;
    top: 0;
    z-index: 100;
    backdrop-filter: blur(10px);
  }
  .logo { font-size: 22px; font-weight: 800; color: var(--accent); letter-spacing: -0.5px; }
  .logo span { color: var(--accent2); }
  .tagline { color: var(--muted); font-size: 13px; }
  .badge-live {
    margin-left: auto;
    background: rgba(0,212,170,0.15);
    color: var(--accent2);
    border: 1px solid var(--accent2);
    border-radius: 20px;
    padding: 4px 12px;
    font-size: 12px;
    font-weight: 600;
  }

  .main { display: grid; grid-template-columns: 280px 1fr; min-height: calc(100vh - 65px); }

  .sidebar {
    background: var(--surface);
    border-right: 1px solid var(--border);
    padding: 24px 16px;
    display: flex;
    flex-direction: column;
    gap: 24px;
  }

  .content { padding: 28px 32px; display: flex; flex-direction: column; gap: 28px; }

  h2 { font-size: 14px; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); margin-bottom: 12px; }
  h3 { font-size: 18px; font-weight: 700; color: var(--text); margin-bottom: 16px; }

  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 20px;
  }

  label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 5px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }

  input, textarea {
    width: 100%;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text);
    padding: 9px 12px;
    font-size: 13px;
    margin-bottom: 12px;
    outline: none;
    transition: border-color 0.2s;
  }
  input:focus, textarea:focus { border-color: var(--accent); }
  textarea { resize: vertical; min-height: 60px; }

  .btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 9px 18px;
    border-radius: 6px;
    border: none;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s;
  }
  .btn-primary { background: var(--accent); color: #fff; }
  .btn-primary:hover { background: #7a72ff; }
  .btn-success { background: var(--accent2); color: #0f1117; }
  .btn-success:hover { background: #00e8ba; }
  .btn-danger { background: var(--danger); color: #fff; }
  .btn-sm { padding: 5px 12px; font-size: 12px; }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }

  .stats-row {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 16px;
  }
  .stat-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 18px 20px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .stat-label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.8px; }
  .stat-value { font-size: 32px; font-weight: 800; color: var(--text); }
  .stat-card.danger .stat-value { color: var(--danger); }
  .stat-card.positive .stat-value { color: var(--positive); }
  .stat-card.negative .stat-value { color: var(--negative); }

  .project-item {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 14px;
    margin-bottom: 8px;
    cursor: pointer;
    transition: all 0.2s;
  }
  .project-item:hover { border-color: var(--accent); }
  .project-item.active { border-color: var(--accent); background: rgba(108,99,255,0.1); }
  .project-item h4 { font-size: 13px; font-weight: 700; margin-bottom: 3px; }
  .project-item .meta { font-size: 11px; color: var(--muted); }

  .ingest-bar {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 14px 20px;
    background: linear-gradient(90deg, rgba(108,99,255,0.1), rgba(0,212,170,0.05));
    border: 1px solid var(--border);
    border-radius: var(--radius);
  }
  .ingest-bar span { flex: 1; font-size: 14px; font-weight: 600; }

  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  thead th {
    text-align: left;
    padding: 10px 14px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
    font-weight: 700;
  }
  tbody tr { border-bottom: 1px solid var(--border); transition: background 0.15s; }
  tbody tr:hover { background: var(--surface2); }
  tbody td { padding: 12px 14px; vertical-align: top; }

  .badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
  }
  .badge-pos { background: rgba(0,212,170,0.15); color: var(--positive); }
  .badge-neg { background: rgba(255,77,109,0.15); color: var(--negative); }
  .badge-neu { background: rgba(255,179,71,0.15); color: var(--warn); }
  .badge-adverse { background: rgba(255,77,109,0.2); color: var(--danger); border: 1px solid var(--danger); }

  .entity-tag {
    display: inline-block;
    padding: 2px 7px;
    border-radius: 4px;
    font-size: 10px;
    font-weight: 600;
    margin: 1px;
    text-transform: uppercase;
  }
  .entity-drug { background: rgba(108,99,255,0.2); color: #a99eff; }
  .entity-symptom { background: rgba(255,179,71,0.2); color: var(--warn); }

  .alert-card {
    background: rgba(255,77,109,0.07);
    border: 1px solid rgba(255,77,109,0.3);
    border-left: 3px solid var(--danger);
    border-radius: var(--radius);
    padding: 16px 20px;
    margin-bottom: 12px;
  }
  .alert-card h4 { font-size: 14px; font-weight: 700; margin-bottom: 6px; }
  .alert-card p { font-size: 13px; color: var(--muted); line-height: 1.5; margin-bottom: 8px; }
  .alert-card .reason { font-size: 11px; color: var(--danger); font-weight: 600; text-transform: uppercase; }
  .alert-meta { display: flex; gap: 12px; align-items: center; font-size: 11px; color: var(--muted); margin-top: 6px; }

  .chart-wrap { position: relative; height: 200px; }

  .tab-row { display: flex; gap: 4px; border-bottom: 1px solid var(--border); margin-bottom: 20px; }
  .tab {
    padding: 10px 18px;
    font-size: 13px;
    font-weight: 600;
    color: var(--muted);
    cursor: pointer;
    border-bottom: 2px solid transparent;
    margin-bottom: -1px;
    transition: all 0.2s;
  }
  .tab.active { color: var(--accent); border-bottom-color: var(--accent); }
  .tab:hover { color: var(--text); }

  .tab-panel { display: none; }
  .tab-panel.active { display: block; }

  .empty-state {
    text-align: center;
    padding: 48px;
    color: var(--muted);
    font-size: 14px;
  }
  .empty-state .icon { font-size: 40px; margin-bottom: 12px; }

  .toast {
    position: fixed;
    bottom: 24px;
    right: 24px;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 20px;
    font-size: 13px;
    font-weight: 600;
    z-index: 999;
    transform: translateY(100px);
    opacity: 0;
    transition: all 0.3s;
    max-width: 360px;
  }
  .toast.show { transform: translateY(0); opacity: 1; }
  .toast.success { border-color: var(--accent2); color: var(--accent2); }
  .toast.error { border-color: var(--danger); color: var(--danger); }

  #spinner { display: none; width: 14px; height: 14px; border: 2px solid rgba(255,255,255,0.3); border-top-color: #fff; border-radius: 50%; animation: spin 0.7s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  .score-bar { display: flex; align-items: center; gap: 6px; }
  .score-bar-track { flex: 1; height: 4px; background: var(--border); border-radius: 2px; overflow: hidden; }
  .score-bar-fill { height: 100%; border-radius: 2px; transition: width 0.4s; }
</style>
</head>
<body>

<header>
  <div>
    <div class="logo">Pulse<span>Rx</span></div>
    <div class="tagline">Social Listening for Drug Safety Monitoring</div>
  </div>
  <div class="badge-live">&#9679; Live Monitor</div>
</header>

<div class="main">
  <!-- Sidebar -->
  <div class="sidebar">
    <div>
      <h2>Projects</h2>
      <div id="projects-list"><div class="empty-state" style="padding:20px;font-size:12px;">No projects yet</div></div>
    </div>

    <div>
      <h2>New Project</h2>
      <div class="card" style="padding:16px;">
        <label>Project Name</label>
        <input type="text" id="proj-name" placeholder="e.g. Metformin Safety Watch"/>
        <label>Keywords (comma separated)</label>
        <input type="text" id="proj-keywords" placeholder="metformin, diabetes, nausea"/>
        <label>Subreddits (comma separated)</label>
        <input type="text" id="proj-sources" placeholder="diabetes, hypertension, AskDocs"/>
        <button class="btn btn-primary" style="width:100%" onclick="createProject()">+ Create Project</button>
      </div>
    </div>
  </div>

  <!-- Main Content -->
  <div class="content">
    <!-- Stats -->
    <div class="stats-row">
      <div class="stat-card">
        <div class="stat-label">Total Posts</div>
        <div class="stat-value" id="stat-total">0</div>
      </div>
      <div class="stat-card danger">
        <div class="stat-label">Adverse Events</div>
        <div class="stat-value" id="stat-adverse">0</div>
      </div>
      <div class="stat-card positive">
        <div class="stat-label">Positive</div>
        <div class="stat-value" id="stat-positive">0</div>
      </div>
      <div class="stat-card negative">
        <div class="stat-label">Negative</div>
        <div class="stat-value" id="stat-negative">0</div>
      </div>
    </div>

    <!-- Ingest bar -->
    <div class="ingest-bar" id="ingest-bar" style="display:none">
      <span id="ingest-project-label">Select a project to monitor</span>
      <div id="spinner"></div>
      <button class="btn btn-success" id="ingest-btn" onclick="ingestPosts()">
        &#8635; Fetch Posts
      </button>
      <button class="btn" style="background:var(--surface2);border:1px solid var(--border);color:var(--text)" onclick="exportCSV('posts')" title="Export all posts as CSV">
        &#8659; Posts CSV
      </button>
      <button class="btn" style="background:rgba(255,77,109,0.12);border:1px solid rgba(255,77,109,0.3);color:var(--danger)" onclick="exportCSV('alerts')" title="Export adverse event alerts as CSV">
        &#8659; Alerts CSV
      </button>
    </div>

    <!-- Sentiment Chart -->
    <div class="card" id="chart-section" style="display:none">
      <h3>Sentiment Trend</h3>
      <div class="chart-wrap">
        <canvas id="sentimentChart"></canvas>
      </div>
    </div>

    <!-- Tabs -->
    <div>
      <div class="tab-row">
        <div class="tab active" onclick="switchTab('posts')">All Posts</div>
        <div class="tab" onclick="switchTab('alerts')">Adverse Event Alerts</div>
      </div>

      <!-- Posts Tab -->
      <div class="tab-panel active" id="tab-posts">
        <div id="posts-container">
          <div class="empty-state">
            <div class="icon">&#128196;</div>
            Create a project and fetch posts to begin monitoring
          </div>
        </div>
      </div>

      <!-- Alerts Tab -->
      <div class="tab-panel" id="tab-alerts">
        <div id="alerts-container">
          <div class="empty-state">
            <div class="icon">&#128680;</div>
            No adverse event alerts yet
          </div>
        </div>
      </div>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
let activeProjectId = null;
let sentimentChart = null;

function showToast(msg, type='success') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'toast show ' + type;
  setTimeout(() => { el.className = 'toast'; }, 3500);
}

function switchTab(name) {
  document.querySelectorAll('.tab').forEach((t, i) => {
    t.classList.toggle('active', ['posts','alerts'][i] === name);
  });
  document.getElementById('tab-posts').classList.toggle('active', name === 'posts');
  document.getElementById('tab-alerts').classList.toggle('active', name === 'alerts');
}

async function api(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Request failed');
  }
  return res.json();
}

async function loadProjects() {
  try {
    const projects = await api('GET', '/projects');
    const el = document.getElementById('projects-list');
    if (!projects.length) {
      el.innerHTML = '<div class="empty-state" style="padding:20px;font-size:12px;">No projects yet</div>';
      return;
    }
    el.innerHTML = projects.map(p => `
      <div class="project-item ${activeProjectId === p.id ? 'active' : ''}" onclick="selectProject(${p.id}, '${escHtml(p.name)}')">
        <h4>${escHtml(p.name)}</h4>
        <div class="meta">${p.post_count} posts &bull; ${p.keywords.slice(0,3).join(', ')}</div>
      </div>
    `).join('');
  } catch(e) { console.error(e); }
}

async function createProject() {
  const name = document.getElementById('proj-name').value.trim();
  const kw = document.getElementById('proj-keywords').value.trim();
  const src = document.getElementById('proj-sources').value.trim();
  if (!name || !kw) { showToast('Name and keywords are required', 'error'); return; }
  try {
    const keywords = kw.split(',').map(s => s.trim()).filter(Boolean);
    const sources = src.split(',').map(s => s.trim()).filter(Boolean);
    const p = await api('POST', '/projects', { name, keywords, sources });
    document.getElementById('proj-name').value = '';
    document.getElementById('proj-keywords').value = '';
    document.getElementById('proj-sources').value = '';
    showToast('Project created: ' + p.name);
    await loadProjects();
    selectProject(p.id, p.name);
  } catch(e) { showToast(e.message, 'error'); }
}

function selectProject(id, name) {
  activeProjectId = id;
  document.querySelectorAll('.project-item').forEach(el => {
    el.classList.toggle('active', el.onclick.toString().includes(',' + id + ','));
  });
  const bar = document.getElementById('ingest-bar');
  bar.style.display = 'flex';
  document.getElementById('ingest-project-label').textContent = 'Monitoring: ' + name;
  loadData();
}

async function ingestPosts() {
  if (!activeProjectId) return;
  const btn = document.getElementById('ingest-btn');
  const spinner = document.getElementById('spinner');
  btn.disabled = true;
  spinner.style.display = 'block';
  try {
    const result = await api('POST', '/ingest', { project_id: activeProjectId });
    showToast(`Ingested ${result.posts_ingested} posts, ${result.alerts_created} alerts (${result.mode} mode)`);
    await loadData();
    await loadProjects();
  } catch(e) { showToast(e.message, 'error'); }
  finally { btn.disabled = false; spinner.style.display = 'none'; }
}

async function loadData() {
  await Promise.all([loadPosts(), loadAlerts(), loadStats()]);
}

async function loadPosts() {
  try {
    const url = activeProjectId ? '/posts?project_id=' + activeProjectId : '/posts';
    const posts = await api('GET', url);
    renderPosts(posts);
  } catch(e) { console.error(e); }
}

async function loadAlerts() {
  try {
    const url = activeProjectId ? '/alerts?project_id=' + activeProjectId : '/alerts';
    const alerts = await api('GET', url);
    renderAlerts(alerts);
  } catch(e) { console.error(e); }
}

async function loadStats() {
  try {
    const url = activeProjectId ? '/stats?project_id=' + activeProjectId : '/stats';
    const stats = await api('GET', url);
    document.getElementById('stat-total').textContent = stats.total_posts;
    document.getElementById('stat-adverse').textContent = stats.adverse_events;
    document.getElementById('stat-positive').textContent = stats.positive_posts;
    document.getElementById('stat-negative').textContent = stats.negative_posts;
    if (stats.chart_data && stats.chart_data.length > 0) {
      document.getElementById('chart-section').style.display = 'block';
      renderChart(stats.chart_data);
    }
  } catch(e) { console.error(e); }
}

function renderChart(data) {
  const ctx = document.getElementById('sentimentChart').getContext('2d');
  const labels = data.map(d => d.time);
  const values = data.map(d => d.sentiment);
  if (sentimentChart) sentimentChart.destroy();
  sentimentChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Sentiment Score',
        data: values,
        borderColor: '#6c63ff',
        backgroundColor: 'rgba(108,99,255,0.1)',
        borderWidth: 2,
        pointRadius: 4,
        pointBackgroundColor: values.map(v => v > 0.1 ? '#00d4aa' : v < -0.1 ? '#ff4d6d' : '#ffb347'),
        tension: 0.3,
        fill: true
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: '#2e3354' }, ticks: { color: '#8b90a7', maxRotation: 0, maxTicksLimit: 8 } },
        y: {
          grid: { color: '#2e3354' },
          ticks: { color: '#8b90a7' },
          min: -1, max: 1,
          title: { display: true, text: 'Polarity', color: '#8b90a7', font: { size: 11 } }
        }
      }
    }
  });
}

function sentimentBadge(label) {
  if (label === 'positive') return '<span class="badge badge-pos">positive</span>';
  if (label === 'negative') return '<span class="badge badge-neg">negative</span>';
  return '<span class="badge badge-neu">neutral</span>';
}

function renderEntities(entities) {
  if (!entities || !entities.length) return '<span style="color:var(--muted);font-size:11px;">none</span>';
  return entities.slice(0, 5).map(e =>
    '<span class="entity-tag entity-' + e.type + '">' + escHtml(e.value) + '</span>'
  ).join('');
}

function scoreBar(score) {
  const pct = Math.round(((score + 1) / 2) * 100);
  const color = score > 0.1 ? 'var(--positive)' : score < -0.1 ? 'var(--danger)' : 'var(--warn)';
  return `<div class="score-bar"><span style="font-size:12px;width:40px;text-align:right;color:${color}">${score > 0 ? '+' : ''}${score.toFixed(2)}</span>
    <div class="score-bar-track"><div class="score-bar-fill" style="width:${pct}%;background:${color}"></div></div>
  </div>`;
}

function renderPosts(posts) {
  const el = document.getElementById('posts-container');
  if (!posts.length) {
    el.innerHTML = '<div class="empty-state"><div class="icon">&#128196;</div>No posts yet. Click "Fetch Posts" to begin.</div>';
    return;
  }
  el.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Post</th>
          <th>Source</th>
          <th>Sentiment</th>
          <th>Entities</th>
          <th>Flag</th>
        </tr>
      </thead>
      <tbody>
        ${posts.map(p => `
          <tr>
            <td style="max-width:320px">
              <div style="font-weight:700;font-size:13px;margin-bottom:4px">
                <a href="${escHtml(p.url)}" target="_blank" style="color:var(--text);text-decoration:none;">${escHtml(p.title)}</a>
              </div>
              <div style="font-size:11px;color:var(--muted)">${escHtml(p.body)}</div>
            </td>
            <td><span style="font-size:12px;color:var(--accent)">${escHtml(p.source)}</span><br/><span style="font-size:11px;color:var(--muted)">u/${escHtml(p.author)}</span></td>
            <td style="min-width:140px">${sentimentBadge(p.sentiment_label)}<br/>${scoreBar(p.sentiment_score)}</td>
            <td style="min-width:160px">${renderEntities(p.entities)}</td>
            <td>${p.is_adverse_event ? '<span class="badge badge-adverse">&#9888; AE</span>' : ''}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
}

function renderAlerts(alerts) {
  const el = document.getElementById('alerts-container');
  if (!alerts.length) {
    el.innerHTML = '<div class="empty-state"><div class="icon">&#10003;</div>No adverse event alerts — looking good!</div>';
    return;
  }
  el.innerHTML = alerts.map(a => `
    <div class="alert-card">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px">
        <h4><a href="${escHtml(a.post.url)}" target="_blank" style="color:var(--text);text-decoration:none;">${escHtml(a.post.title)}</a></h4>
        ${sentimentBadge(a.post.sentiment_label)}
      </div>
      <p>${escHtml(a.post.body)}</p>
      <div class="reason">&#9888; Triggered by: ${escHtml(a.reason)}</div>
      <div class="alert-meta">
        <span>${escHtml(a.post.source)}</span>
        <span>u/${escHtml(a.post.author)}</span>
        <span>${new Date(a.post.ingested_at).toLocaleString()}</span>
      </div>
    </div>
  `).join('');
}

function escHtml(str) {
  if (!str) return '';
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#039;');
}

function exportCSV(type) {
  const base = type === 'posts' ? '/export/posts' : '/export/alerts';
  const url = activeProjectId ? base + '?project_id=' + activeProjectId : base;
  const a = document.createElement('a');
  a.href = url;
  a.download = '';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  showToast('Downloading ' + type + ' CSV...');
}

// Initial load
loadProjects();
loadStats();
loadPosts();
loadAlerts();
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML_TEMPLATE


# ─────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 5000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
