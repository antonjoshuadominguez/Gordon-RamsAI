import json
import random
import re

import streamlit as st
from sidebar import render_sidebar
from ai import generate_response, record_langfuse_rating, get_model_error_roast
from auth_utils import (
    register_user, login_user, get_user_profile,
    update_user_profile, get_current_user, persist_profile_from_chat,
    log_workout, get_recent_workouts, search_meal_library, get_supabase_client,
    save_meal_to_library, get_best_prs, get_best_pr_rows,
    update_workout_log, delete_workout_log,
    list_conversations, create_conversation, list_conversation_messages,
    create_conversation_message, delete_conversation_messages,
    update_conversation_message,
    supabase_secrets_available, SUPABASE_SECRETS_HELP,
)
from workout_parse import extract_workouts_from_text, parse_log_command_body

# ============================================================
# PAGE CONFIG
# ============================================================
st.set_page_config(page_title="Gordon RamsAi", page_icon="🥗", layout="wide")

# ============================================================
# INITIALIZE SESSION STATE
# ============================================================
if "user" not in st.session_state:
    st.session_state.user = None
if "profile" not in st.session_state:
    st.session_state.profile = None
if "session" not in st.session_state:
    st.session_state.session = None
if "current_page" not in st.session_state:
    st.session_state.current_page = "login"
if "messages" not in st.session_state:
    st.session_state.messages = []
if "chats" not in st.session_state:
    st.session_state.chats = []
if "current_chat_id" not in st.session_state:
    st.session_state.current_chat_id = 0

DEFAULT_ASSISTANT_MESSAGE = (
    "Right — pick a lane: lifts, meals, or stop wasting my time. "
    "What are you cooking up in that gym today, donkey?"
)

def _load_conversations_into_session(user_id: str):
    """Load conversations from Supabase into session state."""
    try:
        supabase = get_supabase_client()
        conversations = list_conversations(supabase, user_id)
        st.session_state.chats = [
            {
                "id": chat["id"],
                "name": chat.get("title", "Chat"),
                "messages": []
            }
            for chat in conversations
        ]
        if not st.session_state.chats:
            created = create_conversation(supabase, user_id, "Chat 1")
            if created:
                st.session_state.chats = [{"id": created["id"], "name": created.get("title", "Chat 1"), "messages": []}]
        st.session_state.current_chat_id = 0
    except Exception:
        # Keep existing local state as fallback.
        pass

def _load_current_chat_messages():
    """Load selected conversation messages from Supabase."""
    if not st.session_state.chats:
        st.session_state.messages = []
        return
    chat = st.session_state.chats[st.session_state.current_chat_id]
    chat_id = chat.get("id")
    if not chat_id:
        st.session_state.messages = chat.get("messages", []).copy()
        return
    try:
        supabase = get_supabase_client()
        rows = list_conversation_messages(supabase, chat_id)
        st.session_state.messages = [
            {
                "id": row.get("id"),
                "role": row.get("role"),
                "content": row.get("content"),
                "lf_trace_id": row.get("langfuse_trace_id"),
            }
            for row in rows
        ]
        st.session_state.chats[st.session_state.current_chat_id]["messages"] = st.session_state.messages.copy()
        if not st.session_state.messages:
            inserted = create_conversation_message(supabase, chat_id, "assistant", DEFAULT_ASSISTANT_MESSAGE)
            st.session_state.messages = [
                {"id": inserted.get("id") if inserted else None, "role": "assistant", "content": DEFAULT_ASSISTANT_MESSAGE}
            ]
            st.session_state.chats[st.session_state.current_chat_id]["messages"] = st.session_state.messages.copy()
    except Exception:
        st.session_state.messages = chat.get("messages", []).copy()

def _persist_message_to_current_chat(role: str, content: str, langfuse_trace_id: str | None = None):
    """Persist one message to current chat in DB (if available)."""
    if not st.session_state.chats:
        return None
    chat = st.session_state.chats[st.session_state.current_chat_id]
    chat_id = chat.get("id")
    if not chat_id:
        return None
    try:
        supabase = get_supabase_client()
        inserted = create_conversation_message(
            supabase, chat_id, role, content, langfuse_trace_id=langfuse_trace_id
        )
        return inserted.get("id") if inserted else None
    except Exception:
        return None

