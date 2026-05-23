"""
Decentralized AI Memory Layer — Streamlit Dashboard

Glassmorphism 2.0 Dark Design:
- Background: #0D0D14
- Accent: #6366F1 (indigo)
- Frosted glass panels with backdrop-filter blur(16px)
- 3-column layout: Memory Graph | Chat | Live Memory Feed
- Bottom stats bar with token savings, latency, total memories
"""

import streamlit as st
import requests
import json
import os
from datetime import datetime
from pathlib import Path

# Page config
st.set_page_config(
    page_title="AI Memory Layer",
    layout="wide",
    initial_sidebar_state="collapsed",
    page_icon="🧠",
)

# ==========================================
# GLASSMORPHISM 2.0 CSS
# ==========================================

GLASSMORPHISM_CSS = """
<style>
/* Import fonts */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

/* Global Styles */
* {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}

/* Main background */
.stApp {
    background: #0D0D14;
    color: #E2E8F0;
}

/* Hide default Streamlit elements */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}
[data-testid="stToolbar"] {visibility: hidden;}
[data-testid="stDecoration"] {display: none;}
[data-testid="stStatusWidget"] {display: none;}
#MainMenu {display: none;}
footer {display: none;}
header {display: none;}

/* Block containers */
[data-testid="block-container"] {
    padding: 0 !important;
    max-width: 100% !important;
}

/* Glass Panel Base */
.glass-panel {
    background: rgba(30, 30, 45, 0.6);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border: 1px solid rgba(99, 102, 241, 0.2);
    border-radius: 16px;
    padding: 20px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
    transition: all 0.3s ease;
}

.glass-panel:hover {
    border-color: rgba(99, 102, 241, 0.4);
    box-shadow: 0 8px 32px rgba(99, 102, 241, 0.1);
}

/* Memory Passport Card */
.memory-passport {
    background: linear-gradient(135deg, rgba(30, 30, 45, 0.8) 0%, rgba(20, 20, 35, 0.9) 100%);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border: 2px solid #6366F1;
    border-radius: 16px;
    padding: 20px;
    box-shadow: 
        0 0 20px rgba(99, 102, 241, 0.3),
        0 0 40px rgba(99, 102, 241, 0.1),
        inset 0 1px 0 rgba(255, 255, 255, 0.1);
    animation: glow 3s ease-in-out infinite;
}

@keyframes glow {
    0%, 100% { box-shadow: 0 0 20px rgba(99, 102, 241, 0.3), 0 0 40px rgba(99, 102, 241, 0.1); }
    50% { box-shadow: 0 0 30px rgba(99, 102, 241, 0.5), 0 0 60px rgba(99, 102, 241, 0.2); }
}

/* Stats Bar */
.stats-bar {
    background: rgba(15, 15, 25, 0.9);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border-top: 1px solid rgba(99, 102, 241, 0.3);
    padding: 12px 24px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    z-index: 1000;
}

.stat-item {
    display: flex;
    align-items: center;
    gap: 8px;
    color: #94A3B8;
    font-size: 14px;
}

.stat-value {
    color: #6366F1;
    font-weight: 600;
    font-size: 18px;
}

.stat-label {
    color: #64748B;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

/* Memory Card Animations */
@keyframes slideIn {
    from {
        opacity: 0;
        transform: translateX(20px);
    }
    to {
        opacity: 1;
        transform: translateX(0);
    }
}

.memory-card {
    background: rgba(30, 30, 45, 0.6);
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
    border: 1px solid rgba(99, 102, 241, 0.15);
    border-radius: 12px;
    padding: 12px 16px;
    margin-bottom: 8px;
    animation: slideIn 0.4s ease-out;
    transition: all 0.2s ease;
}

.memory-card:hover {
    border-color: rgba(99, 102, 241, 0.4);
    transform: translateY(-2px);
}

/* Memory Type Tags */
.tag-episodic {
    background: linear-gradient(135deg, #6366F1 0%, #8B5CF6 100%);
    color: white;
    padding: 4px 10px;
    border-radius: 20px;
    font-size: 11px;
    font-weight: 500;
    display: inline-flex;
    align-items: center;
    gap: 4px;
}

.tag-semantic {
    background: linear-gradient(135deg, #06B6D4 0%, #0891B2 100%);
    color: white;
    padding: 4px 10px;
    border-radius: 20px;
    font-size: 11px;
    font-weight: 500;
    display: inline-flex;
    align-items: center;
    gap: 4px;
}

.tag-procedural {
    background: linear-gradient(135deg, #F59E0B 0%, #D97706 100%);
    color: white;
    padding: 4px 10px;
    border-radius: 20px;
    font-size: 11px;
    font-weight: 500;
    display: inline-flex;
    align-items: center;
    gap: 4px;
}

.tag-associative {
    background: linear-gradient(135deg, #10B981 0%, #059669 100%);
    color: white;
    padding: 4px 10px;
    border-radius: 20px;
    font-size: 11px;
    font-weight: 500;
    display: inline-flex;
    align-items: center;
    gap: 4px;
}

/* Chat Messages */
.user-message {
    background: linear-gradient(135deg, #6366F1 0%, #4F46E5 100%);
    color: white;
    padding: 12px 16px;
    border-radius: 16px 16px 4px 16px;
    margin: 8px 0;
    max-width: 85%;
    margin-left: auto;
}

.assistant-message {
    background: rgba(45, 45, 65, 0.8);
    backdrop-filter: blur(8px);
    color: #E2E8F0;
    padding: 12px 16px;
    border-radius: 16px 16px 16px 4px;
    margin: 8px 0;
    max-width: 85%;
    border: 1px solid rgba(99, 102, 241, 0.2);
}

/* Column Headers */
.column-header {
    color: #E2E8F0;
    font-size: 14px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 1px;
    padding-bottom: 12px;
    border-bottom: 1px solid rgba(99, 102, 241, 0.3);
    margin-bottom: 16px;
}

/* Input styling */
.stTextInput > div > div > input {
    background: rgba(30, 30, 45, 0.8) !important;
    border: 1px solid rgba(99, 102, 241, 0.3) !important;
    border-radius: 12px !important;
    color: #E2E8F0 !important;
    padding: 12px !important;
}

.stTextInput > div > div > input:focus {
    border-color: #6366F1 !important;
    box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.2) !important;
}

/* Button styling */
.stButton > button {
    background: linear-gradient(135deg, #6366F1 0%, #4F46E5 100%) !important;
    color: white !important;
    border: none !important;
    border-radius: 12px !important;
    padding: 10px 24px !important;
    font-weight: 500 !important;
    transition: all 0.2s ease !important;
}

.stButton > button:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(99, 102, 241, 0.4) !important;
}

/* Scrollbar */
::-webkit-scrollbar {
    width: 6px;
}

::-webkit-scrollbar-track {
    background: rgba(30, 30, 45, 0.5);
}

::-webkit-scrollbar-thumb {
    background: rgba(99, 102, 241, 0.5);
    border-radius: 3px;
}

::-webkit-scrollbar-thumb:hover {
    background: rgba(99, 102, 241, 0.7);
}

/* Graph Node Styling */
.graph-node {
    background: rgba(45, 45, 65, 0.6);
    backdrop-filter: blur(8px);
    border: 1px solid rgba(99, 102, 241, 0.3);
    border-radius: 8px;
    padding: 8px 12px;
    margin: 4px;
    font-size: 12px;
    display: inline-block;
}

/* Latency Indicator */
.latency-good { color: #10B981; }
.latency-medium { color: #F59E0B; }
.latency-bad { color: #EF4444; }

/* Responsive adjustments */
@media (max-width: 1200px) {
    .stats-bar {
        flex-wrap: wrap;
        gap: 16px;
    }
}
</style>
"""

