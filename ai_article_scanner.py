import os
import json
import time
import smtplib
import urllib.request
import feedparser
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from openai import OpenAI
from duckduckgo_search import DDGS  # <--- NEW LIBRARY
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURATION ---
CONFIG = {
    "storage_dir": os.environ.get("AI_SCANNER_STORAGE", "ai_scanner_storage"),
    "seen_file": "seen_store.json",
    
    "scan_depth": 25,  
    "max_email_items": 5, # Reduced slightly since agentic work takes longer

    "jmir_feed": "https://ai.jmir.org/feed/atom",
    
    "arxiv_query": (
        "(cat:cs.HC OR cat:cs.CY OR cat:cs.SI) AND "
        "(all:advertising OR all:marketing OR all:brand OR "
        "all:consumer OR all:behavioral OR all:psychology OR "
        "all:persuasion OR all:misinformation OR all:social_media OR "
        "all:narrative OR all:creative OR all:adoption)"
    ),

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

# --- NEW AGENTIC FUNCTION ---
def get_web_context(topic_title):
    """
    Searches the web to see if this topic is trending or being discussed.
    """
    print(f"   --> Agent searching web for: {topic_title[:50]}...")
    try:
        results = DDGS().text(topic_title, max_results=3)
        if not results:
            return "No immediate news or public discussion found."
        
        # Compile search snippets into a context string
        context = "Web Search Findings:\n"
        for r in results:
            context += f"- {r['title']}: {r['body']}\n"
        return context
    except Exception as e:
        print(f"   --> Search failed: {e}")
        return "Could not perform web search."

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
        server.login(CONFIG["email_sender"], CONFIG["email_password"])
        server.sendmail(CONFIG["email_sender"], CONFIG["email_recipient"], msg.as_string())
        server.quit()
        print("Email sent successfully!")
    except Exception as e:
        print(f"Email failed: {e}")

def fetch_jmir_articles():
    print("--- Checking JMIR ---")
    try:
        feed = feedparser.parse(CONFIG["jmir_feed"])
        results = []
        for entry in feed.entries[:CONFIG["scan_depth"]]:
            clean_id = entry.id.strip("/").split("/")[-1]
            if is_relevant(entry.title, entry.summary):
                results.append({
                    "source": "JMIR AI",
                    "id": clean_id,
                    "title": entry.title,
                    "abstract": entry.summary,
                    "url": entry.link
                })
        return results
    except Exception: return []

def fetch_arxiv_articles():
    print("--- Checking arXiv ---")
    query = CONFIG["arxiv_query"].replace(" ", "+").replace("(", "%28").replace(")", "%29")
    base_url = 'http://export.arxiv.org/api/query?'
    search_query = f"search_query={query}&start=0&max_results={CONFIG['scan_depth']}&sortBy=submittedDate&sortOrder=descending"
    try:
        response = urllib.request.urlopen(base_url + search_query).read()
        feed = feedparser.parse(response)
        results = []
        for entry in feed.entries:
            clean_id = entry.id.split('/abs/')[-1].split('v')[0]
            if is_relevant(entry.title, entry.summary):
                results.append({
                    "source": "arXiv",
                    "id": clean_id,
                    "title": entry.title.replace('\n', ' '),
                    "abstract": entry.summary.replace('\n', ' '),
                    "url": entry.link
                })
        return results
    except Exception as e:
        print(f"ArXiv Error: {e}")
        return []

def summarize_article(title, abstract, web_context):
    if not abstract: return "No abstract available."
    
    # --- PROMPT UPGRADED FOR CONTEXT ---
    prompt = (
        f"Analyze this research paper for a healthcare communications agency.\n"
        f"I have also performed a web search to see if this topic is trending.\n\n"
        f"PAPER DATA:\nTitle: {title}\nAbstract: {abstract}\n\n"
        f"WEB CONTEXT:\n{web_context}\n\n"
        f"YOUR TASK:\n"
        f"1. Summarize the main finding (2 bullets).\n"
        f"2. 'Buzz Check': Based on the web context, is this a brand new niche idea or part of an existing hot trend?\n"
        f"3. 'Agency Implication': Single sentence on why we should care."
    )
    
    try:
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="gpt-3.5-turbo",
        )
        return response.choices[0].message.content.strip()
    except Exception: return "Summary failed."

# --- MAIN ---

def main():
    print("Starting Agentic Scan...")
    seen_ids = get_seen_ids()
    candidates = fetch_jmir_articles() + fetch_arxiv_articles()
    
    new_finds = []
    for article in candidates:
        if article['id'] in seen_ids:
            continue
        
        if len(new_finds) >= CONFIG["max_email_items"]:
            break

        print(f"Processing: {article['title']}")
        
        # --- AGENTIC STEP ---
        # We search the web for the title to see who else is talking about it
        web_context = get_web_context(article['title'])
        
        summary = summarize_article(article['title'], article['abstract'], web_context)
        
        new_finds.append({
            "title": article['title'],
            "url": article['url'],
            "source": article['source'],
            "summary": summary
        })
        
        save_seen_id(article['id'], seen_ids)
        time.sleep(2) # Sleep longer to be nice to search engines

    if new_finds:
        print(f"Found {len(new_finds)} relevant articles.")
        email_body = f"<h2>Daily Agency/AI Insight Scan ({len(new_finds)})</h2>"
        for item in new_finds:
            clean_summary = item['summary'].replace('\n', '<br>')
            email_body += f"""
            <hr>
            <p style="color:gray; font-size:12px;">{item['source']}</p>
            <h3><a href="{item['url']}">{item['title']}</a></h3>
            <p>{clean_summary}</p>
            """
        send_email(f"AI Insights: {len(new_finds)} New Papers", email_body)
    else:
        print("No articles matched your relevance filters today.")

if __name__ == "__main__":
    main()