def _delete_messages_from_current_chat(message_ids: list):
    """Delete selected messages from active chat and refresh local state."""
    if not message_ids or not st.session_state.chats:
        return
    chat = st.session_state.chats[st.session_state.current_chat_id]
    chat_id = chat.get("id")
    if not chat_id:
        st.session_state.messages = [m for m in st.session_state.messages if m.get("id") not in set(message_ids)]
        chat["messages"] = st.session_state.messages.copy()
        return
    try:
        supabase = get_supabase_client()
        delete_conversation_messages(supabase, chat_id, message_ids)
    except Exception:
        pass
    st.session_state.messages = [m for m in st.session_state.messages if m.get("id") not in set(message_ids)]
    chat["messages"] = st.session_state.messages.copy()


CHAT_HOVER_CSS = """
<style>
/* User messages only: Edit/Del fully hidden until you hover that chat bubble */
div[data-testid="stChatMessage"]:has(.gordon-msg-user) button {
  opacity: 0 !important;
  pointer-events: none !important;
  visibility: hidden !important;
}
div[data-testid="stChatMessage"]:has(.gordon-msg-user):hover button {
  opacity: 1 !important;
  pointer-events: auto !important;
  visibility: visible !important;
}
/* Assistant: rating buttons always visible (not tied to user hover rules) */
div[data-testid="stChatMessage"]:has(.gordon-msg-assistant) button {
  opacity: 1 !important;
  pointer-events: auto !important;
  visibility: visible !important;
}
</style>
"""


def _clear_gemini_session_for_current_chat():
    if not st.session_state.chats:
        return
    idx = st.session_state.current_chat_id
    if 0 <= idx < len(st.session_state.chats):
        st.session_state.chats[idx].pop("gemini_session", None)


def _current_conversation_id():
    if not st.session_state.chats:
        return None
    idx = st.session_state.current_chat_id
    if 0 <= idx < len(st.session_state.chats):
        return st.session_state.chats[idx].get("id")
    return None


def _delete_user_message_and_pair(idx: int):
    """Delete a user message and the assistant reply immediately after it."""
    msgs = st.session_state.messages
    if idx < 0 or idx >= len(msgs) or msgs[idx].get("role") != "user":
        return
    ids = []
    if msgs[idx].get("id"):
        ids.append(msgs[idx]["id"])
    if idx + 1 < len(msgs) and msgs[idx + 1].get("role") == "assistant" and msgs[idx + 1].get("id"):
        ids.append(msgs[idx + 1]["id"])
    chat = st.session_state.chats[st.session_state.current_chat_id]
    if ids and chat.get("id"):
        _delete_messages_from_current_chat(ids)
        _load_current_chat_messages()
    else:
        end = idx + 2 if (idx + 1 < len(msgs) and msgs[idx + 1].get("role") == "assistant") else idx + 1
        st.session_state.messages = msgs[:idx] + msgs[end:]
        chat["messages"] = st.session_state.messages.copy()
    _clear_gemini_session_for_current_chat()


def _apply_user_message_edit(idx: int, new_body: str, user):
    """Update user text, drop all following messages, clear model session."""
    msgs = st.session_state.messages
    if idx < 0 or idx >= len(msgs) or msgs[idx].get("role") != "user":
        return
    chat = st.session_state.chats[st.session_state.current_chat_id]
    cid = chat.get("id")
    row_id = msgs[idx].get("id")
    if cid and row_id:
        try:
            supabase = get_supabase_client()
            update_conversation_message(supabase, row_id, cid, new_body.strip())
        except Exception:
            pass
    tail_ids = [m["id"] for m in msgs[idx + 1 :] if m.get("id")]
    if tail_ids and cid:
        try:
            supabase = get_supabase_client()
            delete_conversation_messages(supabase, cid, tail_ids)
        except Exception:
            pass
    st.session_state.messages = msgs[: idx + 1]
    st.session_state.messages[idx]["content"] = new_body.strip()
    chat["messages"] = st.session_state.messages.copy()
    _clear_gemini_session_for_current_chat()


