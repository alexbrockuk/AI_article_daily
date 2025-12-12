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
    
    # --- HYBRID MODEL STRATEGY ---
    # "Junior Analyst": Cheap, fast, reads the raw data
    "model_cheap": "gpt-4o-mini", 
    # "Strategy Director": The new cutting-edge model for synthesis
    "model_smart": "gpt-5.2",       

    "scan_depth": 25,  
    "max_email_items": 12,

    "jmir_feed": "https://ai.jmir.org/feed/atom",
    
    # EXPERT FEEDS
    "expert_feeds": [
        {"name": "Ethan Mollick", "url": "https://www.oneusefulthing.org/feed", "filter": False},
        {"name": "Scott Galloway", "url": "https://www.profgalloway.com/feed/", "filter": True},
        {"name": "Azeem Azhar", "url": "https://exponentialview.substack.com/feed", "filter": True},
        {"name": "Cory Doctorow", "url": "https://pluralistic.net/feed/", "filter": True},
        {"name": "Jakob Nielsen", "url": "https://jakobnielsenphd.substack.com/feed", "filter": True},
        {"name": "Ed Zitron", "url": "https://www.wheresyoured.at/feed", "filter": True},
        {"name": "Maggie Appleton", "url": "https://maggieappleton.com/rss.xml", "filter": True},
        {"name": "Simon Willison", "url": "https://simonwillison.net/atom/entries/", "filter": True}
    ],

    "expert_ai_keywords": [
        "ai ", "artificial intelligence", "llm", "gpt", "generative", 
        "machine learning", "neural", "algorithm", "robot", "agent", 
        "automation", "copilot", "claude", "gemini", "transformer", 
        "compute", "model", "turing"
    ],
    
    # REDDIT CONFIG
    "reddit_tech_subs": ["ArtificialIntelligence", "MachineLearning", "ChatGPT", "OpenAI"],
    "reddit_general_subs": ["marketing", "advertising", "AgencyLife"],

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

# --- BRIEFING GENERATOR (Uses GPT-5.2) ---
def generate_daily_briefing(items):
    if not items: return "No major updates today."
    
    context_list = ""
    for item in items:
        context_list += f"- [{item['source']}] {item['title']}: {item['summary'][:250]}\n"
        
    prompt = (
        f"You are a Strategy Director. Review these {len(items)} daily insights.\n\n"
        f"CONTEXT:\n{context_list}\n\n"
        f"TASK: Synthesize this into a 3-4 bullet point executive summary.\n"
        f"RULES FOR SYNTHESIS:\n"
        f"1. DO NOT simply list every item. Group related items into themes.\n"
        f"2. IGNORE outliers or niche items unless they signal a massive shift.\n"
        f"3. Focus on the 'So What': Why does this combination of news matter to an agency?\n"
        f"4. Be opinionated and strategic.\n"
        f"5. Start immediately with the bullets."
    )
    
    try:
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=CONFIG["model_smart"], # <--- USES GPT-5.2
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Briefing generation failed: {e}")
        return "Could not generate briefing."

def fetch_expert_insights():
    print("--- Checking Expert Voices ---")
    results = []
    
    for expert in CONFIG["expert_feeds"]:
        try:
            print(f"   --> Checking {expert['name']}...")
            req = urllib.request.Request(
                expert['url'], 
                headers={'User-Agent': 'Mozilla/5.0 (compatible; AgencyScanner/1.0)'}
            )
            data = urllib.request.urlopen(req).read()
            feed = feedparser.parse(data)
            if not feed.entries: continue

            latest = feed.entries[0]
            clean_id = latest.id if 'id' in latest else latest.link
            summary_text = latest.summary[:2500] if 'summary' in latest else latest.title
            
            if expert['filter']:
                full_text = (latest.title + " " + summary_text).lower()
                found_keyword = any(k in full_text for k in CONFIG["expert_ai_keywords"])
                if not found_keyword: continue

            results.append({
                "source": f"Expert Voice: {expert['name']}",
                "id": clean_id,
                "title": latest.title,
                "url": latest.link,
                "raw_text": summary_text
            })
        except Exception as e:
            print(f"   --> Failed to fetch {expert['name']}: {e}")
            
    return results

