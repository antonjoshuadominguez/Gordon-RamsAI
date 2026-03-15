import streamlit as st

def sidebar():

    st.sidebar.title("🥗 Gordon RamsAi")

    st.sidebar.subheader("User Profile")

    goal = st.sidebar.selectbox(
        "Fitness Goal",
        ["Muscle Gain", "Fat Loss", "Maintenance"]
    )

    weight = st.sidebar.number_input("Weight (kg)", 40, 200)

    height = st.sidebar.number_input("Height (cm)", 140, 220)

    workout_days = st.sidebar.slider("Workout Days per Week", 1, 7)

    diet = st.sidebar.selectbox(
        "Diet Preference",
        ["High Protein", "Low Carb", "Vegetarian", "Balanced"]
    )

    st.sidebar.divider()

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

    for i, chat in enumerate(st.session_state.chats):
        col1, col2, col3 = st.sidebar.columns([3, 1, 1])
        with col1:
            if st.button(chat["name"], key=f"select_{i}"):
                st.session_state.current_chat_id = i
                st.session_state.messages = chat["messages"].copy()
        with col2:
            if st.button("✏️", key=f"rename_{i}"):
                st.session_state[f"renaming_{i}"] = True
        with col3:
            if st.button("🗑️", key=f"delete_{i}"):
                del st.session_state.chats[i]
                if st.session_state.current_chat_id >= len(st.session_state.chats):
                    st.session_state.current_chat_id = max(0, len(st.session_state.chats) - 1)
                if st.session_state.chats:
                    st.session_state.messages = st.session_state.chats[st.session_state.current_chat_id]["messages"].copy()
                else:
                    st.session_state.messages = []
                st.rerun()

        if st.session_state.get(f"renaming_{i}", False):
            new_name = st.sidebar.text_input("New name", value=chat["name"], key=f"rename_input_{i}")
            if st.sidebar.button("Save", key=f"save_{i}"):
                st.session_state.chats[i]["name"] = new_name
                st.session_state[f"renaming_{i}"] = False
                st.rerun()

    if st.sidebar.button("➕ New Chat"):
        new_chat = {"name": f"Chat {len(st.session_state.chats) + 1}", "messages": [
            {
                "role": "assistant",
                "content": (
                    "Hi there! I'm Gordon RamsAi — your fitness & nutrition assistant. "
                    "What can I help you with today? Feel free to ask about exercise plans or meal plans!"
                ),
            }
        ]}
        st.session_state.chats.append(new_chat)
        st.session_state.current_chat_id = len(st.session_state.chats) - 1
        st.session_state.messages = new_chat["messages"].copy()

    return {
        "goal": goal,
        "weight": weight,
        "height": height,
        "workout_days": workout_days,
        "diet": diet
    }