def _save_inline_edit_and_regenerate(user, profile, idx: int, new_body: str):
    """Apply edited user text, drop tail, regenerate assistant, persist with Langfuse trace id."""
    _apply_user_message_edit(idx, new_body, user)
    st.session_state.chat_edit_idx = None
    prompt_after = st.session_state.messages[-1]["content"]
    response, trace_id = _run_model_reply(user, profile, prompt_after)
    assistant_message_id = _persist_message_to_current_chat(
        "assistant", response, langfuse_trace_id=trace_id
    )
    st.session_state.messages.append(
        {
            "id": assistant_message_id,
            "role": "assistant",
            "content": response,
            "lf_trace_id": trace_id,
        }
    )
    if st.session_state.current_chat_id < len(st.session_state.chats):
        st.session_state.chats[st.session_state.current_chat_id]["messages"] = (
            st.session_state.messages.copy()
        )


def _run_model_reply(user, profile, prompt: str, hidden_turn_context: str | None = None):
    """Call Gemini and return (response_text, langfuse_trace_id)."""
    supabase = None
    try:
        supabase = get_supabase_client()
        recent_workouts = get_recent_workouts(supabase, user.id, limit=5)
    except Exception:
        recent_workouts = []

    meal_context = []
    is_meal_query = _is_meal_advice_query(prompt)
    if is_meal_query:
        allergies_raw = (profile.get("allergies") or "")
        allergies_list = [item.strip() for item in allergies_raw.split(",") if item.strip()]
        try:
            if supabase is None:
                supabase = get_supabase_client()
            meal_context = search_meal_library(
                supabase_client=supabase,
                allergies_list=allergies_list,
                tags_filter=None,
            )
        except Exception:
            meal_context = []

    session_id = _current_conversation_id()
    _spin = (
        "Shouting at the pass…",
        "Reading the tickets…",
        "Dealing with another idiot sandwich…",
        "Swearing at onions…",
    )
    with st.spinner(random.choice(_spin)):
        try:
            response, _usage, trace_id = generate_response(
                st.session_state.messages,
                profile,
                user_id=user.id,
                meal_context=meal_context,
                recent_workouts=recent_workouts,
                session_id=str(session_id) if session_id else None,
                hidden_turn_context=hidden_turn_context,
            )
            if is_meal_query:
                meal_record = _extract_meal_library_record(response)
                if meal_record:
                    try:
                        if supabase is None:
                            supabase = get_supabase_client()
                        save_meal_to_library(
                            supabase_client=supabase,
                            name=meal_record["name"],
                            ingredients=meal_record["ingredients"],
                            instructions=meal_record["instructions"],
                            calories=meal_record["calories"],
                            macros_tags=meal_record["tags"],
                        )
                    except Exception:
                        pass
                response = _strip_meal_library_record(response)
            return response, trace_id
        except Exception as e:
            return get_model_error_roast(str(e)[:160]), None


def _is_meal_advice_query(prompt: str) -> bool:
    prompt_lower = prompt.lower()
    keywords = ["meal", "diet", "food", "recipe", "eat", "nutrition", "calories"]
    return any(keyword in prompt_lower for keyword in keywords)

