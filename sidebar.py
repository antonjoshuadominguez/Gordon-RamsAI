import streamlit as st
from auth_utils import (
    get_user_profile, logout_user, get_supabase_client,
    list_conversations, create_conversation, rename_conversation,
    delete_conversation, list_conversation_messages, create_conversation_message
)

DEFAULT_ASSISTANT_MESSAGE = (
    "Hi there! I'm Gordon RamsAi — your fitness & nutrition assistant. "
    "What can I help you with today? Feel free to ask about exercise plans or meal plans!"
)

def render_sidebar():
    """Render the sidebar with navigation and user profile."""
    
    st.sidebar.title("🥗 Gordon RamsAi")
    
    # Get current user from session state
    user = st.session_state.get("user", None)
    profile = st.session_state.get("profile", None)
    
    if user and profile:
        st.sidebar.write(f"Welcome, **{profile.get('username', 'User')}**! 👋")
        st.sidebar.divider()
        
        # Navigation Links
        st.sidebar.subheader("Navigation")
        
        col1, col2, col3 = st.sidebar.columns(3)
        with col1:
            if st.button("💬 Chat", use_container_width=True):
                st.session_state.current_page = "chat"
                st.rerun()
        
        with col2:
            if st.button("👤 Profile", use_container_width=True):
                st.session_state.current_page = "profile"
                st.rerun()
        
        with col3:
            if st.button("🚪 Logout", use_container_width=True):
                logout_user()
                st.session_state.user = None
                st.session_state.profile = None
                st.session_state.current_page = "login"
                st.rerun()
        
        st.sidebar.divider()
        
        # Chat History Section
        if "chats" not in st.session_state:
            st.session_state.chats = [{"name": "Chat 1", "messages": [
                {
                    "role": "assistant",
                    "content": (
                        "Hi there! I'm Gordon RamsAi — your fitness & nutrition assistant. "
                        "What can I help you with today? Feel free to ask about exercise plans or meal plans!"
                    ),
                }
            ]}]

        if "current_chat_id" not in st.session_state:
            st.session_state.current_chat_id = 0

        st.sidebar.subheader("Chats")

        # Keep chat list synced with DB on each render (preserve gemini_session per chat id).
        try:
            supabase = get_supabase_client()
            db_chats = list_conversations(supabase, user.id)
            if db_chats:
                old_by_id = {
                    c.get("id"): c
                    for c in st.session_state.get("chats", [])
                    if c.get("id")
                }
                merged_chats = []
                for chat in db_chats:
                    cid = chat["id"]
                    entry = {"id": cid, "name": chat.get("title", "Chat"), "messages": []}
                    prev = old_by_id.get(cid)
                    if prev and prev.get("gemini_session") is not None:
                        entry["gemini_session"] = prev["gemini_session"]
                    merged_chats.append(entry)
                st.session_state.chats = merged_chats
                if st.session_state.current_chat_id >= len(st.session_state.chats):
                    st.session_state.current_chat_id = 0
        except Exception:
            supabase = None

        for i, chat in enumerate(st.session_state.chats):
            col1, col2, col3 = st.sidebar.columns([3, 1, 1])
            with col1:
                if st.button(chat["name"], key=f"select_{i}"):
                    st.session_state.current_chat_id = i
                    try:
                        if supabase and chat.get("id"):
                            rows = list_conversation_messages(supabase, chat["id"])
                            st.session_state.messages = [
                                {
                                    "id": row.get("id"),
                                    "role": row.get("role"),
                                    "content": row.get("content"),
                                    "lf_trace_id": row.get("langfuse_trace_id"),
                                }
                                for row in rows
                            ]
                        else:
                            st.session_state.messages = chat["messages"].copy()
                    except Exception:
                        st.session_state.messages = chat["messages"].copy()
                    st.session_state.current_page = "chat"
                    st.rerun()
            with col2:
                if st.button("✏️", key=f"rename_{i}"):
                    st.session_state[f"renaming_{i}"] = True
            with col3:
                if st.button("🗑️", key=f"delete_{i}"):
                    try:
                        if supabase and chat.get("id"):
                            delete_conversation(supabase, chat["id"], user.id)
                    except Exception:
                        pass
                    del st.session_state.chats[i]
                    if st.session_state.current_chat_id >= len(st.session_state.chats):
                        st.session_state.current_chat_id = max(0, len(st.session_state.chats) - 1)
                    if st.session_state.chats:
                        selected = st.session_state.chats[st.session_state.current_chat_id]
                        try:
                            if supabase and selected.get("id"):
                                rows = list_conversation_messages(supabase, selected["id"])
                                st.session_state.messages = [
                                    {
                                        "id": row.get("id"),
                                        "role": row.get("role"),
                                        "content": row.get("content"),
                                        "lf_trace_id": row.get("langfuse_trace_id"),
                                    }
                                    for row in rows
                                ]
                            else:
                                st.session_state.messages = selected["messages"].copy()
                        except Exception:
                            st.session_state.messages = selected["messages"].copy()
                    else:
                        st.session_state.messages = []
                    st.rerun()

            if st.session_state.get(f"renaming_{i}", False):
                new_name = st.sidebar.text_input("New name", value=chat["name"], key=f"rename_input_{i}")
                if st.sidebar.button("Save", key=f"save_{i}"):
                    st.session_state.chats[i]["name"] = new_name
                    try:
                        if supabase and chat.get("id"):
                            rename_conversation(supabase, chat["id"], user.id, new_name)
                    except Exception:
                        pass
                    st.session_state[f"renaming_{i}"] = False
                    st.rerun()

        if st.sidebar.button("➕ New Chat", use_container_width=True):
            new_chat = {"name": f"Chat {len(st.session_state.chats) + 1}", "messages": [
                {
                    "id": None,
                    "role": "assistant",
                    "content": DEFAULT_ASSISTANT_MESSAGE,
                }
            ]}
            try:
                if supabase:
                    created = create_conversation(supabase, user.id, new_chat["name"])
                    if created:
                        new_chat["id"] = created["id"]
                        inserted = create_conversation_message(supabase, created["id"], "assistant", DEFAULT_ASSISTANT_MESSAGE)
                        if inserted:
                            new_chat["messages"][0]["id"] = inserted.get("id")
            except Exception:
                pass
            st.session_state.chats.append(new_chat)
            st.session_state.current_chat_id = len(st.session_state.chats) - 1
            st.session_state.messages = new_chat["messages"].copy()
            st.session_state.current_page = "chat"
            st.rerun()
    else:
        st.sidebar.write("Please log in to continue.")