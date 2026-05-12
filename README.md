# Gordon RamsAI

Streamlit chat app that pairs **fitness and nutrition coaching** with a **Gordon Ramsay–style** voice. Answers come from **Google Gemini**; accounts, profiles, chats, workouts, and meal data live in **Supabase**. **Langfuse** (optional) traces generations and supports message ratings.

## Features

- **Auth** — Register and sign in with Supabase; JWT is applied to the Supabase client so **RLS** policies see `auth.uid()`.
- **Profiles** — `profiles` stores username, allergies, and fitness goals; the app can infer updates from chat.
- **Chats** — Conversations and messages are stored in Supabase (`conversations`, `conversation_messages`); sidebar loads history and supports new chats and deletes.
- **Workouts** — Natural-language lines (and explicit “log this …” style commands) are parsed and written to `workout_logs`; the assistant reacts in character without dry “logged to database” copy.
- **Meals** — Meal suggestions can include a structured block saved to `meal_library` (with allergy-aware search for context).
- **Observability** — Langfuse trace IDs can be stored on messages for ratings and dashboard review.

## Requirements

- Python **3.10+** (the codebase uses modern typing syntax).
- A [Google AI Studio](https://aistudio.google.com/) API key for Gemini.
- A [Supabase](https://supabase.com/) project (URL + anon key, auth enabled, tables/policies as below).
- Optional: [Langfuse](https://langfuse.com/) project keys.

Install dependencies:

```bash
pip install -r requirements.txt
```

**Streamlit Community Cloud** reads `requirements.txt` and optional **`runtime.txt`** (this repo pins **Python 3.12** for consistent builds). Langfuse integration targets the **v4** client (`langfuse>=4,<5`); tracing is best-effort so chat still works if Langfuse fails.

If `google.genai` / `genai.Client` fails to import, ensure **`google-genai`** is installed (see `requirements.txt`).

## Configuration

### 1. Clone and virtual environment

```bash
git clone https://github.com/antonjoshuadominguez/Gordon-RamsAI.git
cd Gordon-RamsAI
python -m venv venv
# Windows: venv\Scripts\activate
# macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
```

### 2. Streamlit secrets

Create **`.streamlit/secrets.toml`** (this path is already gitignored). Do **not** commit real keys.

Example shape:

```toml
[google]
api_key = "YOUR_GEMINI_API_KEY"

[supabase]
url = "https://YOUR_PROJECT.supabase.co"
key = "YOUR_SUPABASE_ANON_KEY"

[langfuse]
public_key = "YOUR_LANGFUSE_PUBLIC_KEY"
secret_key = "YOUR_LANGFUSE_SECRET_KEY"
host = "https://cloud.langfuse.com"
```

- **`[google]`** — Required for chat.
- **`[supabase]`** — Required for login, profiles, chats, workouts, and meal library.
- **`[langfuse]`** — Optional; if missing or invalid, the app runs without tracing.

### 3. Supabase schema (overview)

You need tables compatible with what `auth_utils.py` expects, including (names may vary slightly if you fork the schema):

| Area | Typical tables |
|------|------------------|
| Auth | Supabase Auth users |
| Profile | `profiles` |
| Chats | `conversations`, `conversation_messages` |
| Fitness | `workout_logs` |
| Nutrition | `meal_library` |

Create these tables (and indexes) in Supabase to match `auth_utils.py` and `app.py`, then add **RLS policies** so each user can only read/write their own rows. The app calls `client.auth.set_session(access_token, refresh_token)` after login so inserts respect `auth.uid()`. If you keep migration scripts in-repo, run them from the Supabase SQL editor or your usual migration tool.

## Run locally

```bash
streamlit run app.py
```

Open the URL Streamlit prints (usually `http://localhost:8501`).

## Project layout

| Path | Role |
|------|------|
| `app.py` | Streamlit UI: login, chat, profile, workout parsing, meal record handling |
| `ai.py` | Gemini client, system prompt, Langfuse hooks, rate-limit / error fallbacks |
| `auth_utils.py` | Supabase client, auth helpers, profiles, chats, workouts, meals |
| `sidebar.py` | Conversation list and navigation |
| `.streamlit/secrets.toml` | Local secrets (not in git) |

## Development notes

- **Secrets** — Never commit `.streamlit/secrets.toml`. Rotate keys if they leak.
- **Persona** — The model is instructed to stay in a Ramsay-like fitness/nutrition persona; guardrails block obvious prompt-injection patterns.
- **Workouts** — Parsing is heuristic; odd phrasing may need clearer numbers (`kg`, `reps`, `sets`) or an explicit “log this: …” line.

## License

Add a `LICENSE` file if you intend to open-source this repository; until then, all rights reserved unless stated otherwise by the repository owner.
