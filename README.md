## 🔐 Setup & Configuration

This project uses Google Gemini via Streamlit's secrets management. Follow these steps to configure your local environment:

1. Create a `.streamlit` folder in the root directory of the project.
2. Create a `secrets.toml` file inside the `.streamlit` folder.
3. Add your API key to `.streamlit/secrets.toml` with the following contents:
   `[google]
   api_key = "YOUR_GOOGLE_GEMINI_API_KEY_HERE"`
4. Ensure .streamlit/secrets.toml is added to your .gitignore file so it is never committed to the repository.