st.markdown(GLASSMORPHISM_CSS, unsafe_allow_html=True)

# ==========================================
# API CLIENT
# ==========================================

API_BASE = os.environ.get("API_BASE_URL") or st.secrets.get("API_BASE_URL", "http://localhost:8000")

# Auth token (JWT or API key)
auth_token = st.text_input(
    "Auth Token (JWT or API Key)",
    type="password",
    help="Enter your JWT or API key for production mode. Leave empty for demo mode."
)


def api_get(endpoint: str, params: dict = None):
    """Make GET request to API."""
    headers = {}
    if auth_token:
        # Check if it's an API key (starts with nxm_) or JWT
        if auth_token.startswith("nxm_"):
            headers["Authorization"] = f"ApiKey {auth_token}"
        else:
            headers["Authorization"] = f"Bearer {auth_token}"
    try:
        r = requests.get(f"{API_BASE}{endpoint}", params=params, headers=headers, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"API Error: {e}")
        return None


def api_post(endpoint: str, data: dict = None):
    """Make POST request to API."""
    headers = {}
    if auth_token:
        # Check if it's an API key (starts with nxm_) or JWT
        if auth_token.startswith("nxm_"):
            headers["Authorization"] = f"ApiKey {auth_token}"
        else:
            headers["Authorization"] = f"Bearer {auth_token}"
    try:
        r = requests.post(f"{API_BASE}{endpoint}", json=data or {}, headers=headers, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"API Error: {e}")
        return None


