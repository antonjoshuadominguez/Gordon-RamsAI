import streamlit as st
from sidebar import sidebar
from ai import generate_response

st.set_page_config(page_title="Gordon RamsAi", page_icon="🥗")

st.title("🥗 Gordon RamsAi")

# Load sidebar
profile = sidebar()

# Initialize chat history
if "messages" not in st.session_state:
    st.session_state.messages = [
        {
            "role": "assistant",
            "content": (
                "Hi there! I'm Gordon RamsAi — your fitness & nutrition assistant. "
                "What can I help you with today? Feel free to ask about exercise plans or meal plans!"
            ),
        }
    ]

# Display chat
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Quick action buttons - show only at start of conversation
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

# Use pending prompt if set
if "pending_prompt" in st.session_state and st.session_state.pending_prompt:
    prompt = st.session_state.pending_prompt
    st.session_state.pending_prompt = None

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})

    with st.chat_message("user"):
        st.markdown(prompt)

    # Generate response
    with st.spinner("Thinking..."):
        try:
            response, usage = generate_response(st.session_state.messages, profile)
        except Exception as e:
            response = f"⚠️ Error generating response: {e}"
            usage = None

    st.session_state.messages.append({"role": "assistant", "content": response})

    with st.chat_message("assistant"):
        st.markdown(response)

    # Update the current chat in session state
    st.session_state.chats[st.session_state.current_chat_id]["messages"] = st.session_state.messages.copy()

