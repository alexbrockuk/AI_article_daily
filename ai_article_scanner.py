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
    Robust search: Looks for Reddit threads and News instead of X.com
    (which blocks robots).
    """
    print("--- Checking Community Buzz ---")
    
    targets = random.sample(CONFIG["twitter_hashtags"], 2)
    buzz_findings = []

    for tag in targets:
        # Strip the hash for the search query (Search engines prefer "GenerativeAI" over "#GenerativeAI")
        clean_tag = tag.replace("#", "")
        
        # SEARCH STRATEGY: Look for Reddit threads or News
        # This is almost guaranteed to find results
        query = f"{clean_tag} reddit news discussion"
        
        print(f"   --> Searching buzz for: {query}...")
        
        try:
            # We use 'text' search which is broader and safer
            results = DDGS().text(query, max_results=4)
            
            if not results: 
                print(f"   --> No results found for {tag}")
                continue

            # Synthesize the chatter
            chatter_text = "\n".join([f"- {r['title']}: {r['body']}" for r in results])
            
            prompt = (
                f"I performed a web search for community discussions on the topic '{clean_tag}'.\n"
                f"Identify the current sentiment or 'hot take' from these snippets.\n"
                f"Ignore generic marketing. Look for debates, fears, or new tools.\n\n"
                f"SNIPPETS:\n{chatter_text}\n\n"
                f"OUTPUT:\n"
                f"1-sentence summary of the current vibe."
            )
            
            response = client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model="gpt-3.5-turbo",
            )
            summary = response.choices[0].message.content.strip()
            
            buzz_findings.append({
                "source": "Community Buzz", 
                "id": f"buzz-{clean_tag}-{datetime.now().strftime('%Y%m%d')}",
                "title": f"Hot Topic: {tag}",
                "url": f"https://duckduckgo.com/?q={clean_tag}+reddit",
                "summary": summary
            })
            time.sleep(2)
            
        except Exception as e:
            print(f"   --> Buzz search failed: {e}")
            
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
            model="gpt-3.5-turbo",
        )
        return response.choices[0].message.content.strip()
    except Exception: return "Summary failed."

# --- MAIN ---

def main():
    print("Starting Multi-Source Agent Scan...")
    seen_ids = get_seen_ids()
    
    # 1. Fetch Papers
    candidates = fetch_jmir_articles() + fetch_arxiv_articles()
    
    # 2. Fetch Twitter Buzz (NEW SOURCE)
    twitter_buzz = fetch_twitter_buzz()
    
    # Combine (Twitter stuff goes first in the email as 'News')
    all_content = twitter_buzz + candidates
    
    new_finds = []
    for item in all_content:
        # Check duplicates
        if item['id'] in seen_ids: continue
        if len(new_finds) >= CONFIG["max_email_items"]: break

        print(f"Processing: {item['title']}")
        
        # If it's a paper, do the deep dive. If it's Twitter, we already summarized it.
        if "summary" not in item:
            web_ctx = get_web_context(item['title'])
            item["summary"] = summarize_article(item['title'], item['abstract'], web_ctx)
        
        new_finds.append(item)
        save_seen_id(item['id'], seen_ids)
        time.sleep(2)

    if new_finds:
        print(f"Found {len(new_finds)} items.")
        email_body = f"<h2>Daily Agency/AI Insight Scan ({len(new_finds)})</h2>"
        for item in new_finds:
            clean_summary = item['summary'].replace('\n', '<br>')
            # Color code sources: Blue for Twitter, Gray for Papers
            color = "#1DA1F2" if "Twitter" in item['source'] else "gray"
            
            email_body += f"""
            <hr>
            <p style="color:{color}; font-weight:bold; font-size:12px;">{item['source']}</p>
            <h3><a href="{item['url']}">{item['title']}</a></h3>
            <p>{clean_summary}</p>
            """
        send_email(f"AI Insights: {len(new_finds)} New Updates", email_body)
    else:
        print("No new relevant insights today.")

if __name__ == "__main__":
    main()
