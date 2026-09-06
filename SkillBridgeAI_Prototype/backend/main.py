from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import pandas as pd
import numpy as np
import re
import os
import io
import json
import hmac
import base64
import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.metrics import roc_auc_score
from groq import Groq

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / 'data'
DATABASE_PATH = Path(os.getenv('DATABASE_PATH', BASE / 'skillbridge.db'))
SECRET_KEY = os.getenv('SECRET_KEY', 'change-this-local-development-secret')
TOKEN_TTL_HOURS = int(os.getenv('TOKEN_TTL_HOURS', '168'))
groq_client = Groq(api_key=os.getenv('GROQ_API_KEY'))
GROQ_MODEL = 'llama-3.3-70b-versatile'

JOBS_FILE = DATA / 'job_postings_cleaned.xlsx'
JOB_SKILLS_FILE = DATA / 'job_skills_cleaned.xlsx'
COURSES_FILE = DATA / 'coursera_course_dataset_cleaned.xlsx'
STUDENTS_FILE = DATA / 'student_career_success_dataset_cleaned (1).xlsx'

FUNCTIONS = {
    'IT':'Information Technology','SALE':'Sales','MGMT':'Management','MNFC':'Manufacturing',
    'BD':'Business Development','ENG':'Engineering','OTHR':'Other','HCPR':'Healthcare & Public Relations',
    'FIN':'Finance','ACCT':'Accounting','MRKT':'Marketing','PRJM':'Project Management','ADM':'Administration',
    'ANLS':'Analytics','RSCH':'Research','HR':'Human Resources','CUST':'Customer Service','DSGN':'Design',
    'EDU':'Education','ART':'Arts','LGL':'Legal','CNSL':'Consulting','GENB':'General Business','PR':'Public Relations',
    'WRT':'Writing','QA':'Quality Assurance','ADVR':'Advertising','STRA':'Strategy','TRNG':'Training','SUPL':'Supply Chain',
    'PRDM':'Product Management','SCI':'Science','PRCH':'Procurement','PROD':'Production','DIST':'Distribution'
}

print('Loading SkillBridge data...')
jobs = pd.read_excel(JOBS_FILE)
job_skills_raw = pd.read_excel(JOB_SKILLS_FILE)
courses = pd.read_excel(COURSES_FILE)
students = pd.read_excel(STUDENTS_FILE)

# Clean common fields
for col in ['title','description','location','formatted_experience_level','formatted_work_type']:
    if col in jobs.columns:
        jobs[col] = jobs[col].fillna('').astype(str)

courses['Skills'] = courses['Skills'].fillna('').astype(str)
courses['Title'] = courses['Title'].fillna('').astype(str)
courses['Organization'] = courses['Organization'].fillna('').astype(str)

# Course skill taxonomy. We intentionally use course skills as the canonical vocabulary,
# then extract matching skills from job descriptions.
def split_skills(value):
    return [x.strip() for x in re.split(r'[,;|]', str(value)) if x.strip()]

course_skill_counts = {}
for s in courses['Skills']:
    for token in split_skills(s):
        key = re.sub(r'\s+', ' ', token.strip())
        if len(key) >= 3:
            course_skill_counts[key] = course_skill_counts.get(key, 0) + 1

# Remove extremely generic/non-actionable terms from the job matching vocabulary.
NOISY = {
    'Business','Management','Strategy','Leadership','Research','Communication','Writing',
    'Problem Solving','Critical Thinking','Planning','Operations','Marketing','Finance',
    'Sales','English Language','Presentations','Decision Making','Collaboration'
}
canonical_skills = sorted(
    [s for s in course_skill_counts if s not in NOISY and len(s) >= 3],
    key=len, reverse=True
)

# Aliases make the demo more useful without requiring a heavy NLP service.
ALIASES = {
    'python programming':'Python', 'python scripting':'Python', 'python':'Python',
    'sql':'SQL', 'mysql':'SQL', 'postgresql':'SQL',
    'microsoft excel':'Excel', 'spreadsheet software':'Excel', 'excel':'Excel',
    'power bi':'Power BI', 'powerbi':'Power BI',
    'machine learning':'Machine Learning', 'deep learning':'Deep Learning',
    'data visualization':'Data Visualization', 'data analysis':'Data Analysis',
    'statistics':'Statistics', 'statistical analysis':'Statistics',
    'aws':'AWS', 'amazon web services':'AWS', 'cloud computing':'Cloud Computing',
    'computer programming':'Programming', 'programming':'Programming',
    'database':'Databases', 'databases':'Databases',
    'network security':'Network Security', 'cybersecurity':'Cybersecurity',
    'project management':'Project Management', 'business analysis':'Business Analysis',
    'tableau':'Tableau', 'r programming':'R Programming', 'java':'Java',
    'javascript':'JavaScript', 'c++':'C++', 'c#':'C#'
}