# ==========================================
# SESSION STATE INITIALIZATION
# ==========================================

if "user_id" not in st.session_state:
    st.session_state.user_id = "7e082e59-86b3-428f-a17a-e84480daf072"

if "messages" not in st.session_state:
    st.session_state.messages = []

if "stats" not in st.session_state:
    st.session_state.stats = {
        "total_memories": 0,
        "episodic_count": 0,
        "semantic_count": 0,
        "procedural_count": 0,
        "graph_nodes": 0,
    }

if "token_savings" not in st.session_state:
    st.session_state.token_savings = 0

if "last_latency" not in st.session_state:
    st.session_state.last_latency = 0

if "recent_memories" not in st.session_state:
    st.session_state.recent_memories = []


# ==========================================
# HELPER FUNCTIONS
# ==========================================

def get_emoji_label(memory_type: str) -> str:
    """Get emoji label for memory type."""
    labels = {
        "episodic": "\U0001f9e0 Episodic",
        "semantic": "\U0001f50d Semantic",
        "procedural": "\u2699\ufe0f Procedural",
        "associative": "\U0001f578\ufe0f Associative",
    }
    return labels.get(memory_type, memory_type)


def get_tag_class(memory_type: str) -> str:
    """Get CSS class for memory type tag."""
    classes = {
        "episodic": "tag-episodic",
        "semantic": "tag-semantic",
        "procedural": "tag-procedural",
        "associative": "tag-associative",
    }
    return classes.get(memory_type, "tag-episodic")


def format_time(iso_string: str) -> str:
    """Format ISO time string to readable format."""
    try:
        dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo)
        diff = now - dt
        if diff.days > 0:
            return f"{diff.days}d ago"
        elif diff.seconds > 3600:
            return f"{diff.seconds // 3600}h ago"
        elif diff.seconds > 60:
            return f"{diff.seconds // 60}m ago"
        else:
            return "just now"
    except:
        return iso_string


def load_stats():
    """Load memory statistics from API."""
    stats = api_get(f"/api/v1/memory/stats/{st.session_state.user_id}")
    if stats:
        st.session_state.stats = stats


def load_recent_memories():
    """Load recent memories for the live feed."""
    memories = api_get(
        f"/api/v1/memory/recent/{st.session_state.user_id}",
        params={"limit": 10}
    )
    if memories:
        st.session_state.recent_memories = memories


def load_graph_nodes():
    """Load knowledge graph nodes."""
    return api_get(f"/api/v1/agents/{st.session_state.user_id}/graph/nodes") or []


def load_graph_edges():
    """Load knowledge graph edges."""
    return api_get(f"/api/v1/agents/{st.session_state.user_id}/graph/edges") or []


# ==========================================
# MAIN LAYOUT
# ==========================================

# User ID input (top bar)
col_top1, col_top2, col_top3, col_top4 = st.columns([1, 2, 1, 1])
with col_top1:
    st.markdown("### \U0001f9e0 AI Memory Layer")
with col_top2:
    user_input = st.text_input(
        "User ID",
        value=st.session_state.user_id,
        label_visibility="collapsed",
        key="user_id_input",
    )
    if user_input != st.session_state.user_id:
        st.session_state.user_id = user_input
        st.rerun()
with col_top3:
    if st.button("\u21bb Refresh", use_container_width=True):
        load_stats()
        load_recent_memories()
        st.rerun()
with col_top4:
    # Auth badge
    if auth_token:
        st.markdown("🔒 **Authenticated**")
    else:
        st.markdown("🔓 **Demo Mode**")

st.markdown("---")

# Load initial data
if not st.session_state.recent_memories:
    load_recent_memories()
    load_stats()

# 3-Column Layout
col_left, col_center, col_right = st.columns([1, 2, 1])

