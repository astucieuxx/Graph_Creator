import streamlit as st
import re

st.set_page_config(page_title="Signal", layout="wide", initial_sidebar_state="collapsed")

# Hide Streamlit chrome
st.markdown("""
<style>
  #MainMenu, header, footer { display: none !important; }
  .block-container { padding: 0 !important; max-width: 100% !important; }
  iframe { border: none !important; }
</style>
""", unsafe_allow_html=True)

with open("index.html", "r") as f:
    html = f.read()

try:
    api_key = st.secrets["ANTHROPIC_API_KEY"]
except Exception:
    api_key = ""

# Inject overrides as the FIRST script in <head>
# This runs before any other JS, so it overrides API_URL and getApiKey()
inject = f"""<script>
// Streamlit overrides — injected at deploy time
window.__SIGNAL_API_KEY__ = "{api_key}";
window.__SIGNAL_API_URL__ = "https://api.anthropic.com/v1/messages";
</script>"""

html = html.replace("<head>", "<head>\n" + inject, 1)

# Patch the JS to use window overrides if available
patch = """
<script>
// Apply Streamlit overrides after page scripts load
document.addEventListener('DOMContentLoaded', function() {
  if (window.__SIGNAL_API_URL__) {
    window.__patchedApiUrl__ = window.__SIGNAL_API_URL__;
  }
  if (window.__SIGNAL_API_KEY__) {
    window.__patchedApiKey__ = window.__SIGNAL_API_KEY__;
  }
});
</script>"""
html = html.replace("</body>", patch + "\n</body>", 1)

# Replace API_URL references to use the patched value if available
html = re.sub(
    r'const API_URL\s*=.*?;',
    'const API_URL = window.__SIGNAL_API_URL__ || (window.location.hostname === "localhost" ? "http://localhost:8080/api/messages" : "/api/messages");',
    html,
    flags=re.DOTALL
)

# Replace getApiKey() to use injected key if available
html = html.replace(
    "return localStorage.getItem(API_KEY_STORAGE_KEY) || \"\";",
    "return window.__SIGNAL_API_KEY__ || localStorage.getItem(API_KEY_STORAGE_KEY) || \"\";"
)

st.components.v1.html(html, height=920, scrolling=False)
