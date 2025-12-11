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
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURATION ---
CONFIG = {
    "storage_dir": os.environ.get("AI_SCANNER_STORAGE", "ai_scanner_storage"),
    "seen_file": "seen_store.json",
    
    "scan_depth": 25,  # Scan more deep to find the right ones
    "max_email_items": 7, 

    "jmir_feed": "https://ai.jmir.org/feed/atom",
    
    # NEW STRATEGY: 
    # 1. Remove cs.LG (Machine Learning) to stop the math papers.
    # 2. Focus heavily on HC (Human Computer Interaction) and CY (Computers and Society).
    # 3. The query now demands a "Human Element" (user, consumer, social, behavior).
    "arxiv_query": (
        "(cat:cs.HC OR cat:cs.CY OR cat:cs.SI) AND "
        "(all:advertising OR all:marketing OR all:brand OR "
        "all:consumer OR all:behavioral OR all:psychology OR "
        "all:persuasion OR all:misinformation OR all:social_media OR "
        "all:narrative OR all:creative OR all:adoption)"
    ),

    # --- FILTERING LOGIC ---
    # We keep the positive keywords to double-check relevance
    "positive_keywords": [
        "communication", "agency", "marketing", "advertising", "brand", 
        "behavior", "psycholog", "persua", "nudge", "decision", 
        "user experience", "ux", "interface", "design", "chatbot", 
        "conversational", "narrative", "social media", "adoption",
        "consumer", "trust", "ethics", "generative"
    ],
    
    # We keep the negative keywords to block the heavy medical stuff from JMIR
    "negative_keywords": [
        "radiology", "tumor", "cancer", "surgery", "surgical", 
        "prognosis", "diagnosis", "diagnostic", "clinical trial", 
        "genomic", "protein", "molecular", "scan", "mri", "ct image",
        "reinforcement learning", "neural network architecture", 
        "gradient descent", "logit", "quantization", "bit-flip"
    ],
    
    # Email Settings
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
    """
    Decides if an article is relevant for an Agency/Comms context.
    Returns: True (Keep) or False (Discard)
    """
    text = (title + " " + abstract).lower()
    
    # 1. Hard Block: If it mentions heavy medical tech, drop it immediately.
    for word in CONFIG["negative_keywords"]:
        if word in text:
            return False

    # 2. Soft Keep: For JMIR, we need to ensure it hits a 'positive' topic 
    # since the source is medical. For arXiv, the query handles most of this, 
    # but this double-checks.
    for word in CONFIG["positive_keywords"]:
        if word in text:
            return True
            
    # If it's from arXiv, our query already did the heavy lifting, so we might contain it.
    # But for JMIR, if it lacks a positive keyword, we assume it's too clinical.
    return False 

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
    print("--- Checking JMIR (Filtering for Comms/UX) ---")
    try:
        feed = feedparser.parse(CONFIG["jmir_feed"])
        results = []
        # We look at deeper history (scan_depth) to find the gems hidden among the clinical stuff
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
    print("--- Checking arXiv (Broad Lateral Search) ---")
    query = CONFIG["arxiv_query"].replace(" ", "+").replace("(", "%28").replace(")", "%29")
    base_url = 'http://export.arxiv.org/api/query?'
    # We fetch a few more than needed to allow for post-filtering
    search_query = f"search_query={query}&start=0&max_results={CONFIG['scan_depth']}&sortBy=submittedDate&sortOrder=descending"
    try:
        response = urllib.request.urlopen(base_url + search_query).read()
        feed = feedparser.parse(response)
        results = []
        for entry in feed.entries:
            clean_id = entry.id.split('/abs/')[-1].split('v')[0]
            # Even though arXiv query is specific, we run the filter again to remove "medical imaging" noise
            if is_relevant(entry.title, entry.summary):
                results.append({
                    "source": "arXiv",
                    "id": clean_id,
                    "title": entry.title.replace('\n', ' '),
                    "abstract": entry.summary.replace('\n', ' '),
                    "url": entry.link
                })
        return results
    except Exception: return []

def summarize_article(title, abstract):
    if not abstract: return "No abstract available."
    # Adjusted prompt for "Connecting Dots"
    prompt = (
        f"Summarize this research paper in 2 bullet points. "
        f"Focus on the implications for human behavior, decision making, or user experience. "
        f"Ignore technical medical implementation details.\n\n"
        f"Title: {title}\nAbstract: {abstract}"
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
    print("Starting Smart Scan...")
    seen_ids = get_seen_ids()
    
    # Gather candidates
    candidates = fetch_jmir_articles() + fetch_arxiv_articles()
    
    # Process only new ones
    new_finds = []
    for article in candidates:
        if article['id'] in seen_ids:
            continue
        
        # Limit the daily email size so you don't get overwhelmed
        if len(new_finds) >= CONFIG["max_email_items"]:
            break

        print(f"Processing: {article['title']}")
        summary = summarize_article(article['title'], article['abstract'])
        
        new_finds.append({
            "title": article['title'],
            "url": article['url'],
            "source": article['source'],
            "summary": summary
        })
        
        save_seen_id(article['id'], seen_ids)
        time.sleep(1)

    if new_finds:
        print(f"Found {len(new_finds)} relevant articles.")
        email_body = f"<h2>Daily Agency/AI Insight Scan ({len(new_finds)})</h2>"
        for item in new_finds:
            clean_summary = item['summary'].replace('\n', '<br>')
            email_body += f"""
            <hr>
            <p><strong>{item['source']}</strong></p>
            <h3><a href="{item['url']}">{item['title']}</a></h3>
            <p><b>Insight:</b><br>{clean_summary}</p>
            """
        send_email(f"AI Insights: {len(new_finds)} New Papers", email_body)
    else:
        print("No articles matched your relevance filters today.")

if __name__ == "__main__":
    main()