# Build a compact, fast prototype vocabulary. We use the most common course skills plus aliases.
# A production version can replace this with a transformer/NER pipeline.
base_vocab = sorted(course_skill_counts.items(), key=lambda x: (-x[1], -len(x[0])))[:100]
manual = list(ALIASES.keys())
match_terms = sorted(set([x[0] for x in base_vocab] + manual), key=len, reverse=True)
_PATTERN_MAP = {r.lower(): ALIASES.get(r.lower(), r) for r in match_terms}
# Require a term boundary on either side of a skill.  Without this, "Java" is
# also found in "JavaScript", and other short skills can be counted inside
# unrelated words.
SKILL_PATTERNS = {
    raw.lower(): re.compile(r'(?<!\\w)' + re.escape(raw.lower()) + r'(?!\\w)')
    for raw in match_terms
}

@lru_cache(maxsize=20000)
def extract_skills(text: str):
    t = (text or '').lower()
    found = []
    seen = set()
    for raw in match_terms:
        r = raw.lower()
        if SKILL_PATTERNS[r].search(t):
            canon = _PATTERN_MAP[r]
            if canon not in seen:
                seen.add(canon); found.append(canon)
    return tuple(found)

# Pre-compute only once at startup. 15k rows is small enough for this prototype.
print('Extracting skills from job descriptions...')
jobs['extracted_skills'] = jobs['description'].map(extract_skills)

# Job function mapping from supplied job_skills file.
job_function_map = job_skills_raw.copy()
job_function_map['skill_abr'] = job_function_map['skill_abr'].fillna('').astype(str).str.upper()
job_function_map['function_name'] = job_function_map['skill_abr'].map(FUNCTIONS).fillna(job_function_map['skill_abr'])

job_to_functions = job_function_map.groupby('job_id')['function_name'].apply(lambda x: sorted(set(x))).to_dict()

# Course skills normalized
courses['skill_list'] = courses['Skills'].map(lambda x: [ALIASES.get(s.lower(), s) for s in split_skills(x)])

# Placement model: deliberately uses only pre-outcome features.
MODEL_FEATURES = [
    'CGPA','Attendance_Percentage','Study_Hours_Per_Week','Programming_Skill',
    'Projects_Completed','Certifications','Hackathons','Internships',
    'Leadership_Experience','Resume_Score','Communication_Skills','Teamwork',
    'Problem_Solving','English_Proficiency','Interview_Score'
]
X = students[MODEL_FEATURES].apply(pd.to_numeric, errors='coerce').fillna(0)
y = students['Placement_Status'].astype(str).str.strip().str.lower().map({'placed':1,'not placed':0})
valid = y.notna()
X, y = X.loc[valid], y.loc[valid]
# Report validation quality from unseen records, then fit the served model on
# all available training data.  Measuring on the same rows used for fitting
# materially overstates model quality.
model = Pipeline([('scale', StandardScaler()), ('clf', LogisticRegression(max_iter=1000))])
try:
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    validation_model = Pipeline([
        ('scale', StandardScaler()), ('clf', LogisticRegression(max_iter=1000))
    ])
    validation_model.fit(X_train, y_train)
    MODEL_AUC = float(roc_auc_score(y_test, validation_model.predict_proba(X_test)[:, 1]))
except Exception:
    MODEL_AUC = None
model.fit(X, y)

# Aggregates
TOTAL_JOBS = int(len(jobs))
TOTAL_COURSES = int(len(courses))
TOTAL_STUDENTS = int(len(students))
PLACEMENT_RATE = round(float((students['Placement_Status'].astype(str).str.lower() == 'placed').mean()) * 100, 1)


ALL_ROLE_TITLES = sorted(jobs['title'].str.strip().replace('', np.nan).dropna().unique().tolist(), key=len, reverse=True)

