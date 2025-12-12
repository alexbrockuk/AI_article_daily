import os
import json
import time
import smtplib
import urllib.request
import feedparser
import random
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from openai import OpenAI
from duckduckgo_search import DDGS
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURATION ---
CONFIG = {
    "storage_dir": os.environ.get("AI_SCANNER_STORAGE", "ai_scanner_storage"),
    "seen_file": "seen_store.json",
    
    "scan_depth": 25,  
    "max_email_items": 6, 

    "jmir_feed": "https://ai.jmir.org/feed/atom",
    
    "arxiv_query": (
        "(cat:cs.HC OR cat:cs.CY OR cat:cs.SI) AND "
        "(all:advertising OR all:marketing OR all:brand OR "
        "all:consumer OR all:behavioral OR all:psychology OR "
        "all:persuasion OR all:misinformation OR all:social_media OR "
        "all:narrative OR all:creative OR all:adoption)"
    ),

    # TWITTER/X TARGETS
    "twitter_hashtags": [
        "#GenerativeAI", "#LLM", "#AIArt", "#PromptEngineering", 
        "#MarketingAI", "#AIEthics", "#AIUX"
    ],

    "positive_keywords": [
        "communication", "agency", "marketing", "advertising", "brand", 
        "behavior", "psycholog", "persua", "nudge", "decision", 
        "user experience", "ux", "interface", "design", "chatbot", 
        "conversational", "narrative", "social media", "adoption",
        "consumer", "trust", "ethics", "generative"
    ],
    
    "negative_keywords": [
        "radiology", "tumor", "cancer", "surgery", "surgical", 
        "prognosis", "diagnosis", "diagnostic", "clinical trial", 
        "genomic", "protein", "molecular", "scan", "mri", "ct image",
        "reinforcement learning", "neural network architecture", 
        "gradient descent", "logit", "quantization", "bit-flip"
    ],
    
    "email_sender": os.environ.get("EMAIL_ADDRESS"),
    "email_password": os.environ.get("EMAIL_PASSWORD"),
    "email_recipient": os.environ.get("EMAIL_ADDRESS")
}

# --- SETUP ---
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# --- FUNCTIONS ---

def get_seen_ids():
    path = os.path.join(CONFIG["storage_dir"], CONFIG["seen_file"])
    if not os.path.exists(path): return []
    try:
        with open(path, 'r') as f:
            return json.load(f).get("seen_ids", [])
    except json.JSONDecodeError: return []

def save_seen_id(article_id, seen_ids):
    if article_id not in seen_ids: seen_ids.append(article_id)
    path = os.path.join(CONFIG["storage_dir"], CONFIG["seen_file"])
    os.makedirs(CONFIG["storage_dir"], exist_ok=True)
    with open(path, 'w') as f:
        json.dump({"seen_ids": seen_ids, "last_updated": str(datetime.now())}, f, indent=2)

def is_relevant(title, abstract):
    text = (title + " " + abstract).lower()
    for word in CONFIG["negative_keywords"]:
        if word in text: return False
    for word in CONFIG["positive_keywords"]:
        if word in text: return True
    return False 

# --- NEW: SEARCH ENGINE SIDE-DOOR FOR TWITTER ---
def fetch_twitter_buzz():
    """
    Picks 2 random hashtags and checks X.com via DuckDuckGo.
    Returns a 'Buzz Report' object if interesting stuff is found.
    """
    print("--- Checking Twitter/X Buzz (via Search) ---")
    
    # Pick 2 random tags to keep it fresh daily
    targets = random.sample(CONFIG["twitter_hashtags"], 2)
    buzz_findings = []

    for tag in targets:
        # Search query: "site:x.com #GenerativeAI" (past day/week implied by 'fresh' search results)
        query = f"site:x.com {tag}"
        print(f"   --> Searching X for: {tag}...")
        
        try:
            # We fetch top 5 results
            results = DDGS().text(query, max_results=5)
            if not results: continue

            # Synthesize the chatter
            chatter_text = "\n".join([f"- {r['title']}: {r['body']}" for r in results])
            
            # Ask OpenAI to summarize the "Vibe"
            prompt = (
                f"I have scraped recent search results from X (Twitter) for the hashtag {tag}.\n"
                f"Based on these snippets, what is the current hot topic or sentiment?\n"
                f"Ignore generic spam. Focus on debates, new tools, or controversy.\n\n"
                f"SNIPPETS:\n{chatter_text}\n\n"
                f"OUTPUT:\n"
                f"Provide a 1-sentence summary of what's happening."
            )
            
            response = client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model="gpt-3.5-turbo",
            )
            summary = response.choices[0].message.content.strip()
            
            buzz_findings.append({
                "source": "X (Twitter) Trend",
                "id": f"twitter-{tag}-{datetime.now().strftime('%Y%m%d')}", # Virtual ID
                "title": f"Community Buzz: {tag}",
                "url": f"https://x.com/search?q={tag.replace('#', '%23')}",
                "summary": summary
            })
            time.sleep(2)
            
        except Exception as e:
            print(f"   --> Twitter search failed: {e}")
            
    return buzz_findings

def get_web_context(topic_title):
    print(f"   --> Agent searching web for: {topic_title[:50]}...")
    try:
        results = DDGS().text(topic_title, max_results=3)
        if not results: return "No immediate news found."
        context = "Web Findings:\n"
        for r in results: context += f"- {r['title']}: {r['body']}\n"
        return context
    except Exception: return "Web search failed."

def send_email(subject, body):
    if not CONFIG["email_sender"] or not CONFIG["email_password"]:
        print("Skipping email: Credentials not set.")
        return
    msg = MIMEMultipart()
    msg['From'] = CONFIG["email_sender"]
    msg['To'] = CONFIG["email_recipient"]
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'html'))
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.
