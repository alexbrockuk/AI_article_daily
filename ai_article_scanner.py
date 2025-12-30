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
    
    # Hybrid Model Strategy
    "model_cheap": "gpt-4o-mini", # Volume processing
    "model_smart": "gpt-5.2",     # Strategic synthesis

    "scan_depth": 25,  
    "max_email_items": 12,

    "jmir_feed": "https://ai.jmir.org/feed/atom",
    
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

# --- HELPER: CLEANER ---
def clean_llm_output(text):
    if not text: return ""
    text = text.replace("**", "").replace("### ", "").replace("###", "")
    keywords = ["Agency Implication:", "Buzz Check:", "Themes:", "The Debate:", "The Concept:"]
    for k in keywords:
        text = text.replace(k, f"<b>{k}</b>")
    return text.replace("\n", "<br>")

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

# --- BRIEFING GENERATOR ---
def generate_daily_briefing(items):
    if not items: return "No major updates today."
    
    context_list = ""
    for item in items:
        context_list += f"- [{item['source']}] {item['title']}: {item['summary'][:300]}\n"
        
    prompt = (
        f"You are a Strategy Director. Review these {len(items)} insights.\n\n"
        f"CONTEXT:\n{context_list}\n\n"
        f"TASK: Write an Executive Briefing in HTML.\n"
        f"RULES:\n"
        f"1. Create 3-4 distinct bullet points.\n"
        f"2. STYLE: Don't be telegraphic. Be explanatory but concise (2-3 sentences per point).\n"
        f"3. Explain the 'Why': Connect the news to broader agency strategy or client risks.\n"
        f"4. Grouping: If two items are about the same topic, combine them into one strong point.\n"
        f"5. NO Markdown symbols (**). Use <b> tags for emphasis."
    )
    
    try:
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=CONFIG["model_smart"], 
        )
        return response.choices[0].message.content.strip().replace("**", "")
    except Exception: return "Could not generate briefing."

# --- REDDIT DEEP DIVE FUNCTIONS ---

def fetch_reddit_discussion(url):
    """
    Fetches the JSON version of a Reddit thread to get the actual comments.
    Includes logic to strip query parameters to prevent 404s.
    """
    try:
        # 1. Clean URL: Remove '?source=rss' and other params
        clean_url = url.split('?')[0]
        # 2. Append .json
        json_url = f"{clean_url.rstrip('/')}.json"
        
        req = urllib.request.Request(
            json_url, 
            # Use a very generic browser User-Agent to avoid blocking
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        )
        response = urllib.request.urlopen(req).read()
        data = json.loads(response)
        
        # Reddit JSON structure: [0] is post, [1] is comments
        post_data = data[0]['data']['children'][0]['data']
        comments_data = data[1]['data']['children']
        
        # Get Post Body
        body = post_data.get('selftext', '')[:800] 
        
        # Get Top 5 Comments
        comments_text = ""
        for i, c in enumerate(comments_data[:5]):
            if 'data' in c and 'body' in c['data']:
                comments_text += f"Comment {i+1}: {c['data']['body'][:400]}\n"
        
        full_transcript = f"OP POST: {body}\n\nTOP COMMENTS:\n{comments_text}"
        return full_transcript
        
    except Exception as e:
        print(f"   --> Failed to fetch Reddit JSON: {e}")
        return None # Return None to trigger fallback

def fetch_reddit_buzz():
    print("--- Checking Reddit Communities ---")
    all_targets = CONFIG["reddit_tech_subs"] + CONFIG["reddit_general_subs"]
    valid_candidates = []

    for sub in all_targets:
        try:
            req = urllib.request.Request(
                f"https://www.reddit.com/r/{sub}/top/.rss?t=day", 
                headers={'User-Agent': 'Mozilla/5.0 (compatible; AgencyScanner/1.0)'}
            )
            data = urllib.request.urlopen(req).read()
            feed = feedparser.parse(data)
            if not feed.entries: continue
            
            found_count = 0
            for p in feed.entries[:5]: 
                clean_title = p.title.replace("[D]", "").strip()
                
                # Relevance Filter
                if sub in CONFIG["reddit_general_subs"]:
                    if not any(k in clean_title.lower() for k in CONFIG["expert_ai_keywords"]): continue
                
                valid_candidates.append({
                    "source": f"r/{sub}",
                    "id": p.id,
                    "title": clean_title,
                    "url": p.link,
                    "raw_text": "" # Will fill in main loop
                })
                found_count += 1
                if found_count >= 2: break 
        except Exception: continue

    return valid_candidates[:3]

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
        except Exception: continue
    return results

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
    try:
        feed = feedparser.parse(CONFIG["jmir_feed"])
        results = []
        for entry in feed.entries[:CONFIG["scan_depth"]]:
            if is_relevant(entry.title, entry.summary):
                results.append({
                    "source": "JMIR AI",
                    "id": entry.id.strip("/").split("/")[-1],
                    "title": entry.title,
                    "abstract": entry.summary,
                    "url": entry.link
                })
        return results
    except Exception: return []

def fetch_arxiv_articles():
    query = CONFIG["arxiv_query"].replace(" ", "+").replace("(", "%28").replace(")", "%29")
    try:
        response = urllib.request.urlopen(f'http://export.arxiv.org/api/query?search_query={query}&start=0&max_results={CONFIG["scan_depth"]}&sortBy=submittedDate&sortOrder=descending').read()
        feed = feedparser.parse(response)
        results = []
        for entry in feed.entries:
            if is_relevant(entry.title, entry.summary):
                results.append({
                    "source": "arXiv",
                    "id": entry.id.split('/abs/')[-1].split('v')[0],
                    "title": entry.title.replace('\n', ' '),
                    "abstract": entry.summary.replace('\n', ' '),
                    "url": entry.link
                })
        return results
    except Exception: return []

