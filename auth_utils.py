import streamlit as st
from supabase import create_client, Client
import re

from workout_parse import normalize_exercise_key

PROFILE_TABLE = "user_profiles"
CONVERSATIONS_TABLE = "conversations"
CONVERSATION_MESSAGES_TABLE = "conversation_messages"

def _session_access_refresh(sess):
    """Extract access + refresh tokens from Streamlit-stored session (object or dict)."""
    if sess is None:
        return None, None
    if isinstance(sess, dict):
        return sess.get("access_token"), sess.get("refresh_token")
    return getattr(sess, "access_token", None), getattr(sess, "refresh_token", None)


def get_supabase_client() -> Client:
    """
    Return a Supabase client using project URL + anon key from secrets.

    After login, ``st.session_state.session`` holds the user JWT. Attaching it
    with ``set_session`` is required so Row Level Security policies
    (e.g. on ``conversations`` / ``conversation_messages``) see ``auth.uid()``.
    """
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    client = create_client(url, key)
    access, refresh = _session_access_refresh(st.session_state.get("session"))
    if access and refresh:
        try:
            client.auth.set_session(access, refresh)
        except Exception:
            pass
    return client

def is_valid_email(email: str) -> bool:
    """Validate email format."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def is_valid_username(username: str) -> bool:
    """Validate username (alphanumeric and underscore, 3-20 chars)."""
    pattern = r'^[a-zA-Z0-9_]{3,20}$'
    return re.match(pattern, username) is not None

def _safe_username_from_email(email: str) -> str:
    """Create a valid fallback username from an email address."""
    base = email.split("@")[0]
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", base)[:20]
    if len(sanitized) < 3:
        sanitized = f"user_{sanitized}".replace("__", "_")[:20]
    return sanitized or "user"

def _is_permission_error(error_msg: str) -> bool:
    """Detect database permission errors from Supabase responses."""
    lowered = (error_msg or "").lower()
    return "permission denied" in lowered or "code': '42501'" in lowered or 'code": "42501"' in lowered

def _fallback_profile(user_id: str, email: str = "", username: str = "") -> dict:
    """Build a minimal profile object when DB profile access is blocked."""
    base_username = username if is_valid_username(username) else _safe_username_from_email(email)
    return {
        "id": user_id,
        "username": base_username,
        "allergies": "",
        "fitness_goals": "",
    }

def _ensure_profile_row(user_id: str, username: str, email: str = "") -> dict:
    """Ensure a profile row exists and return it."""
    supabase = get_supabase_client()
    fallback_username = username if is_valid_username(username) else _safe_username_from_email(email)
    try:
        upsert_response = supabase.table(PROFILE_TABLE).upsert(
            {
                "id": user_id,
                "username": fallback_username,
                "allergies": "",
                "fitness_goals": ""
            },
            on_conflict="id"
        ).execute()
        if upsert_response.data:
            return upsert_response.data[0]
        return get_user_profile(user_id)
    except Exception:
        return _fallback_profile(user_id=user_id, email=email, username=username)

def register_user(email: str, password: str, username: str) -> dict:
    """
    Register a new user with email, password, and username.
    Returns: {"success": bool, "message": str, "user": dict or None}
    """
    # Validate inputs
    if not is_valid_email(email):
        return {"success": False, "message": "Invalid email format."}
    
    if len(password) < 6:
        return {"success": False, "message": "Password must be at least 6 characters."}
    
    if not is_valid_username(username):
        return {"success": False, "message": "Username must be 3-20 alphanumeric characters (underscore allowed)."}
    
    try:
        supabase = get_supabase_client()
        
        # Sign up the user
        response = supabase.auth.sign_up(
            {
                "email": email,
                "password": password,
                "options": {
                    "data": {"username": username}
                }
            }
        )
        
        if not response.user:
            return {"success": False, "message": "Registration failed. Email may already be in use."}
        
        user_id = response.user.id
        
        # Ensure profile exists even if DB trigger did not create it.
        profile = _ensure_profile_row(user_id, username, email)

        # Immediately sign in so users can continue without extra email-confirmation flow in-app.
        session_response = supabase.auth.sign_in_with_password({"email": email, "password": password})
        if session_response and session_response.user:
            if not profile:
                profile = _ensure_profile_row(user_id, username, email)
            return {
                "success": True,
                "message": "Registration successful!",
                "user": session_response.user,
                "profile": profile,
                "session": session_response.session
            }
        
        return {
            "success": True,
            "message": "Registration successful! Please log in.",
            "user": response.user
        }
    
    except Exception as e:
        error_msg = str(e)
        lower_error = error_msg.lower()
        if "database error saving new user" in lower_error:
            return {
                "success": False,
                "message": "Registration failed because your Supabase Auth trigger/policy rejected the new user row. Common fix: make sure your auth->profile trigger can handle missing metadata, or pass metadata fields like username."
            }
        if "duplicate key" in lower_error or "unique" in lower_error:
            return {"success": False, "message": "Username or email already exists."}
        if "user already registered" in lower_error or "already registered" in lower_error:
            try:
                supabase = get_supabase_client()
                login_response = supabase.auth.sign_in_with_password({"email": email, "password": password})
                if login_response and login_response.user:
                    profile = _ensure_profile_row(login_response.user.id, username, email)
                    return {
                        "success": True,
                        "message": "Account already existed. Logged you in.",
                        "user": login_response.user,
                        "profile": profile,
                        "session": login_response.session
                    }
            except Exception:
                pass
            return {"success": False, "message": "Account already exists. Please log in instead."}
        if "email not confirmed" in lower_error or "email_not_confirmed" in lower_error:
            return {
                "success": False,
                "message": "Email confirmation is enabled in Supabase Auth. Disable it in Supabase (Auth > Providers > Email) to allow instant login after registration."
            }
        return {"success": False, "message": f"Registration error: {error_msg}"}

def login_user(email: str, password: str) -> dict:
    """
    Login a user with email and password.
    Returns: {"success": bool, "message": str, "user": dict or None}
    """
    try:
        supabase = get_supabase_client()
        response = supabase.auth.sign_in_with_password({"email": email, "password": password})
        
        if not response.user:
            return {"success": False, "message": "Invalid email or password."}
        
        # Fetch user profile
        try:
            profile_response = supabase.table(PROFILE_TABLE).select("*").eq("id", response.user.id).execute()
            profile = profile_response.data[0] if profile_response.data else None
        except Exception as profile_error:
            error_msg = str(profile_error)
            if _is_permission_error(error_msg):
                profile = _fallback_profile(
                    user_id=response.user.id,
                    email=email,
                    username=response.user.user_metadata.get("username", "") if response.user.user_metadata else ""
                )
            else:
                return {"success": False, "message": f"Could not load profile: {error_msg}"}
        
        if not profile:
            profile = _ensure_profile_row(response.user.id, "", email)
            if not profile:
                return {"success": False, "message": "Profile could not be created. Please try again."}
        
        return {
            "success": True,
            "message": "Login successful!",
            "user": response.user,
            "profile": profile,
            "session": response.session
        }
    
    except Exception as e:
        error_msg = str(e)
        # Provide more helpful error messages
        if "Invalid login credentials" in error_msg or "Invalid password" in error_msg:
            return {"success": False, "message": "Invalid email or password."}
        elif "user_not_found" in error_msg:
            return {"success": False, "message": "Email not found. Please register first."}
        else:
            return {"success": False, "message": f"Login error: {error_msg}"}

def logout_user() -> None:
    """Logout the current user and clear session state."""
    try:
        supabase = get_supabase_client()
        supabase.auth.sign_out()
    except:
        pass
    
    # Clear session state
    for key in list(st.session_state.keys()):
        if key not in ["chats", "current_chat_id"]:
            del st.session_state[key]

def get_user_profile(user_id: str) -> dict:
    """Fetch user profile from database."""
    try:
        supabase = get_supabase_client()
        response = supabase.table(PROFILE_TABLE).select("*").eq("id", user_id).execute()
        
        return response.data[0] if response.data else None
    
    except Exception as e:
        st.error(f"Error fetching profile: {str(e)}")
        return None

def update_user_profile(user_id: str, username: str, allergies: str, fitness_goals: str) -> dict:
    """
    Update user profile.
    Returns: {"success": bool, "message": str}
    """
    if not is_valid_username(username):
        return {"success": False, "message": "Invalid username format."}
    
    try:
        supabase = get_supabase_client()
        
        update_data = {
            "username": username,
            "allergies": allergies,
            "fitness_goals": fitness_goals
        }
        
        response = supabase.table(PROFILE_TABLE).update(update_data).eq("id", user_id).execute()
        
        if not response.data:
            response = supabase.table(PROFILE_TABLE).upsert(
                {"id": user_id, **update_data},
                on_conflict="id"
            ).execute()

        if response.data:
            return {"success": True, "message": "Profile updated successfully!"}
        else:
            return {"success": False, "message": "Failed to update profile."}
    
    except Exception as e:
        error_msg = str(e)
        if _is_permission_error(error_msg):
            return {
                "success": False,
                "message": "Profile update blocked by Supabase table permissions (RLS/policy)."
            }
        if "duplicate key" in error_msg.lower() or "unique" in error_msg.lower():
            return {"success": False, "message": "Username already taken."}
        return {"success": False, "message": f"Update error: {error_msg}"}

def _extract_allergies(message: str) -> str:
    """Extract simple allergy details from free-form user messages."""
    allergy_match = re.search(
        r"(?:allergic to|allergy to|allergies?:?)\s+([^.!?\n]+)",
        message,
        re.IGNORECASE
    )
    if allergy_match:
        return allergy_match.group(1).strip(" ,.;")
    return ""

def _extract_fitness_goal(message: str) -> str:
    """Extract goal details from free-form user messages."""
    goal_match = re.search(
        r"(?:goal is to|my goal is to|aiming to|i want to|trying to)\s+([^.!?\n]+)",
        message,
        re.IGNORECASE
    )
    if goal_match:
        return goal_match.group(1).strip(" ,.;")

    weight_goal_match = re.search(
        r"(\d{2,3})\s?kg.*?(?:reach|to|aiming).{0,15}(\d{2,3})\s?kg",
        message,
        re.IGNORECASE
    )
    if weight_goal_match:
        return f"Reduce weight from {weight_goal_match.group(1)}kg to {weight_goal_match.group(2)}kg"

    return ""

def persist_profile_from_chat(user_id: str, profile: dict, message: str) -> dict:
    """
    Persist discovered allergy/goal details from chat to user profile.
    Returns the updated profile dict (or original profile if no changes).
    """
    if not user_id or not profile or not message:
        return profile

    extracted_allergies = _extract_allergies(message)
    extracted_goal = _extract_fitness_goal(message)

    existing_allergies = (profile.get("allergies") or "").strip()
    existing_goal = (profile.get("fitness_goals") or "").strip()

    new_allergies = existing_allergies or extracted_allergies
    new_goal = existing_goal or extracted_goal

    if new_allergies == existing_allergies and new_goal == existing_goal:
        return profile

    result = update_user_profile(
        user_id=user_id,
        username=profile.get("username", "user"),
        allergies=new_allergies,
        fitness_goals=new_goal
    )
    if result.get("success"):
        refreshed = get_user_profile(user_id)
        return refreshed or profile
    return profile

def log_workout(supabase_client, user_id: str, exercise: str, sets: int, reps: int, weight: float):
    """Insert a workout log row for a user."""
    payload = {
        "user_id": str(user_id),
        "exercise_name": exercise.strip(),
        "sets": int(sets),
        "reps": int(reps),
        "weight_kg": float(weight) if weight is not None else 0.0,
    }
    response = supabase_client.table("workout_logs").insert(payload).execute()
    if response.data:
        return response.data[0]
    # Some PostgREST configs return empty data on success; treat as OK if no error attr.
    err = getattr(response, "error", None) or getattr(response, "message", None)
    if err:
        raise RuntimeError(str(err))
    return payload

def get_recent_workouts(supabase_client, user_id: str, limit: int = 5):
    """Fetch recent workouts for short-term context."""
    response = (
        supabase_client
        .table("workout_logs")
        .select("id, created_at, exercise_name, sets, reps, weight_kg")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return response.data or []

def search_meal_library(supabase_client, allergies_list, tags_filter=None):
    """
    Return meals excluding ingredients that match any allergy term.
    Uses simple case-insensitive substring matching for safety.
    """
    query = supabase_client.table("meal_library").select(
        "id, name, ingredients, tags, instructions, calories"
    )
    if tags_filter:
        query = query.overlaps("tags", tags_filter)

    response = query.execute()
    meals = response.data or []

    normalized_allergies = {
        allergy.strip().lower() for allergy in (allergies_list or [])
        if allergy and allergy.strip()
    }
    if not normalized_allergies:
        return meals

    safe_meals = []
    for meal in meals:
        ingredients = [item.lower().strip() for item in (meal.get("ingredients") or [])]
        has_conflict = any(
            any(allergy in ingredient for ingredient in ingredients)
            for allergy in normalized_allergies
        )
        if not has_conflict:
            safe_meals.append(meal)
    return safe_meals

def save_meal_to_library(
    supabase_client,
    name: str,
    ingredients: list,
    instructions: str,
    calories: int,
    macros_tags: list
):
    """Insert a meal recommendation into shared meal_library."""
    def normalize_ingredients(items: list) -> list:
        # Normalize case/spacing only; keep quantities intact for exact matching semantics.
        return [re.sub(r"\s+", " ", str(item).strip().lower()) for item in items if str(item).strip()]

    normalized_incoming = normalize_ingredients(ingredients)

    # Exact-ingredient de-duplication:
    # same normalized ingredient list (including quantities like 2 eggs vs 3 eggs) => treat as duplicate.
    existing = supabase_client.table("meal_library").select("id, ingredients").execute()
    for row in (existing.data or []):
        existing_normalized = normalize_ingredients(row.get("ingredients") or [])
        if existing_normalized == normalized_incoming:
            return row

    payload = {
        "name": name.strip(),
        "ingredients": ingredients,
        "instructions": instructions.strip(),
        "calories": int(calories),
        "tags": macros_tags,
    }
    response = supabase_client.table("meal_library").insert(payload).execute()
    return response.data[0] if response.data else None

def get_best_pr_rows(supabase_client, user_id: str, limit: int = 15):
    """
    One row per exercise (normalized key): highest weight_kg, then highest reps.
    All historical rows stay in workout_logs; this is display / edit selection only.
    """
    response = (
        supabase_client.table("workout_logs")
        .select("id, exercise_name, sets, reps, weight_kg, created_at")
        .eq("user_id", str(user_id))
        .execute()
    )
    rows = response.data or []
    best: dict[str, dict] = {}
    for row in rows:
        key = normalize_exercise_key(row.get("exercise_name") or "")
        if not key:
            continue
        w = float(row.get("weight_kg") or 0)
        r = int(row.get("reps") or 0)
        prev = best.get(key)
        if prev is None:
            best[key] = row
        else:
            pw = float(prev.get("weight_kg") or 0)
            pr = int(prev.get("reps") or 0)
            if w > pw or (w == pw and r > pr):
                best[key] = row
    out = list(best.values())
    out.sort(key=lambda x: (-float(x.get("weight_kg") or 0), -int(x.get("reps") or 0)))
    return out[:limit]


def get_best_prs(supabase_client, user_id: str, limit: int = 10):
    """Return display dicts for the PR table (best lift per exercise name)."""
    formatted = []
    for row in get_best_pr_rows(supabase_client, user_id, limit=limit):
        weight_value = float(row.get("weight_kg") or 0)
        weight_label = "bodyweight" if weight_value <= 0 else f"{weight_value:g} kg"
        formatted.append(
            {
                "Exercise": row.get("exercise_name", ""),
                "Weight": weight_label,
                "Reps": int(row.get("reps") or 0),
            }
        )
    return formatted


def update_workout_log(
    supabase_client,
    user_id: str,
    log_id: str,
    exercise_name: str,
    sets: int,
    reps: int,
    weight_kg: float,
):
    """Update a single workout_logs row (must belong to user_id for RLS)."""
    supabase_client.table("workout_logs").update(
        {
            "exercise_name": exercise_name.strip(),
            "sets": max(1, int(sets)),
            "reps": max(1, int(reps)),
            "weight_kg": float(weight_kg),
        }
    ).eq("id", log_id).eq("user_id", str(user_id)).execute()


def delete_workout_log(supabase_client, user_id: str, log_id: str):
    """Delete one workout_logs row."""
    supabase_client.table("workout_logs").delete().eq("id", log_id).eq("user_id", str(user_id)).execute()

def get_current_user() -> dict:
    """Get the current authenticated user from session state."""
    return st.session_state.get("user", None)

def get_current_session() -> dict:
    """Get the current session from session state."""
    return st.session_state.get("session", None)

def list_conversations(supabase_client, user_id: str):
    """List a user's conversations (newest first)."""
    response = (
        supabase_client
        .table(CONVERSATIONS_TABLE)
        .select("id, title, created_at")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    return response.data or []

def create_conversation(supabase_client, user_id: str, title: str):
    """Create a conversation for the current user."""
    response = (
        supabase_client
        .table(CONVERSATIONS_TABLE)
        .insert({"user_id": user_id, "title": title})
        .execute()
    )
    return response.data[0] if response.data else None

def rename_conversation(supabase_client, conversation_id: str, user_id: str, title: str):
    """Rename a conversation."""
    response = (
        supabase_client
        .table(CONVERSATIONS_TABLE)
        .update({"title": title})
        .eq("id", conversation_id)
        .eq("user_id", user_id)
        .execute()
    )
    return response.data[0] if response.data else None

def delete_conversation(supabase_client, conversation_id: str, user_id: str):
    """Delete a conversation and its messages (if FK cascade is set)."""
    response = (
        supabase_client
        .table(CONVERSATIONS_TABLE)
        .delete()
        .eq("id", conversation_id)
        .eq("user_id", user_id)
        .execute()
    )
    return response.data or []

def list_conversation_messages(supabase_client, conversation_id: str):
    """List all messages in a conversation by creation order."""
    try:
        response = (
            supabase_client
            .table(CONVERSATION_MESSAGES_TABLE)
            .select("id, role, content, created_at, langfuse_trace_id")
            .eq("conversation_id", conversation_id)
            .order("created_at", desc=False)
            .execute()
        )
    except Exception:
        response = (
            supabase_client
            .table(CONVERSATION_MESSAGES_TABLE)
            .select("id, role, content, created_at")
            .eq("conversation_id", conversation_id)
            .order("created_at", desc=False)
            .execute()
        )
    return response.data or []


def create_conversation_message(
    supabase_client,
    conversation_id: str,
    role: str,
    content: str,
    langfuse_trace_id: str | None = None,
):
    """Insert one chat message row."""
    payload = {
        "conversation_id": conversation_id,
        "role": role,
        "content": content,
    }
    if langfuse_trace_id:
        payload["langfuse_trace_id"] = langfuse_trace_id
    try:
        response = supabase_client.table(CONVERSATION_MESSAGES_TABLE).insert(payload).execute()
        return response.data[0] if response.data else None
    except Exception:
        if "langfuse_trace_id" in payload:
            payload.pop("langfuse_trace_id", None)
            response = supabase_client.table(CONVERSATION_MESSAGES_TABLE).insert(payload).execute()
            return response.data[0] if response.data else None
        raise

def delete_conversation_messages(supabase_client, conversation_id: str, message_ids: list):
    """Delete selected messages from a conversation."""
    if not message_ids:
        return []
    response = (
        supabase_client
        .table(CONVERSATION_MESSAGES_TABLE)
        .delete()
        .eq("conversation_id", conversation_id)
        .in_("id", message_ids)
        .execute()
    )
    return response.data or []


def update_conversation_message(supabase_client, message_id: str, conversation_id: str, content: str):
    """Update message body (RLS must allow update for owner)."""
    response = (
        supabase_client
        .table(CONVERSATION_MESSAGES_TABLE)
        .update({"content": content})
        .eq("id", message_id)
        .eq("conversation_id", conversation_id)
        .execute()
    )
    return response.data[0] if response.data else None
