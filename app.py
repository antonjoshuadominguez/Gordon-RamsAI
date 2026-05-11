import random
import re

import streamlit as st
from sidebar import render_sidebar
from ai import generate_response, record_langfuse_rating, get_model_error_roast
from auth_utils import (
    register_user, login_user, get_user_profile,
    update_user_profile, get_current_user, persist_profile_from_chat,
    log_workout, get_recent_workouts, search_meal_library, get_supabase_client,
    save_meal_to_library, get_best_prs,
    list_conversations, create_conversation, list_conversation_messages,
    create_conversation_message, delete_conversation_messages,
    update_conversation_message,
)

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

def _trim_exercise_noise(name: str) -> str:
    """Strip trailing chat filler ('bench today' -> 'bench')."""
    return re.sub(
        r"\s+(today|yesterday|yesterday'?s|tonight|just|earlier|now)\s*$",
        "",
        name.strip(),
        flags=re.IGNORECASE,
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


def _parse_workout_log_body(body: str):
    """Parse exercise/sets/reps/weight from the tail after a log prefix."""
    b = body.strip()
    if not b:
        return None

    def _clean_exercise(name: str) -> str:
        n = re.sub(r"\s+", " ", name).strip(" -–—:,")
        return _trim_exercise_noise(n)

    _xr = r"(?:\s*x\s*|x|\s+)"  # "3 x 10", "3x10", or "3 sets 10" style gap
    patterns = (
        # bench press 3 sets 10 reps 60kg / squat 5x5 100kg
        r"^(?P<exercise>.+?)\s+(?P<sets>\d+)(?:\s*sets?)?"
        + _xr
        + r"\s*(?P<reps>\d+)(?:\s*reps?)?\s+(?P<weight>\d+(?:\.\d+)?)\s*kg?$",
        # bench 3 x 10 60kg / bench 3x10 60kg
        r"^(?P<exercise>.+?)\s+(?P<sets>\d+)(?:\s*x\s*|x)(?P<reps>\d+)\s+(?P<weight>\d+(?:\.\d+)?)\s*kg?$",
        # bench 3x10 @ 60kg
        r"^(?P<exercise>.+?)\s+(?P<sets>\d+)(?:\s*x\s*|x)(?P<reps>\d+)\s*@\s*(?P<weight>\d+(?:\.\d+)?)\s*kg?$",
        # bench press 100kg 3 sets 10
        r"^(?P<exercise>.+?)\s+(?P<weight>\d+(?:\.\d+)?)\s*kg\s+(?P<sets>\d+)(?:\s*sets?)?"
        + _xr
        + r"\s*(?P<reps>\d+)(?:\s*reps?)?$",
        # 3x10 @ 60kg bench
        r"^(?P<sets>\d+)(?:\s*x\s*|x)(?P<reps>\d+)\s*@\s*(?P<weight>\d+(?:\.\d+)?)\s*kg\s+(?P<exercise>.+)$",
    )
    for pat in patterns:
        match = re.search(pat, b, flags=re.IGNORECASE)
        if not match:
            continue
        gd = match.groupdict()
        ex = _clean_exercise(gd["exercise"])
        if len(ex) < 2:
            continue
        return {
            "exercise": ex,
            "sets": int(gd["sets"]),
            "reps": int(gd["reps"]),
            "weight": float(gd["weight"]),
        }
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
    return _parse_workout_log_body(body)


def _parse_workout_from_statement(text: str):
    """
    Parse workout-result statements, e.g.:
    - i just did a PR of 100kg on bench press
    - i did 80kg on squat
    - hit 100kg bench today
    - i just did 100kg squats for 8 reps
    """
    normalized = text.strip().lower()

    # "100kg squats for 8 reps" / "did 100 kg bench for 5 reps" (sets default 1)
    kg_ex_for_reps = re.search(
        r"(?:^|[\s!?.]|did|done|hit|got)\s*(\d+(?:\.\d+)?)\s*kg\s+([a-z][a-z\s]{2,40}?)\s+for\s+(\d+)\s*reps?\b",
        normalized,
    )
    if kg_ex_for_reps:
        ex = _trim_exercise_noise(kg_ex_for_reps.group(2).strip())
        if len(ex) >= 2:
            return {
                "exercise": ex,
                "sets": 1,
                "reps": int(kg_ex_for_reps.group(3)),
                "weight": float(kg_ex_for_reps.group(1)),
            }

    # "squats for 8 reps at 100kg"
    ex_for_reps_kg = re.search(
        r"([a-z][a-z\s]{2,40}?)\s+for\s+(\d+)\s*reps?\s+(?:at|@)\s+(\d+(?:\.\d+)?)\s*kg\b",
        normalized,
    )
    if ex_for_reps_kg:
        ex = _trim_exercise_noise(ex_for_reps_kg.group(1).strip())
        if len(ex) >= 2:
            return {
                "exercise": ex,
                "sets": 1,
                "reps": int(ex_for_reps_kg.group(2)),
                "weight": float(ex_for_reps_kg.group(3)),
            }

    pr_match = re.search(
        r"(?:pr|personal record).{0,30}?(\d+(?:\.\d+)?)\s*kg.{0,25}?(?:on|for)?\s*([a-z][a-z\s]{2,50})",
        normalized,
    )
    if pr_match:
        ex = _trim_exercise_noise(pr_match.group(2).strip().rstrip(".,!?"))
        return {
            "exercise": ex,
            "sets": 1,
            "reps": 1,
            "weight": float(pr_match.group(1)),
        }

    generic_match = re.search(
        r"(?:i\s+(?:just\s+)?did|i\s+hit|i\s+completed|hit).{0,25}?(\d+(?:\.\d+)?)\s*kg.{0,20}?(?:on|for)?\s*([a-z][a-z\s]{2,50})",
        normalized,
    )
    if generic_match:
        ex = _trim_exercise_noise(generic_match.group(2).strip().rstrip(".,!?"))
        return {
            "exercise": ex,
            "sets": 1,
            "reps": 1,
            "weight": float(generic_match.group(1)),
        }

    # "100kg bench" / "100 kg on bench press"
    kg_first = re.search(
        r"(\d+(?:\.\d+)?)\s*kg.{0,12}?(?:on|for)?\s*([a-z][a-z\s]{2,50}?)(?:[.,!?]|$|\s+today|\s+just)",
        normalized,
    )
    if kg_first:
        ex = _trim_exercise_noise(kg_first.group(2).strip().rstrip(".,!?"))
        if len(ex) >= 3:
            return {
                "exercise": ex,
                "sets": 1,
                "reps": 1,
                "weight": float(kg_first.group(1)),
            }

    return None


def _workout_turn_hidden_context(
    *,
    payload: dict,
    duplicate: bool,
    log_failed: bool,
    log_error: str | None,
) -> str:
    """Facts for <kitchen_context>; model must not read this aloud as bookkeeping."""
    ex = payload["exercise"]
    s, r, w = payload["sets"], payload["reps"], payload["weight"]
    if log_failed:
        return (
            f"Workout save to the server FAILED ({log_error or 'unknown'}). "
            "Stay in character: blame chaos on the pass / useless systems — no stack dumps, no JSON."
        )
    if duplicate:
        return (
            f"They already hammered this exact line earlier: {ex}, {s}×{r} @ {w}kg. "
            "They sent it again. Roast the repeat — never say 'logged', 'database', or 'duplicate entry'."
        )
    return (
        f"Their lift (your tone only — not a readout): {ex}, {s} sets × {r} reps @ {w}kg. "
        "Back-office is handled. You respond only as Ramsay: hype, pressure, or disgust — never 'I have saved'."
    )


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

def _resolve_workout_payload(prompt: str, messages: list):
    """
    Resolve workout payload from:
    1) explicit log command
    2) direct workout statement in current prompt
    3) previous user message when prompt asks to "log that"
    """
    payload = _parse_workout_log_command(prompt) or _parse_workout_from_statement(prompt)
    if payload:
        return payload

    if _should_scan_history_for_workout(prompt):
        # Scan previous user messages for a parsable workout statement.
        for msg in reversed(messages[:-1]):
            if msg.get("role") != "user":
                continue
            historical_payload = _parse_workout_log_command(msg.get("content", "")) or _parse_workout_from_statement(msg.get("content", ""))
            if historical_payload:
                return historical_payload
    return None

# ============================================================
# LOGIN PAGE
# ============================================================
def show_login_page():
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        st.title("🥗 Gordon RamsAi")
        st.subheader("Your Fitness & Nutrition Assistant")
        
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
            best_prs = get_best_prs(supabase, user.id, limit=10)
            if best_prs:
                st.table(best_prs)
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
        workout_payload = _resolve_workout_payload(prompt, st.session_state.messages)
        trace_id = None
        if workout_payload:
            signature = _build_workout_signature(workout_payload)
            duplicate = st.session_state.get("last_logged_workout_signature") == signature
            log_failed = False
            log_error: str | None = None
            if not duplicate:
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
                    log_error = str(e)
            hidden = _workout_turn_hidden_context(
                payload=workout_payload,
                duplicate=duplicate and not log_failed,
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