# --- SUMMARIZERS ---

def summarize_expert_post(title, raw_text):
    prompt = (
        f"Summarize this essay for an agency strategist.\nTITLE: {title}\nTEXT: {raw_text}\n\n"
        f"TASK: 1. Core Thesis (1 sentence). 2. Agency Takeaway (1 sentence).\n"
        f"FORMAT: Plain text. No markdown (**). Use <b> for headers."
    )
    try:
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=CONFIG["model_cheap"],
        )
        return clean_llm_output(response.choices[0].message.content.strip())
    except Exception: return "Summary failed."

def summarize_reddit_post(title, context):
    """
    Analyzes Reddit context. Handles cases where context is missing via fallback.
    """
    # Fallback Logic: If scraping failed, context will be short/generic
    if not context or "Web Findings" in context:
        prompt_intro = f"We could not read the specific comments, but here is the topic context:\n{context}\n"
        task_instruction = "Based on this general topic, explain the likely controversy or strategic importance."
    else:
        prompt_intro = f"DISCUSSION TRANSCRIPT:\n{context}\n"
        task_instruction = "Based on these real comments, summarize the specific debate."

    prompt = (
        f"Analyze this Reddit discussion.\n"
        f"TITLE: {title}\n{prompt_intro}\n"
        f"TASK:\n"
        f"1. 'The Debate': {task_instruction}\n"
        f"2. 'Agency Implication': Why should a creative/strategy agency care?\n"
        f"FORMAT: Plain text. No markdown (**). Use <b> for headers."
    )
    try:
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=CONFIG["model_cheap"],
        )
        return clean_llm_output(response.choices[0].message.content.strip())
    except Exception: return "Summary failed."

def summarize_article(title, abstract, web_context):
    prompt = (
        f"Explain this research paper to a NON-TECHNICAL Strategy Director.\n"
        f"PAPER: {title}\nABSTRACT: {abstract}\n\n"
        f"RULES:\n"
        f"1. NO JARGON. Do not use words like 'weights', 'loss function', or 'transformer' without defining them simply.\n"
        f"2. Conceptualize: What is the *capability* or *risk* being described?\n"
        f"TASK:\n"
        f"1. 'The Concept': Simple English explanation of what they did.\n"
        f"2. 'Why it matters': The practical upshot for creative/business strategy.\n"
        f"FORMAT: Plain text. No markdown (**). Use <b> for headers."
    )
    try:
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=CONFIG["model_cheap"],
        )
        return clean_llm_output(response.choices[0].message.content.strip())
    except Exception: return "Summary failed."

# --- MAIN ---

def main():
    print(f"Starting Scan...")
    seen_ids = get_seen_ids()
    
    # 1. Gather Content
    all_content = fetch_expert_insights() + fetch_reddit_buzz() + fetch_jmir_articles() + fetch_arxiv_articles()
    
    new_finds = []
    for item in all_content:
        if item['id'] in seen_ids: continue
        if len(new_finds) >= CONFIG["max_email_items"]: break

        print(f"Processing: {item['title']}")
        
        # 2. Deep Fetch for Reddit (Get the real comments)
        if "r/" in item["source"]:
             # Try to get real comments
             discussion_text = fetch_reddit_discussion(item['url'])
             
             # If Reddit blocked us (discussion_text is None), FALLBACK to Web Search
             if discussion_text is None:
                 print(f"   --> Reddit blocked JSON. Falling back to Web Search...")
                 web_ctx = get_web_context(item['title'] + " reddit discussion")
                 item['raw_text'] = f"Reddit scraping failed. Web Search Context:\n{web_ctx}"
             else:
                 item['raw_text'] = discussion_text

        # 3. Generate Summaries based on Type
        if "summary" not in item:
            if "Expert Voice" in item["source"]:
                item["summary"] = summarize_expert_post(item['title'], item['raw_text'])
            elif "r/" in item["source"]:
                item["summary"] = summarize_reddit_post(item['title'], item['raw_text'])
            else:
                web_ctx = get_web_context(item['title'])
                item["summary"] = summarize_article(item['title'], item['abstract'], web_ctx)
        
        new_finds.append(item)
        save_seen_id(item['id'], seen_ids)
        time.sleep(2)

    if new_finds:
        print(f"Found {len(new_finds)} items. Generating briefing...")
        
        raw_briefing = generate_daily_briefing(new_finds)
        clean_briefing = raw_briefing.replace("**", "").replace("###", "")
        
        email_body = f"""
        <div style="background-color:#f0f4f8; padding:20px; border-radius:8px; border-left: 5px solid #2c3e50; margin-bottom:25px; font-family: sans-serif;">
            <h3 style="margin-top:0; color:#2c3e50;">☕ Morning Briefing</h3>
            <div style="font-size:15px; line-height:1.6; color:#333;">{clean_briefing}</div>
        </div>
        """
        
        for item in new_finds:
            if "Expert" in item['source']: color = "#800080"
            elif "r/" in item['source']: color = "#FF4500"
            else: color = "gray"
            
            email_body += f"""
            <hr style="border:0; border-top:1px solid #eee; margin: 20px 0;">
            <p style="color:{color}; font-weight:bold; font-size:11px; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:5px;">{item['source']}</p>
            <h3 style="margin-top:0; margin-bottom:10px;"><a href="{item['url']}" style="color:#0066cc; text-decoration:none;">{item['title']}</a></h3>
            <div style="font-size:14px; line-height:1.5; color:#444;">{item['summary']}</div>
            """
        
        send_email(f"AI Strategy Daily: {len(new_finds)} Updates", email_body)
    else:
        print("No new relevant insights today.")

if __name__ == "__main__":
    main()