# ==========================================
# LEFT COLUMN: Memory Graph
# ==========================================

with col_left:
    st.markdown('<div class="column-header">\U0001f578\ufe0f Knowledge Graph</div>', unsafe_allow_html=True)

    # Memory Passport Card
    st.markdown("""
    <div class="memory-passport">
        <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 12px;">
            <div style="width: 48px; height: 48px; background: linear-gradient(135deg, #6366F1 0%, #8B5CF6 100%); border-radius: 12px; display: flex; align-items: center; justify-content: center; font-size: 24px;">
                \U0001f9e0
            </div>
            <div>
                <div style="color: #E2E8F0; font-weight: 600; font-size: 16px;">Memory Passport</div>
                <div style="color: #94A3B8; font-size: 12px;">User Identity</div>
            </div>
        </div>
        <div style="background: rgba(99, 102, 241, 0.1); border-radius: 8px; padding: 8px 12px;">
            <div style="color: #6366F1; font-size: 13px; font-family: monospace;">"""+st.session_state.user_id+"""</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Graph Nodes Panel
    st.markdown('<div class="glass-panel">', unsafe_allow_html=True)
    st.markdown("**Graph Nodes**")

    nodes = load_graph_nodes()
    if nodes:
        for node in nodes:
            node_type = node.get("type", "concept")
            emoji = {"domain": "\U0001f4ca", "technology": "\U0001f4bb", "concept": "\U0001f4a1",
                     "framework": "\U0001f6e0\ufe0f", "tool": "\U0001f527"}.get(node_type, "\U0001f4c1")
            st.markdown(f"""
            <div class="memory-card" style="animation-delay: {nodes.index(node) * 0.1}s">
                <span style="font-size: 16px;">{emoji}</span>
                <strong>{node['label']}</strong>
                <div style="color: #94A3B8; font-size: 11px; margin-top: 4px;">{node_type}</div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info("No graph nodes yet")

    # Graph Edges Panel
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("**Graph Connections**")

    edges = load_graph_edges()
    if edges:
        # Create a mapping of node IDs to labels
        node_map = {str(n["id"]): n["label"] for n in nodes}
        for edge in edges[:8]:
            from_label = node_map.get(edge["from_node_id"], "?")
            to_label = node_map.get(edge["to_node_id"], "?")
            st.markdown(f"""
            <div class="memory-card">
                <div style="display: flex; align-items: center; gap: 8px; font-size: 12px;">
                    <span style="color: #6366F1;">{from_label}</span>
                    <span style="color: #64748B;">→</span>
                    <span style="color: #6366F1;">{to_label}</span>
                </div>
                <div style="color: #94A3B8; font-size: 10px; margin-top: 4px;">
                    {edge['relation']} (weight: {edge.get('weight', 1.0)})
                </div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info("No graph edges yet")

    st.markdown('</div>', unsafe_allow_html=True)


# ==========================================
# CENTER COLUMN: Chat Interface
# ==========================================

with col_center:
    st.markdown('<div class="column-header">\U0001f4ac Memory-Enhanced Chat</div>', unsafe_allow_html=True)

    # Chat messages container (scrollable)
    chat_container = st.container(height=500, border=False)

    with chat_container:
        for msg in st.session_state.messages:
            if msg["role"] == "user":
                st.markdown(f"""
                <div class="user-message">{msg['content']}</div>
                """, unsafe_allow_html=True)
            else:
                # Show metadata if available
                metadata_html = ""
                if msg.get("metadata"):
                    m = msg["metadata"]
                    latency = m.get("latency_ms", 0)
                    latency_class = "latency-good" if latency < 1000 else "latency-medium" if latency < 3000 else "latency-bad"
                    tokens = m.get("completion_tokens", 0)

                    metadata_html = f"""
                    <div style="margin-top: 8px; padding-top: 8px; border-top: 1px solid rgba(99, 102, 241, 0.2); display: flex; gap: 16px; font-size: 11px;">
                        <span class="{latency_class}">⚡ {latency:.0f}ms</span>
                        <span style="color: #94A3B8;">🔤 {tokens} tokens</span>
                    </div>
                    """

                st.markdown(f"""
                <div class="assistant-message">
                    {msg['content']}
                    {metadata_html}
                </div>
                """, unsafe_allow_html=True)

    # Chat input
    user_message = st.chat_input("Message with memory...")

    if user_message:
        # Add user message to chat
        st.session_state.messages.append({"role": "user", "content": user_message})

        # Call RAG API
        with st.spinner("Thinking with memory context..."):
            response = api_post("/api/v1/rag/chat", {
                "user_id": st.session_state.user_id,
                "message": user_message,
                "include_episodic": True,
                "include_semantic": True,
                "include_procedural": True,
                "include_graph": True,
                "top_k": 5,
            })

        if response:
            # Add assistant response with metadata
            st.session_state.messages.append({
                "role": "assistant",
                "content": response.get("reply", "I couldn't generate a response."),
                "metadata": {
                    "latency_ms": response.get("latency_ms", 0),
                    "completion_tokens": response.get("completion_tokens", 0),
                    "prompt_tokens": response.get("prompt_tokens", 0),
                }
            })

            # Update stats
            st.session_state.last_latency = response.get("latency_ms", 0)

            # Update token savings (simulated: showing reduction vs non-RAG)
            if response.get("prompt_tokens", 0) > 0:
                # Simulate 40% token savings with RAG context
                st.session_state.token_savings += int(response.get("prompt_tokens", 0) * 0.4)

            # Refresh recent memories
            load_recent_memories()
            load_stats()
            st.rerun()
        else:
            # If API failed, we still want to keep the user message but show error
            st.error("Connection lost or backend error. Please try again.")
            # Note: no rerun here so error persists

    # Quick Actions
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<div class="glass-panel">', unsafe_allow_html=True)
    st.markdown("**Quick Actions**")

    col_q1, col_q2 = st.columns(2)
    with col_q1:
        if st.button("\U0001f4e2 Add Memory", use_container_width=True):
            st.session_state.show_add_memory = True
    with col_q2:
        if st.button("\U0001f50d Search Memory", use_container_width=True):
            st.session_state.show_search = True

    # Add Memory Form (conditional)
    if st.session_state.get("show_add_memory", False):
        with st.expander("Add New Memory", expanded=True):
            with st.form("add_memory_form"):
                content = st.text_area("Memory Content", height=100)
                mem_type = st.selectbox("Memory Type", ["episodic", "semantic"])
                tags = st.text_input("Tags (comma-separated)")
                submit = st.form_submit_button("Store Memory")

                if submit and content:
                    result = api_post(f"/api/v1/agents/{st.session_state.user_id}/episodes", {
                        "session_id": f"manual_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                        "content": content,
                        "tags": [t.strip() for t in tags.split(",") if t.strip()],
                        "metadata": {"source": "manual", "type": mem_type},
                        "store_episodic": True,
                    })
                    if result and result.get("id"):
                        st.success("Memory stored!")
                        load_recent_memories()
                        load_stats()
                        st.session_state.show_add_memory = False
                        st.rerun()

    # Search Form (conditional)
    if st.session_state.get("show_search", False):
        with st.expander("Search Memories", expanded=True):
            search_query = st.text_input("Search Query")
            if st.button("Search") and search_query:
                results = api_post(f"/api/v1/agents/{st.session_state.user_id}/semantic/search", {
                    "query": search_query,
                    "k": 5,
                })
                if results:
                    st.markdown("**Results:**")
                    for r in results:
                        st.markdown(f"""
                        <div class="memory-card">
                            <div style="display: flex; justify-content: space-between; align-items: center;">
                                <span class="tag-semantic">\U0001f50d Semantic</span>
                                <span style="color: #6366F1; font-size: 12px;">{(r.get('similarity', 0) * 100):.1f}% match</span>
                            </div>
                            <div style="margin-top: 8px; font-size: 13px; color: #E2E8F0;">
                                {r.get('summary', r.get('content_preview', ''))[:150]}
                            </div>
                        </div>
                        """, unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)


# ==========================================
# RIGHT COLUMN: Live Memory Feed
# ==========================================

with col_right:
    st.markdown('<div class="column-header">\u26a1 Live Memory Feed</div>', unsafe_allow_html=True)

    st.markdown('<div class="glass-panel" style="max-height: 600px; overflow-y: auto;">', unsafe_allow_html=True)

    recent = st.session_state.recent_memories

    if recent:
        for i, mem in enumerate(recent):
            mem_type = mem.get("memory_type", "episodic")
            tag_class = get_tag_class(mem_type)
            emoji_label = get_emoji_label(mem_type)
            time_ago = format_time(mem.get("created_at", ""))
            content = mem.get("content", "")[:120]
            if len(mem.get("content", "")) > 120:
                content += "..."

            st.markdown(f"""
            <div class="memory-card" style="animation-delay: {i * 0.1}s">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                    <span class="{tag_class}">{emoji_label}</span>
                    <span style="color: #64748B; font-size: 10px;">{time_ago}</span>
                </div>
                <div style="color: #CBD5E1; font-size: 13px; line-height: 1.4;">
                    {content}
                </div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <div style="text-align: center; padding: 40px 20px; color: #64748B;">
            <div style="font-size: 48px; margin-bottom: 16px;">\U0001f4ed</div>
            <div>No memories yet</div>
            <div style="font-size: 12px; margin-top: 8px;">Start chatting to create memories</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)

    # Memory Summary Panel
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<div class="glass-panel">', unsafe_allow_html=True)
    st.markdown("**Memory Distribution**")

    stats = st.session_state.stats
    total = max(stats.get("total_memories", 0), 1)

    # Visual distribution bars
    for label, count, color in [
        ("\U0001f9e0 Episodic", stats.get("episodic_count", 0), "#6366F1"),
        ("\U0001f50d Semantic", stats.get("semantic_count", 0), "#06B6D4"),
        ("\u2699\ufe0f Procedural", stats.get("procedural_count", 0), "#F59E0B"),
        ("\U0001f578\ufe0f Graph", stats.get("graph_node_count", 0), "#10B981"),
    ]:
        pct = (count / total * 100) if total > 0 else 0
        st.markdown(f"""
        <div style="margin-bottom: 12px;">
            <div style="display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 4px;">
                <span style="color: #94A3B8;">{label}</span>
                <span style="color: {color}; font-weight: 600;">{count}</span>
            </div>
            <div style="background: rgba(30, 30, 45, 0.8); border-radius: 4px; height: 6px; overflow: hidden;">
                <div style="background: {color}; height: 100%; width: {min(pct, 100)}%; border-radius: 4px; transition: width 0.5s ease;"></div>
            </div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)


# ==========================================
# BOTTOM STATS BAR
# ==========================================

stats = st.session_state.stats

latency = st.session_state.last_latency
latency_color = "#10B981" if latency < 1000 else "#F59E0B" if latency < 3000 else "#EF4444"
latency_icon = "\u2714" if latency < 1000 else "\u26a0" if latency < 3000 else "\u2716"

st.markdown(f"""
<div class="stats-bar">
    <div class="stat-item">
        <span style="color: #6366F1; font-size: 20px;">\U0001f4c8</span>
        <div>
            <div class="stat-label">Token Savings</div>
            <div class="stat-value">{st.session_state.token_savings:,}</div>
        </div>
    </div>
    <div class="stat-item">
        <span style="color: {latency_color}; font-size: 20px;">{latency_icon}</span>
        <div>
            <div class="stat-label">Latency</div>
            <div class="stat-value" style="color: {latency_color};">{latency:.0f}ms</div>
        </div>
    </div>
    <div class="stat-item">
        <span style="color: #6366F1; font-size: 20px;">\U0001f9e0</span>
        <div>
            <div class="stat-label">Total Memories</div>
            <div class="stat-value">{stats.get('total_memories', 0):,}</div>
        </div>
    </div>
    <div class="stat-item">
        <span style="color: #10B981; font-size: 20px;">\U0001f4ca</span>
        <div>
            <div class="stat-label">Episodic</div>
            <div class="stat-value" style="color: #6366F1;">{stats.get('episodic_count', 0)}</div>
        </div>
    </div>
    <div class="stat-item">
        <span style="color: #06B6D4; font-size: 20px;">\U0001f50d</span>
        <div>
            <div class="stat-label">Semantic</div>
            <div class="stat-value" style="color: #06B6D4;">{stats.get('semantic_count', 0)}</div>
        </div>
    </div>
    <div class="stat-item">
        <span style="color: #F59E0B; font-size: 20px;">\u2699\ufe0f</span>
        <div>
            <div class="stat-label">Procedural</div>
            <div class="stat-value" style="color: #F59E0B;">{stats.get('procedural_count', 0)}</div>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

# Add bottom padding to prevent content from being hidden behind stats bar
st.markdown("<br><br><br><br>", unsafe_allow_html=True)