def _extract_meal_library_record(response_text: str):
    """Parse model-provided MEAL_LIBRARY_RECORD block."""
    block_match = re.search(
        r"\[MEAL_LIBRARY_RECORD\](.*?)\[/MEAL_LIBRARY_RECORD\]",
        response_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not block_match:
        return None

    block = block_match.group(1)
    def pick(label: str):
        match = re.search(rf"{label}\s*:\s*(.+)", block, flags=re.IGNORECASE)
        return match.group(1).strip() if match else ""

    name = pick("name")
    ingredients_raw = pick("ingredients")
    instructions = pick("instructions")
    calories_raw = pick("calories")
    tags_raw = pick("tags")

    if not name or not ingredients_raw:
        return None

    ingredients = [item.strip() for item in ingredients_raw.split(",") if item.strip()]
    tags = [item.strip() for item in tags_raw.split(",") if item.strip()]
    try:
        calories = int(re.search(r"\d+", calories_raw).group(0)) if calories_raw else 0
    except Exception:
        calories = 0

    return {
        "name": name,
        "ingredients": ingredients,
        "instructions": instructions,
        "calories": calories,
        "tags": tags,
    }

def _strip_meal_library_record(response_text: str) -> str:
    """Remove machine-readable meal record block from user-visible output."""
    return re.sub(
        r"\[MEAL_LIBRARY_RECORD\].*?\[/MEAL_LIBRARY_RECORD\]",
        "",
        response_text,
        flags=re.IGNORECASE | re.DOTALL
    ).strip()

def _strip_log_command_prefix(text: str) -> str | None:
    """If text starts with a log/record/save workout intent, return the remainder; else None."""
    t = text.strip()
    patterns = (
        r"^(?:please\s+)?(?:can you\s+)?(?:log|record|save)\s+(?:this|that|it|my|the|those|last)\s*[:,-]?\s*",
        r"^(?:please\s+)?(?:can you\s+)?(?:log|record|save)\s+my\s+(?:workout|lift|session)\s*[:,-]?\s*",
        r"^(?:please\s+)?(?:can you\s+)?(?:log|record|save)\s*[:,-]\s*",
        r"^(?:please\s+)?(?:can you\s+)?(?:log|record|save)\s+this\s*[:,-]?\s*",
    )
    for p in patterns:
        m = re.match(p, t, flags=re.IGNORECASE)
        if m:
            return t[m.end() :].strip()
    return None


def _parse_workout_log_command(prompt: str):
    """
    Parse commands like:
    - Log this: bench press 3 sets 10 reps 60kg
    - log that squat 5x5 100kg
    - please log: bench 3x10 @ 80kg
    """
    text = prompt.strip()
    body = _strip_log_command_prefix(text)
    if body is None:
        return None
    return parse_log_command_body(body)


def _workout_turn_hidden_context(
    *,
    payload: dict,
    duplicate: bool,
    log_failed: bool,
    log_error: str | None,
) -> str:
    """Short facts for <kitchen_context> (keep simple for the model)."""
    ex = payload["exercise"]
    s, r, w = payload["sets"], payload["reps"], payload["weight"]
    if log_failed:
        return f"Workout save failed ({log_error or 'unknown'}). Stay in character; do not quote errors verbatim."
    if duplicate:
        return f"Duplicate send: {ex} {s}x{r} @ {w}kg. Roast them; do not say logged/database."
    return f"They lifted {ex} {s}x{r} @ {w}kg. Hype or roast; do not say you saved anything."


def _workout_turn_hidden_context_multi(
    payloads: list[dict],
    *,
    duplicate_flags: list[bool],
    log_failed: bool,
    log_error: str | None,
) -> str:
    if log_failed:
        return f"Workout save failed ({log_error or 'unknown'}). Stay in character; do not quote errors verbatim."
    if payloads and all(duplicate_flags):
        return "Duplicate lifts. Roast them; do not say logged/database."
    bits = [f"{p['exercise']} {p['sets']}x{p['reps']} @ {p['weight']}kg" for p in payloads]
    return "They lifted: " + "; ".join(bits) + ". Hype or roast; do not say you saved anything."


def _should_scan_history_for_workout(prompt: str) -> bool:
    """True when the user is asking to persist a prior mention (not only when they said 'workout')."""
    lowered = prompt.lower()
    if re.search(r"\b(log|record|save)\s+(that|this|it|my|the|those|last)\b", lowered):
        return True
    if re.search(r"\b(can you|please)\s+(log|record|save)\b", lowered):
        return True
    if re.search(r"\b(log|record|save)\s+my\s+(workout|lift|session|pr)\b", lowered):
        return True
    if re.search(r"\badd\s+(that|this|it)\s+to\s+(my\s+)?(workout\s+)?log\b", lowered):
        return True
    if "log" in lowered and "workout" in lowered:
        return True
    return False

def _build_workout_signature(payload: dict) -> str:
    return f"{payload['exercise'].lower()}|{payload['sets']}|{payload['reps']}|{payload['weight']}"

def _resolve_workout_payloads(prompt: str, messages: list) -> list[dict]:
    """
    Resolve zero or more workout payloads (log commands, natural PR lines, or history scan).
    """
    cmd = _parse_workout_log_command(prompt)
    if cmd:
        return [cmd]
    found = extract_workouts_from_text(prompt)
    if found:
        return found
    if _should_scan_history_for_workout(prompt):
        for msg in reversed(messages[:-1]):
            if msg.get("role") != "user":
                continue
            c = _parse_workout_log_command(msg.get("content", ""))
            if c:
                return [c]
            h = extract_workouts_from_text(msg.get("content", ""))
            if h:
                return h
    return []

# ============================================================
# LOGIN PAGE
# ============================================================
def show_login_page():
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        st.title("🥗 Gordon RamsAi")
        st.subheader("Your Fitness & Nutrition Assistant")

        if not supabase_secrets_available()[0]:
            st.warning(SUPABASE_SECRETS_HELP)

        st.divider()

        # Tabs for Login and Register
        tab1, tab2 = st.tabs(["Login", "Register"])
        
        with tab1:
            st.write("Sign in to your account")
            
            login_email = st.text_input("Email", key="login_email")
            login_password = st.text_input("Password", type="password", key="login_password")
            
            if st.button("Login", type="primary", use_container_width=True):
                if not login_email or not login_password:
                    st.error("Please enter both email and password.")
                else:
                    with st.spinner("Logging in..."):
                        result = login_user(login_email, login_password)
                    
                    if result["success"]:
                        st.session_state.user = result["user"]
                        st.session_state.profile = result["profile"]
                        st.session_state.session = result.get("session")
                        st.session_state.current_page = "chat"
                        _load_conversations_into_session(result["user"].id)
                        _load_current_chat_messages()
                        st.rerun()
                    else:
                        st.error(result["message"])
        
        with tab2:
            st.write("Create a new account")
            
            reg_email = st.text_input("Email", key="reg_email")
            reg_username = st.text_input("Username (3-20 alphanumeric)", key="reg_username")
            reg_password = st.text_input("Password (min 6 characters)", type="password", key="reg_password")
            reg_password_confirm = st.text_input("Confirm Password", type="password", key="reg_password_confirm")
            
            if st.button("Register", type="primary", use_container_width=True):
                if not all([reg_email, reg_username, reg_password, reg_password_confirm]):
                    st.error("Please fill in all fields.")
                elif reg_password != reg_password_confirm:
                    st.error("Passwords do not match.")
                else:
                    with st.spinner("Creating account..."):
                        result = register_user(reg_email, reg_password, reg_username)
                    
                    if result["success"]:
                        st.success(result["message"])
                        if result.get("user") and result.get("session"):
                            st.session_state.user = result["user"]
                            st.session_state.profile = result.get("profile")
                            st.session_state.session = result["session"]
                            st.session_state.current_page = "chat"
                            _load_conversations_into_session(result["user"].id)
                            _load_current_chat_messages()
                            st.rerun()
                        st.info("Please log in with your new account.")
                    else:
                        st.error(result["message"])

def _meaningful_pr_editor_rows(rows: list) -> list:
    """Ignore empty trailing rows Streamlit adds with num_rows='dynamic'."""
    out = []
    for row in rows or []:
        rid = str(row.get("id") or "").strip()
        ex = str(row.get("exercise_name", "")).strip()
        try:
            w = float(row.get("weight_kg") or 0)
        except (TypeError, ValueError):
            w = 0.0
        if rid or (ex and w > 0):
            out.append(row)
    return out


def _canonical_pr_rows(rows: list) -> str:
    """Stable JSON for detecting edits in the PR data editor."""
    norm = []
    for row in _meaningful_pr_editor_rows(rows or []):
        rid = str(row.get("id") or "").strip()
        ex = str(row.get("exercise_name", "")).strip()
        try:
            w = round(float(row.get("weight_kg") or 0), 3)
        except (TypeError, ValueError):
            w = 0.0
        s, rps = int(row.get("sets") or 1), int(row.get("reps") or 1)
        norm.append(
            {"id": rid, "exercise_name": ex, "sets": s, "reps": rps, "weight_kg": w}
        )
    norm.sort(
        key=lambda x: (x["id"], x["exercise_name"], x["weight_kg"], x["sets"], x["reps"])
    )
    return json.dumps(norm, sort_keys=True)


def _persist_pr_editor(supabase, user_id: str, original_rows: list[dict], edited: list[dict]) -> None:
    """Apply deletes, updates, and new rows from the PR table editor."""
    orig_by_id = {str(r["id"]): r for r in original_rows if str(r.get("id") or "").strip()}
    orig_ids = set(orig_by_id.keys())
    cur_ids = {str(r.get("id") or "").strip() for r in edited if str(r.get("id") or "").strip()}
    for oid in orig_ids - cur_ids:
        delete_workout_log(supabase, user_id, oid)
    for row in edited:
        rid = str(row.get("id") or "").strip()
        ex = str(row.get("exercise_name", "")).strip()
        try:
            w = float(row.get("weight_kg") or 0)
        except (TypeError, ValueError):
            w = 0.0
        s, rps = int(row.get("sets") or 1), int(row.get("reps") or 1)
        if not rid:
            if ex and w > 0:
                log_workout(
                    supabase_client=supabase,
                    user_id=user_id,
                    exercise=ex,
                    sets=s,
                    reps=rps,
                    weight=w,
                )
            continue
        if rid in orig_by_id and (not ex or w <= 0):
            delete_workout_log(supabase, user_id, rid)
            continue
        if rid not in orig_by_id:
            if ex and w > 0:
                log_workout(
                    supabase_client=supabase,
                    user_id=user_id,
                    exercise=ex,
                    sets=s,
                    reps=rps,
                    weight=w,
                )
            continue
        o = orig_by_id[rid]
        if (
            str(o.get("exercise_name", "")).strip() == ex
            and int(o.get("sets") or 1) == s
            and int(o.get("reps") or 1) == rps
            and abs(float(o.get("weight_kg") or 0) - w) < 1e-6
        ):
            continue
        update_workout_log(supabase, user_id, rid, ex, s, rps, w)


# ============================================================
# EDIT PROFILE PAGE
# ============================================================
def show_profile_page():
    st.title("👤 Edit Profile")
    
    user = st.session_state.get("user")
    profile = st.session_state.get("profile")
    
    if not user or not profile:
        st.error("User not authenticated.")
        return
    
    col1, col2 = st.columns([2, 1])
    
    with col1:
        st.subheader("Profile Information")
        
        # Display email
        st.text_input("Email", value=user.email, disabled=True)
        
        # Editable fields
        username = st.text_input("Username", value=profile.get("username", ""), help="3-20 alphanumeric characters")
        
        st.divider()
        
        st.subheader("Health & Fitness")
        
        allergies = st.text_area(
            "Allergies (optional)",
            value=profile.get("allergies", ""),
            placeholder="e.g., Peanuts, Shellfish, Dairy",
            help="List any food allergies or dietary restrictions"
        )
        
        fitness_goals = st.text_area(
            "Fitness Goals (optional)",
            value=profile.get("fitness_goals", ""),
            placeholder="e.g., Lose 10kg in 3 months, Build muscle, Improve endurance",
            help="Describe your fitness goals and what you want to achieve"
        )
        
        st.divider()
        
        col1a, col1b = st.columns(2)
        
        with col1a:
            if st.button("💾 Save Changes", type="primary", use_container_width=True):
                with st.spinner("Updating profile..."):
                    result = update_user_profile(
                        user.id,
                        username,
                        allergies,
                        fitness_goals
                    )
                
                if result["success"]:
                    st.success(result["message"])
                    # Update session state
                    st.session_state.profile = get_user_profile(user.id)
                    st.rerun()
                else:
                    st.error(result["message"])
        
        with col1b:
            if st.button("← Back to Chat", use_container_width=True):
                st.session_state.current_page = "chat"
                st.rerun()
    
    with col2:
        st.subheader("Account Info")
        st.info(f"**User ID:**\n`{user.id}`\n\n**Joined:**\n{profile.get('created_at', 'N/A')[:10]}")
        st.divider()
        st.subheader("🏆 Best Exercises / PRs")
        try:
            supabase = get_supabase_client()
            pr_rows = get_best_pr_rows(supabase, user.id, limit=15)
            if pr_rows:
                editor_rows = [
                    {
                        "id": str(r["id"]),
                        "exercise_name": r.get("exercise_name") or "",
                        "sets": int(r.get("sets") or 1),
                        "reps": int(r.get("reps") or 1),
                        "weight_kg": float(r.get("weight_kg") or 0),
                    }
                    for r in pr_rows
                ]
                ver = int(st.session_state.get("pr_table_ver", 0))
                edited = st.data_editor(
                    editor_rows,
                    num_rows="dynamic",
                    key=f"pr_data_editor_{ver}",
                    column_config={
                        "id": st.column_config.TextColumn("id", disabled=True),
                        "exercise_name": st.column_config.TextColumn("Exercise"),
                        "sets": st.column_config.NumberColumn("Sets", min_value=1, step=1),
                        "reps": st.column_config.NumberColumn("Reps", min_value=1, step=1),
                        "weight_kg": st.column_config.NumberColumn("Weight (kg)", min_value=0.0, format="%.2f"),
                    },
                    hide_index=True,
                )
                if edited is None:
                    edited = editor_rows
                c_ed = _canonical_pr_rows(edited)
                c_db = _canonical_pr_rows(editor_rows)
                if c_ed != c_db:
                    try:
                        _persist_pr_editor(supabase, user.id, editor_rows, edited)
                        st.session_state.pr_table_ver = ver + 1
                        st.rerun()
                    except Exception as ex:
                        st.error(f"Could not update PR table: {ex}")
            else:
                st.caption("No workout logs yet.")
        except Exception as e:
            st.caption(f"Could not load PRs: {e}")

# ============================================================
# CHAT PAGE
# ============================================================
def show_chat_page():
    st.title("🥗 Gordon RamsAi")
    
    user = st.session_state.get("user")
    profile = st.session_state.get("profile")
    
    if not user or not profile:
        st.error("User not authenticated.")
        st.session_state.current_page = "login"
        st.rerun()
    
    # Initialize messages for current chat
    if not st.session_state.messages:
        _load_current_chat_messages()

    st.markdown(CHAT_HOVER_CSS, unsafe_allow_html=True)

    # Display chat messages (Streamlit buttons — no <a href> navigation / new tabs / lost session)
    for idx, message in enumerate(st.session_state.messages):
        with st.chat_message(message["role"]):
            if message.get("role") == "user":
                st.markdown(
                    '<span class="gordon-msg-user" aria-hidden="true"></span>',
                    unsafe_allow_html=True,
                )
                active_edit = st.session_state.get("chat_edit_idx")
                col_main, col_act = st.columns([6, 1])
                with col_main:
                    st.markdown(message.get("content") or "")
                    if active_edit == idx:
                        st.text_area(
                            "Edit message",
                            value=message.get("content") or "",
                            height=min(240, max(100, len(message.get("content") or "") // 2 + 40)),
                            key=f"inline_edit_{idx}",
                            label_visibility="collapsed",
                        )
                        c_save, c_cancel = st.columns(2)
                        with c_save:
                            if st.button("Save & regenerate", type="primary", key=f"inline_save_{idx}"):
                                new_text = st.session_state.get(
                                    f"inline_edit_{idx}", message.get("content") or ""
                                )
                                _save_inline_edit_and_regenerate(user, profile, idx, new_text)
                                st.rerun()
                        with c_cancel:
                            if st.button("Cancel", key=f"inline_cancel_{idx}"):
                                st.session_state.chat_edit_idx = None
                                st.rerun()
                with col_act:
                    if active_edit != idx:
                        if st.button("Edit", key=f"msg_edit_{idx}", help="Edit this message and regenerate the reply"):
                            st.session_state.chat_edit_idx = idx
                            st.rerun()
                        if st.button("Del", key=f"msg_del_{idx}", help="Delete this message and the assistant reply"):
                            _delete_user_message_and_pair(idx)
                            st.rerun()
            else:
                st.markdown(
                    '<span class="gordon-msg-assistant" aria-hidden="true"></span>',
                    unsafe_allow_html=True,
                )
                st.markdown(message.get("content") or "")
                tid = message.get("lf_trace_id")
                if tid:
                    st.caption("Rate this reply")
                    rcols = st.columns(5)
                    for j, stars in enumerate((1, 2, 3, 4, 5)):
                        with rcols[j]:
                            if st.button(f"{stars}★", key=f"msg_rate_{idx}_{stars}", help=f"Rate {stars}/5"):
                                if record_langfuse_rating(tid, float(stars), comment=f"stars={stars}"):
                                    if hasattr(st, "toast"):
                                        st.toast("Saved to Langfuse.")
                                    else:
                                        st.success("Saved to Langfuse.")
                                st.rerun()
    
    # Quick action buttons (only on first message)
    if len(st.session_state.messages) == 1:
        buttons = [
            ("💪 Quick Workout", "Suggest a 10-15 min bodyweight or DIY equipment routine for small spaces."),
            ("🔥 Hell Week", "Give me an intense workout plan for the week."),
            ("🧘 Rest & Recover", "Provide stretching, mobility, or rest-day guidance."),
            ("🍽️ Fuel & Sweat", "Suggest a combined meal and workout pairing with budget-friendly recipes."),
            ("🛒 Cheap Meal Ideas", "Give high-protein, low-cost meal suggestions."),
            ("⚡ Pre-Workout Snack", "Suggest quick, affordable energy boost ideas."),
        ]
        for i in range(0, len(buttons), 2):
            col1, col2 = st.columns(2)
            with col1:
                if st.button(buttons[i][0]):
                    st.session_state.pending_prompt = buttons[i][1]
            if i + 1 < len(buttons):
                with col2:
                    if st.button(buttons[i + 1][0]):
                        st.session_state.pending_prompt = buttons[i + 1][1]
    
    # Chat input
    prompt = st.chat_input("Send a message...")
    
    if "pending_prompt" in st.session_state and st.session_state.pending_prompt:
        prompt = st.session_state.pending_prompt
        st.session_state.pending_prompt = None
    
    if prompt:
        # Add user message to history
        user_message_id = _persist_message_to_current_chat("user", prompt)
        st.session_state.messages.append({"id": user_message_id, "role": "user", "content": prompt})

        # Persist profile details (allergies/goals) mentioned in chat.
        st.session_state.profile = persist_profile_from_chat(
            user_id=user.id,
            profile=st.session_state.profile,
            message=prompt
        )
        profile = st.session_state.profile
        
        with st.chat_message("user"):
            st.markdown(prompt)

        # Handle workout logging (explicit command + inferred workout statements).
        workout_payloads = _resolve_workout_payloads(prompt, st.session_state.messages)
        trace_id = None
        if workout_payloads:
            duplicate_flags: list[bool] = []
            log_failed = False
            log_error: str | None = None
            last_sig = st.session_state.get("last_logged_workout_signature")
            for workout_payload in workout_payloads:
                signature = _build_workout_signature(workout_payload)
                duplicate = last_sig == signature
                duplicate_flags.append(duplicate)
                if duplicate:
                    continue
                try:
                    supabase = get_supabase_client()
                    log_workout(
                        supabase_client=supabase,
                        user_id=user.id,
                        exercise=workout_payload["exercise"],
                        sets=workout_payload["sets"],
                        reps=workout_payload["reps"],
                        weight=workout_payload["weight"],
                    )
                    st.session_state.last_logged_workout_signature = signature
                except Exception as e:
                    log_failed = True
                    log_error = (f"{log_error}; " if log_error else "") + str(e)
            if len(workout_payloads) == 1:
                hidden = _workout_turn_hidden_context(
                    payload=workout_payloads[0],
                    duplicate=bool(duplicate_flags[0]) and not log_failed,
                    log_failed=log_failed,
                    log_error=log_error,
                )
            else:
                hidden = _workout_turn_hidden_context_multi(
                    workout_payloads,
                    duplicate_flags=duplicate_flags,
                    log_failed=log_failed,
                    log_error=log_error,
                )
            response, trace_id = _run_model_reply(
                user, profile, prompt, hidden_turn_context=hidden
            )
        else:
            response, trace_id = _run_model_reply(user, profile, prompt)

        # Add assistant response to history
        assistant_message_id = _persist_message_to_current_chat(
            "assistant", response, langfuse_trace_id=trace_id
        )
        st.session_state.messages.append(
            {
                "id": assistant_message_id,
                "role": "assistant",
                "content": response,
                "lf_trace_id": trace_id,
            }
        )
        
        with st.chat_message("assistant"):
            st.markdown(response)
        
        # Update current chat in session
        if st.session_state.current_chat_id < len(st.session_state.chats):
            st.session_state.chats[st.session_state.current_chat_id]["messages"] = st.session_state.messages.copy()

# ============================================================
# MAIN APP LOGIC
# ============================================================
def main():
    # Render sidebar if authenticated
    if st.session_state.user is not None:
        render_sidebar()
    
    # Render the appropriate page
    if st.session_state.user is None:
        show_login_page()
    elif st.session_state.current_page == "profile":
        show_profile_page()
    else:  # Default to chat
        st.session_state.current_page = "chat"
        show_chat_page()

if __name__ == "__main__":
    main()

