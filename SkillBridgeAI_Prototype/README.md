# SkillBridge AI — Connected Working Prototype

This package connects the provided SkillBridge frontend to the four supplied datasets.

## Folder structure

- `frontend/` — your original `index.html` + `style.css`, plus `app.js` for API integration.
- `backend/` — FastAPI API, skill extraction, course matching and student placement model.
- `data/` — the four Excel datasets used by the backend.

## Windows quick start

### Terminal 1 — backend
```bat
cd SkillBridgeAI_Prototype
run_backend.bat
```

The API will run at `http://127.0.0.1:8000`.

### Terminal 2 — frontend
```bat
cd SkillBridgeAI_Prototype
run_frontend.bat
```

Open `http://127.0.0.1:5500`.

## Manual start

If you already have Python installed:

```bash
cd backend
python -m pip install -r requirements.txt
python main.py
```

Then in another terminal:

```bash
cd frontend
python -m http.server 5500
```

## Run automated checks

```bash
cd backend
python -m unittest test_main.py
```

## What is connected

1. Hero statistics are loaded from the real datasets.
2. `Start Skill Assessment` opens a live assessment modal.
3. The assessment calls `/api/assessment` and returns a placement estimate + readiness + skill gaps.
4. A new Live Data Intelligence section uses `/api/roles`, `/api/role-analysis` and `/api/courses`.
5. Selecting a job role shows real job count, median salary, extracted industry skills and recommended courses.
6. The backend uses the supplied job-function file as a broad function layer and extracts actual skills from job descriptions using a lightweight normalized vocabulary derived from the course dataset.

## Reliability notes

- The displayed model AUC is evaluated on a held-out, stratified sample before the final model is trained on all supplied student records. It is still an exploratory estimate, not a hiring prediction.
- Skill matching uses case-insensitive word boundaries so closely named technologies (for example, Java and JavaScript) are counted separately.

## API endpoints

- `GET /api/health`
- `GET /api/overview`
- `GET /api/roles`
- `GET /api/functions`
- `GET /api/role-analysis?role=Data%20Analyst&location=`
- `GET /api/courses?role=Data%20Analyst`
- `POST /api/assessment`
- `POST /api/auth/register` and `POST /api/auth/login`
- `GET /api/profile/assessments` (authenticated)
- `POST /api/resume-analysis` (PDF, DOCX, or TXT upload)
- `POST /api/learning-path`
- Interactive API docs: `http://127.0.0.1:8000/docs`

## Added product features

- Create a local account or sign in to save assessment history. The prototype uses a SQLite database (`skillbridge.db`) and securely hashes passwords; change `SECRET_KEY` before deployment.
- Upload a PDF, DOCX, or TXT résumé to extract recognized skills and compare them with a target role.
- Filter job-role analysis by location, experience, work type, and minimum salary.
- Generate a phased learning roadmap from role demand and your current skills.
- Configure allowed frontend origins with `CORS_ORIGINS`, database location with `DATABASE_PATH`, and token lifetime with `TOKEN_TTL_HOURS`.

For production, use HTTPS, a managed database, a secrets manager, email verification/password reset, rate limiting, and a proper identity provider rather than the prototype's local token implementation.

## Important data note

The supplied job-posting sample is cross-sectional, so the prototype reports skill prevalence and demand rather than claiming year-over-year growth. The job-function file contains broad function abbreviations, so actual skill matching is derived from job descriptions and normalized against course skills.