def detect_role_in_text(text: str) -> str | None:
    t = text.lower()
    for title in ALL_ROLE_TITLES:
        if title.lower() in t:
            return title
    return None


def role_rows(role: str):
    q = role.strip().lower()
    mask = jobs['title'].str.lower().str.contains(re.escape(q), na=False)
    subset = jobs.loc[mask]
    if subset.empty:
        # token fallback: any important word in title
        tokens = [t for t in re.findall(r'[a-zA-Z]{3,}', q) if t not in {'and','the','for'}]
        if tokens:
            mask = jobs['title'].str.lower().apply(lambda s: all(t in s for t in tokens))
            subset = jobs.loc[mask]
    return subset


def demand_for_role(subset: pd.DataFrame):
    if subset.empty:
        return []
    counts = {}
    for skills in subset['extracted_skills']:
        for s in skills:
            counts[s] = counts.get(s, 0) + 1
    total = len(subset)
    rows = [{'skill': s, 'demand_percent': round(c/total*100,1), 'job_count': int(c)} for s,c in counts.items()]
    rows.sort(key=lambda x: (-x['demand_percent'], -x['job_count'], x['skill']))
    return rows[:15]


def db_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_database():
    with db_connection() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS assessments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                target_role TEXT NOT NULL,
                placement_probability REAL NOT NULL,
                readiness_score REAL NOT NULL,
                skill_gaps TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
        ''')


def password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 210_000).hex()
    return f'{salt}${digest}'


def password_matches(password: str, stored: str) -> bool:
    try:
        salt, expected = stored.split('$', 1)
        return hmac.compare_digest(password_hash(password, salt), stored)
    except ValueError:
        return False


def create_token(user_id: int) -> str:
    payload = json.dumps({
        'sub': user_id,
        'exp': int((datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_HOURS)).timestamp())
    }, separators=(',', ':')).encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip('=')
    signature = hmac.new(SECRET_KEY.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return f'{encoded}.{signature}'


def user_from_authorization(authorization: str | None) -> int | None:
    if not authorization or not authorization.startswith('Bearer '):
        return None
    try:
        encoded, signature = authorization.removeprefix('Bearer ').split('.', 1)
        expected = hmac.new(SECRET_KEY.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
        if payload['exp'] < int(datetime.now(timezone.utc).timestamp()):
            return None
        return int(payload['sub'])
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


def require_user(authorization: str | None) -> int:
    user_id = user_from_authorization(authorization)
    if user_id is None:
        raise HTTPException(status_code=401, detail='Sign in to access saved progress.')
    return user_id


def filtered_role_rows(role: str, location: str = '', experience: str = '', work_type: str = '', min_salary: float | None = None):
    subset = role_rows(role)
    if location.strip():
        subset = subset[subset['location'].str.lower().str.contains(re.escape(location.strip().lower()), na=False)]
    if experience.strip():
        subset = subset[subset['formatted_experience_level'].str.lower().str.contains(re.escape(experience.strip().lower()), na=False)]
    if work_type.strip():
        subset = subset[subset['formatted_work_type'].str.lower().str.contains(re.escape(work_type.strip().lower()), na=False)]
    if min_salary is not None and 'med_salary_inr' in subset:
        salaries = pd.to_numeric(subset['med_salary_inr'], errors='coerce')
        subset = subset.loc[salaries >= min_salary]
    return subset


initialize_database()
app = FastAPI(title='SkillBridge AI API', version='1.0.0')
cors_origins = [origin.strip() for origin in os.getenv('CORS_ORIGINS', 'http://127.0.0.1:5500,http://localhost:5500').split(',') if origin.strip()]
app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_credentials=True, allow_methods=['*'], allow_headers=['*'])

class Assessment(BaseModel):
    cgpa: float = Field(3.0, ge=0, le=4)
    attendance: float = Field(80, ge=0, le=100)
    study_hours: float = Field(10, ge=0, le=80)
    programming: float = Field(6, ge=0, le=10)
    projects: float = Field(3, ge=0, le=20)
    certifications: float = Field(1, ge=0, le=10)
    hackathons: float = Field(1, ge=0, le=10)
    internships: float = Field(1, ge=0, le=10)
    leadership: float = Field(5, ge=0, le=10)
    resume_score: float = Field(70, ge=0, le=100)
    communication: float = Field(7, ge=0, le=10)
    teamwork: float = Field(7, ge=0, le=10)
    problem_solving: float = Field(7, ge=0, le=10)
    english: float = Field(7, ge=0, le=10)
    interview_score: float = Field(70, ge=0, le=100)
    target_role: str = 'Data Analyst'


class Credentials(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=128)


class LearningPathRequest(BaseModel):
    target_role: str = Field(min_length=2, max_length=120)
    current_skills: list[str] = Field(default_factory=list)
    weeks_available: int = Field(8, ge=1, le=52)


class ChatMessage(BaseModel):
    role: str  # 'user' or 'assistant'
    content: str = Field(max_length=2000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    history: list[ChatMessage] = Field(default_factory=list)

@app.get('/api/health')
def health():
    return {'status':'ok','message':'SkillBridge AI backend is running'}

@app.get('/api/overview')
def overview():
    top_functions = (job_function_map['function_name'].value_counts().head(8).reset_index())
    top_functions.columns = ['function','records']
    top_roles = jobs['title'].str.strip().replace('', np.nan).dropna().value_counts().head(8).reset_index()
    top_roles.columns = ['role','jobs']
    return {
        'jobs': TOTAL_JOBS,
        'courses': TOTAL_COURSES,
        'students': TOTAL_STUDENTS,
        'placement_rate': PLACEMENT_RATE,
        'model_auc': round(MODEL_AUC, 3) if MODEL_AUC is not None else None,
        'top_functions': top_functions.to_dict('records'),
        'top_roles': top_roles.to_dict('records'),
        'skill_taxonomy_size': len(set(ALIASES.values()) | set(canonical_skills))
    }

@app.get('/api/roles')
def roles(limit: int = Query(50, ge=1, le=200), location: str = '', experience: str = '', work_type: str = '', min_salary: float | None = Query(None, ge=0)):
    subset = jobs
    if location.strip(): subset = subset[subset['location'].str.lower().str.contains(re.escape(location.strip().lower()), na=False)]
    if experience.strip(): subset = subset[subset['formatted_experience_level'].str.lower().str.contains(re.escape(experience.strip().lower()), na=False)]
    if work_type.strip(): subset = subset[subset['formatted_work_type'].str.lower().str.contains(re.escape(work_type.strip().lower()), na=False)]
    if min_salary is not None and 'med_salary_inr' in subset:
        subset = subset.loc[pd.to_numeric(subset['med_salary_inr'], errors='coerce') >= min_salary]
    vals = subset['title'].str.strip().replace('', np.nan).dropna().value_counts().head(limit)
    return [{'role': idx, 'jobs': int(count)} for idx,count in vals.items()]

@app.get('/api/functions')
def functions():
    vals = job_function_map['function_name'].value_counts()
    return [{'function': idx, 'records': int(count)} for idx,count in vals.items()]

@app.get('/api/role-analysis')
def role_analysis(role: str, location: str = '', experience: str = '', work_type: str = '', min_salary: float | None = Query(None, ge=0)):
    subset = filtered_role_rows(role, location, experience, work_type, min_salary)
    demand = demand_for_role(subset)
    median_salary = pd.to_numeric(subset['med_salary_inr'], errors='coerce').median() if 'med_salary_inr' in subset else np.nan
    experiences = subset['formatted_experience_level'].replace('', np.nan).dropna().value_counts().head(5).to_dict()
    functions = {}
    for jid in subset['job_id'].tolist():
        for fn in job_to_functions.get(jid, []): functions[fn] = functions.get(fn,0)+1
    top_fn = sorted(functions.items(), key=lambda x:-x[1])[:5]
    return {
        'role': role,
        'location': location,
        'filters': {'experience': experience, 'work_type': work_type, 'min_salary': min_salary},
        'job_count': int(len(subset)),
        'median_salary_inr': None if pd.isna(median_salary) else round(float(median_salary),0),
        'experience_distribution': [{'level':k,'jobs':int(v)} for k,v in experiences.items()],
        'top_functions': [{'function':k,'jobs':int(v)} for k,v in top_fn],
        'skills': demand,
        'data_note': 'Current job postings are a cross-sectional sample; use skill prevalence rather than year-over-year growth.'
    }

def score_courses(role: str, limit: int = 8, location: str = '', experience: str = '', work_type: str = '', min_salary: float | None = None):
    subset = filtered_role_rows(role, location, experience, work_type, min_salary)
    demand = demand_for_role(subset)
    demand_map = {x['skill'].lower(): x['demand_percent']/100 for x in demand}
    if not demand_map:
        # fallback to broad popular skills
        demand_map = {s.lower(): min(0.5, c/10) for s,c in list(course_skill_counts.items())[:15]}
    scored = []
    for idx,row in courses.iterrows():
        skills = row['skill_list']
        if not skills: continue
        score_parts = [demand_map[s.lower()] for s in skills if s.lower() in demand_map]
        alignment = float(np.mean(score_parts)) if score_parts else 0.0
        coverage = len(score_parts) / max(1,len(demand_map))
        rating = float(row['Ratings']) if pd.notna(row['Ratings']) else 0
        rating_norm = min(max(rating/5,0),1)
        final = 0.75*coverage + 0.15*alignment + 0.10*rating_norm
        scored.append({
            'title': row['Title'], 'organization': row['Organization'],
            'difficulty': row['Difficulty_Level'], 'duration': row['Duration'],
            'rating': None if pd.isna(row['Ratings']) else round(float(row['Ratings']),2),
            'alignment_percent': round(final*100,1),
            'matched_skills': [s for s in skills if s.lower() in demand_map][:8]
        })
    scored.sort(key=lambda x:x['alignment_percent'], reverse=True)
    return scored[:limit]


@app.get('/api/courses')
def course_recommendations(role: str, limit: int = Query(8, ge=1, le=20), location: str = '', experience: str = '', work_type: str = '', min_salary: float | None = Query(None, ge=0)):
    return score_courses(role, limit, location, experience, work_type, min_salary)

@app.post('/api/assessment')
def assessment(a: Assessment, authorization: str | None = Header(default=None)):
    row = pd.DataFrame([{
        'CGPA':a.cgpa,'Attendance_Percentage':a.attendance,'Study_Hours_Per_Week':a.study_hours,
        'Programming_Skill':a.programming,'Projects_Completed':a.projects,'Certifications':a.certifications,
        'Hackathons':a.hackathons,'Internships':a.internships,'Leadership_Experience':a.leadership,
        'Resume_Score':a.resume_score,'Communication_Skills':a.communication,'Teamwork':a.teamwork,
        'Problem_Solving':a.problem_solving,'English_Proficiency':a.english,'Interview_Score':a.interview_score
    }])[MODEL_FEATURES]
    probability = float(model.predict_proba(row)[0,1])
    # Simple transparent readiness components for UI.
    technical = np.mean([a.programming/10, min(a.projects/10,1), min(a.internships/5,1)])
    soft = np.mean([a.communication/10,a.teamwork/10,a.problem_solving/10,a.english/10])
    interview = a.interview_score/100
    readiness = round((0.45*technical + 0.30*soft + 0.25*interview)*100)
    role = a.target_role.strip() or 'Data Analyst'
    demand = demand_for_role(role_rows(role))
    gaps=[]
    for item in demand[:8]:
        # Map broad programming/analytics demand to available assessment dimensions.
        s=item['skill'].lower()
        if any(k in s for k in ['python','java','javascript','programming','c++','c#']): score=a.programming*10
        elif 'communication' in s: score=a.communication*10
        elif 'problem' in s: score=a.problem_solving*10
        elif 'excel' in s: score=min(a.programming*10+15,100)
        else: score=readiness
        gap=max(0, round(100-score))
        if gap>=20: gaps.append({'skill':item['skill'],'gap_percent':gap,'priority':'High' if gap>=40 else 'Medium'})
    gaps=sorted(gaps,key=lambda x:-x['gap_percent'])[:5]
    result = {
        'placement_probability': round(probability*100,1),
        'readiness_score': readiness,
        'technical_score': round(technical*100), 'soft_skill_score':round(soft*100),
        'interview_score': round(interview*100), 'target_role':role,
        'skill_gaps':gaps,
        'model_note':'Estimated from the supplied student dataset; not a hiring guarantee.'
    }
    user_id = user_from_authorization(authorization)
    if user_id is not None:
        with db_connection() as conn:
            conn.execute(
                'INSERT INTO assessments (user_id, target_role, placement_probability, readiness_score, skill_gaps, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                (user_id, role, result['placement_probability'], result['readiness_score'], json.dumps(gaps), datetime.now(timezone.utc).isoformat())
            )
        result['saved'] = True
    else:
        result['saved'] = False
    return result


@app.post('/api/auth/register')
def register(credentials: Credentials):
    email = credentials.email.strip().lower()
    if '@' not in email:
        raise HTTPException(status_code=422, detail='Enter a valid email address.')
    try:
        with db_connection() as conn:
            cursor = conn.execute(
                'INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)',
                (email, password_hash(credentials.password), datetime.now(timezone.utc).isoformat())
            )
            user_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail='An account with this email already exists.')
    return {'token': create_token(user_id), 'email': email}


@app.post('/api/auth/login')
def login(credentials: Credentials):
    email = credentials.email.strip().lower()
    with db_connection() as conn:
        user = conn.execute('SELECT id, password_hash FROM users WHERE email = ?', (email,)).fetchone()
    if user is None or not password_matches(credentials.password, user['password_hash']):
        raise HTTPException(status_code=401, detail='Invalid email or password.')
    return {'token': create_token(user['id']), 'email': email}


@app.get('/api/profile/assessments')
def saved_assessments(authorization: str | None = Header(default=None)):
    user_id = require_user(authorization)
    with db_connection() as conn:
        rows = conn.execute(
            'SELECT target_role, placement_probability, readiness_score, skill_gaps, created_at FROM assessments WHERE user_id = ? ORDER BY id DESC LIMIT 25',
            (user_id,)
        ).fetchall()
    return [{**dict(row), 'skill_gaps': json.loads(row['skill_gaps'])} for row in rows]


@app.get('/api/ai-greeting')
def ai_greeting(authorization: str | None = Header(default=None)):
    user_id = require_user(authorization)
    with db_connection() as conn:
        latest = conn.execute(
            'SELECT target_role, placement_probability, readiness_score, skill_gaps, created_at '
            'FROM assessments WHERE user_id = ? ORDER BY id DESC LIMIT 1',
            (user_id,)
        ).fetchone()

    if latest is None:
        return {
            'greeting': "Welcome to SkillBridge AI! Take your first skill assessment and I'll "
                        "start giving you personalized guidance on where to focus your learning."
        }

    gaps = json.loads(latest['skill_gaps'])
    gap_summary = ', '.join(f"{g['skill']} ({g['gap_percent']}% gap)" for g in gaps[:3]) or 'no major gaps detected'

    prompt = f"""You're an encouraging skill-development coach for a platform called SkillBridge AI.

This student's most recent assessment:
- Target role: {latest['target_role']}
- Readiness score: {latest['readiness_score']}%
- Placement probability: {latest['placement_probability']}%
- Top skill gaps: {gap_summary}

Write a short, warm welcome-back message (2-3 sentences). Mention their readiness score,
name their single biggest skill gap, and give one encouraging, concrete suggestion for what
to focus on next. No headers, no bullet points, casual and motivating tone."""

    message = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        max_tokens=200,
        messages=[{'role': 'user', 'content': prompt}]
    )
    return {'greeting': message.choices[0].message.content}


@app.post('/api/chat')
def chat(req: ChatRequest, authorization: str | None = Header(default=None)):
    user_id = require_user(authorization)

    # Pull the student's real latest assessment for context.
    with db_connection() as conn:
        latest = conn.execute(
            'SELECT target_role, readiness_score, skill_gaps FROM assessments '
            'WHERE user_id = ? ORDER BY id DESC LIMIT 1', (user_id,)
        ).fetchone()

    context_parts = []
    if latest:
        gaps = json.loads(latest['skill_gaps'])
        gap_text = ', '.join(f"{g['skill']} ({g['gap_percent']}% gap)" for g in gaps[:5]) or 'none recorded'
        context_parts.append(
            f"Student's last assessment: target role '{latest['target_role']}', "
            f"readiness {latest['readiness_score']}%, skill gaps: {gap_text}."
        )
    else:
        context_parts.append("This student has not completed an assessment yet.")

    # If the user's message mentions a specific role, pull REAL demand + course data for it.
    detected_role = detect_role_in_text(req.message)
    if detected_role:
        demand = demand_for_role(role_rows(detected_role))[:8]
        top_courses = score_courses(detected_role, limit=3)
        if demand:
            skill_text = ', '.join(f"{d['skill']} ({d['demand_percent']}% of postings)" for d in demand)
            context_parts.append(f"Real job-posting data for '{detected_role}': top in-demand skills are {skill_text}.")
        if top_courses:
            course_text = '; '.join(f"{c['title']} by {c['organization']} ({c['alignment_percent']}% match)" for c in top_courses)
            context_parts.append(f"Top matching courses for '{detected_role}': {course_text}.")

    system_prompt = (
        "You are the SkillBridge AI assistant, a career-guidance chat helper for students. "
        "Only use the factual data given to you below about this student and about job/course data. "
        "Do not invent job requirements, statistics, or course names that are not provided to you. "
        "If you don't have real data for something the student asks, say so honestly and suggest "
        "they try a specific role name instead. Keep answers short (3-5 sentences), warm, and practical.\n\n"
        "FACTS YOU CAN USE:\n" + "\n".join(context_parts)
    )

    groq_messages = [{'role': 'system', 'content': system_prompt}]
    groq_messages += [{'role': m.role, 'content': m.content} for m in req.history]
    groq_messages.append({'role': 'user', 'content': req.message})

    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        max_tokens=400,
        messages=groq_messages
    )
    return {'reply': response.choices[0].message.content, 'detected_role': detected_role}


def read_resume_text(filename: str, content: bytes) -> str:
    suffix = Path(filename or '').suffix.lower()
    if suffix == '.pdf':
        from pypdf import PdfReader
        return '\n'.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(content)).pages)
    if suffix == '.docx':
        from docx import Document
        return '\n'.join(paragraph.text for paragraph in Document(io.BytesIO(content)).paragraphs)
    return content.decode('utf-8', errors='ignore')


@app.post('/api/resume-analysis')
async def resume_analysis(
    file: UploadFile = File(...),
    target_role: str = Form(...)
):
    content = await file.read()
    if len(content) > 5_000_000:
        raise HTTPException(status_code=413, detail='Please upload a résumé smaller than 5 MB.')
    if Path(file.filename or '').suffix.lower() not in {'.pdf', '.docx', '.txt'}:
        raise HTTPException(status_code=415, detail='Upload a PDF, DOCX, or TXT résumé.')
    try:
        text = read_resume_text(file.filename or '', content)
    except Exception:
        raise HTTPException(status_code=422, detail='The résumé could not be read. Try exporting it as a text-based PDF or DOCX.')
    detected = list(extract_skills(text))
    demand = demand_for_role(role_rows(target_role))
    detected_normalized = {skill.lower() for skill in detected}
    gaps = [item for item in demand if item['skill'].lower() not in detected_normalized][:8]
    return {'target_role': target_role, 'detected_skills': detected, 'skill_gaps': gaps, 'text_length': len(text)}


@app.post('/api/learning-path')
def learning_path(request: LearningPathRequest):
    demand = demand_for_role(role_rows(request.target_role))
    existing = {ALIASES.get(skill.strip().lower(), skill.strip()).lower() for skill in request.current_skills if skill.strip()}
    priorities = [item for item in demand if item['skill'].lower() not in existing][:4]
    weeks_each = max(1, round(request.weeks_available / max(1, len(priorities))))
    steps = []
    for index, priority in enumerate(priorities, start=1):
        matching = courses[courses['skill_list'].map(lambda skills: priority['skill'].lower() in {s.lower() for s in skills})]
        course = matching.sort_values('Ratings', ascending=False).iloc[0] if not matching.empty else None
        steps.append({
            'week_start': 1 + (index - 1) * weeks_each,
            'week_end': min(request.weeks_available, index * weeks_each),
            'focus_skill': priority['skill'],
            'why': f"Appears in {priority['demand_percent']}% of matching job postings.",
            'course': None if course is None else {
                'title': course['Title'], 'organization': course['Organization'],
                'duration': course['Duration'], 'rating': None if pd.isna(course['Ratings']) else round(float(course['Ratings']), 2)
            }
        })
    return {'target_role': request.target_role, 'weeks_available': request.weeks_available, 'steps': steps, 'note': 'Use this as a study guide, not as a guarantee of placement.'}

if __name__ == '__main__':
    import uvicorn
    uvicorn.run('main:app', host='127.0.0.1', port=8000, reload=True)
