import streamlit as st

st.set_page_config(page_title="Signal", layout="wide", initial_sidebar_state="collapsed")

# Hide Streamlit chrome
st.markdown("""
<style>
  #MainMenu, header, footer { display: none !important; }
  .block-container { padding: 0 !important; max-width: 100% !important; }
  iframe { border: none !important; }
</style>
""", unsafe_allow_html=True)

# Read HTML and inject API key from Streamlit secrets
with open("index.html", "r") as f:
    html = f.read()

try:
    api_key = st.secrets["ANTHROPIC_API_KEY"]
    # Inject key and switch to direct browser calls (no proxy needed on HTTPS)
    html = html.replace(
        'const API_URL = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1"\n  ? "http://localhost:8080/api/messages"\n  : "/api/messages";',
        'const API_URL = "https://api.anthropic.com/v1/messages";'
    )
    html = html.replace('const MODEL   = "claude-sonnet-4-5";',
        f'const MODEL = "claude-sonnet-4-5";\nfunction getApiKey() {{ return "{api_key}"; }}'
    )
except Exception:
    pass  # Let the user set the key via the UI

st.components.v1.html(html, height=900, scrolling=False)