def fetch_reddit_buzz():
    print("--- Checking Reddit Communities (Consolidated) ---")
    
    all_targets = CONFIG["reddit_tech_subs"] + CONFIG["reddit_general_subs"]
    valid_candidates = []

    for sub in all_targets:
        rss_url = f"https://www.reddit.com/r/{sub}/top/.rss?t=day"
        print(f"   --> Scanning r/{sub}...")
        try:
            req = urllib.request.Request(
                rss_url, headers={'User-Agent': 'Mozilla/5.0 (compatible; AgencyScanner/1.0)'}
            )
            data = urllib.request.urlopen(req).read()
            feed = feedparser.parse(data)
            if not feed.entries: continue
            
            found_count = 0
            for p in feed.entries[:5]:
                clean_title = p.title.replace("[D]", "").strip()
                
                if sub in CONFIG["reddit_general_subs"]:
                    full_text = clean_title.lower()
                    has_ai = any(k in full_text for k in CONFIG["expert_ai_keywords"])
                    if not has_ai: continue
                
                valid_candidates.append({
                    "sub": sub,
                    "title": clean_title,
                    "link": p.link
                })
                found_count += 1
                if found_count >= 2: break
                
        except Exception: continue

    if not valid_candidates: return []

    final_selection = valid_candidates[:6]
    
    post_context = ""
    links_html = "<br><strong>Trending Threads:</strong><ul>"
    
    for p in final_selection:
        post_context += f"- [r/{p['sub']}] {p['title']}\n"
        links_html += f"<li><strong>r/{p['sub']}:</strong> <a href='{p['link']}' style='text-decoration:none;'>{p['title']}</a></li>"
    links_html += "</ul>"

    prompt = (
        f"Review these trending discussions from across the Reddit AI & Agency ecosystem.\n"
        f"POSTS:\n{post_context}\n\n"
        f"TASK:\n"
        f"1. 'Meta-Narrative': What is the dominant mood today? (Hype, anger, technical breakthrough?)\n"
        f"2. 'Agency Implication': Strategic advice based on this sentiment."
    )
    
    try:
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=CONFIG["model_cheap"], # <--- USES CHEAP MODEL (4o-mini)
        )
        ai_analysis = response.choices[0].message.content.strip()
        
        return [{
            "source": "Community Pulse: Reddit", 
            "id": f"reddit-consolidated-{datetime.now().strftime('%Y%m%d')}",
            "title": "Cross-Community AI Sentiment",
            "url": "https://www.reddit.com/r/ArtificialIntelligence/top/?t=day",
            "summary": ai_analysis.replace('\n', '<br>') + links_html
        }]
    except Exception: return []

def get_web_context(topic_title):
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
    except Exception: return []

def summarize_expert_post(title, raw_text):
    prompt = (
        f"Summarize this essay from a thought leader (like Ed Zitron or Ethan Mollick) for an agency strategist.\n"
        f"TITLE: {title}\nTEXT SNIPPET: {raw_text}\n\n"
        f"TASK:\n1. What is their core thesis? (1-2 sentences)\n"
        f"2. 'Agency Takeaway': How should we adapt our thinking based on this?"
    )
    try:
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=CONFIG["model_cheap"], # <--- USES CHEAP MODEL (4o-mini)
        )
        return response.choices[0].message.content.strip()
    except Exception: return "Summary failed."

def summarize_article(title, abstract, web_context):
    if not abstract: return "No abstract available."
    prompt = (
        f"Analyze this research paper for a healthcare comms agency.\n"
        f"WEB CONTEXT: {web_context}\n\n"
        f"PAPER DATA:\nTitle: {title}\nAbstract: {abstract}\n\n"
        f"TASK:\n1. Summarize finding (2 bullets).\n"
        f"2. 'Buzz Check': Is this niche or trending?\n"
        f"3. 'Agency Implication': Why we should care."
    )
    try:
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=CONFIG["model_cheap"], # <--- USES CHEAP MODEL (4o-mini)
        )
        return response.choices[0].message.content.strip()
    except Exception: return "Summary failed."

# --- MAIN ---

def main():
    print(f"Starting Scan (Hybrid Models: {CONFIG['model_cheap']} / {CONFIG['model_smart']})...")
    seen_ids = get_seen_ids()
    
    # 1. Gather all content
    all_content = fetch_expert_insights() + fetch_reddit_buzz() + fetch_jmir_articles() + fetch_arxiv_articles()
    
    new_finds = []
    for item in all_content:
        if item['id'] in seen_ids: continue
        if len(new_finds) >= CONFIG["max_email_items"]: break

        print(f"Processing: {item['title']}")
        
        if "summary" not in item:
            if "Expert Voice" in item["source"]:
                item["summary"] = summarize_expert_post(item['title'], item['raw_text'])
            else:
                web_ctx = get_web_context(item['title'])
                item["summary"] = summarize_article(item['title'], item['abstract'], web_ctx)
        
        new_finds.append(item)
        save_seen_id(item['id'], seen_ids)
        time.sleep(2)

    if new_finds:
        print(f"Found {len(new_finds)} items. Generating briefing...")
        
        daily_briefing = generate_daily_briefing(new_finds)
        
        # Build Email
        clean_briefing = daily_briefing.replace('\n', '<br>')
        
        email_body = f"""
        <div style="background-color:#f0f4f8; padding:20px; border-radius:8px; border-left: 5px solid #2c3e50; margin-bottom:25px; font-family: sans-serif;">
            <h3 style="margin-top:0; color:#2c3e50;">☕ Morning Briefing</h3>
            <div style="font-size:15px; line-height:1.6; color:#333;">{clean_briefing}</div>
        </div>
        """
        
        for item in new_finds:
            clean_summary = item['summary'].replace('\n', '<br>')
            if "Expert" in item['source']: color = "#800080"
            elif "Community Pulse" in item['source']: color = "#FF4500"
            else: color = "gray"
            
            email_body += f"""
            <hr style="border:0; border-top:1px solid #eee; margin: 20px 0;">
            <p style="color:{color}; font-weight:bold; font-size:11px; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:5px;">{item['source']}</p>
            <h3 style="margin-top:0; margin-bottom:10px;"><a href="{item['url']}" style="color:#0066cc; text-decoration:none;">{item['title']}</a></h3>
            <div style="font-size:14px; line-height:1.5; color:#444;">{clean_summary}</div>
            """
        
        send_email(f"AI Strategy Daily: {len(new_finds)} Updates", email_body)
    else:
        print("No new relevant insights today.")

if __name__ == "__main__":
    main()
