import os
import json
import time
import urllib.request
import feedparser
from datetime import datetime
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURATION ---
CONFIG = {
    "storage_dir": os.environ.get("AI_SCANNER_STORAGE", "ai_scanner_storage"),
    "seen_file": "seen_store.json",
    "max_results": 5, # Max papers PER source
    
    # 1. JMIR Source (Specific to AI in Healthcare)
    "jmir_feed": "https://ai.jmir.org/feed/atom",
    
    # 2. arXiv Source (Targeting Ads, Agencies, & Health AI)
    # Looking for CS papers that ALSO mention specific keywords
    "arxiv_query": (
        "(cat:cs.AI OR cat:cs.CL OR cat:cs.LG) AND "
        "(all:advertising OR all:marketing OR all:healthcare OR all:clinical)"
    )
}

# --- SETUP ---
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# --- FUNCTIONS ---

def get_seen_ids():
    path = os.path.join(CONFIG["storage_dir"], CONFIG["seen_file"])
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r') as f:
            data = json.load(f)
            return data.get("seen_ids", [])
    except json.JSONDecodeError:
        return []

def save_seen_id(article_id, seen_ids):
    if article_id not in seen_ids:
        seen_ids.append(article_id)
        
    path = os.path.join(CONFIG["storage_dir"], CONFIG["seen_file"])
    os.makedirs(CONFIG["storage_dir"], exist_ok=True)
    
    with open(path, 'w') as f:
        json.dump({"seen_ids": seen_ids, "last_updated": str(datetime.now())}, f, indent=2)

def fetch_jmir_articles():
    """Scrapes the JMIR AI RSS Feed"""
    print("--- Checking JMIR AI ---")
    try:
        feed = feedparser.parse(CONFIG["jmir_feed"])
        results = []
        for entry in feed.entries[:CONFIG["max_results"]]:
            # JMIR IDs are usually URLs, we strip to the number for cleaner tracking
            # ID format example: https://ai.jmir.org/2023/1/e44556
            clean_id = entry.id.strip("/").split("/")[-1]
            
            results.append({
                "source": "JMIR",
                "id": clean_id,
                "title": entry.title,
                "abstract": entry.summary, # JMIR summaries in RSS are often short
                "url": entry.link
            })
        return results
    except Exception as e:
        print(f"Error fetching JMIR: {e}")
        return []

def fetch_arxiv_articles():
    """Queries arXiv for Ads/Health AI intersections"""
    print("--- Checking arXiv ---")
    query = CONFIG["arxiv_query"].replace(" ", "+").replace("(", "%28").replace(")", "%29")
    base_url = 'http://export.arxiv.org/api/query?'
    search_query = f"search_query={query}&start=0&max_results={CONFIG['max_results']}&sortBy=submittedDate&sortOrder=descending"
    
    try:
        response = urllib.request.urlopen(base_url + search_query).read()
        feed = feedparser.parse(response)
        
        results = []
        for entry in feed.entries:
            clean_id = entry.id.split('/abs/')[-1].split('v')[0]
            results.append({
                "source": "arXiv",
                "id": clean_id,
                "title": entry.title.replace('\n', ' '),
                "abstract": entry.summary.replace('\n', ' '),
                "url": entry.link
            })
        return results
    except Exception as e:
        print(f"Error fetching arXiv: {e}")
        return []

def summarize_article(title, abstract):
    if not abstract:
        return "No abstract available."
    
    # We tweak the prompt to focus on Business/Application
    prompt = (
        f"Summarize this research paper in 2 bullet points. "
        f"Focus specifically on the practical application for business or healthcare.\n\n"
        f"Title: {title}\nAbstract: {abstract}"
    )

    try:
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="gpt-3.5-turbo",
        )
        return response.choices[0].message.content.strip()
    except Exception:
        return "Summary failed."

# --- MAIN ---

def main():
    print("Starting Multi-Source AI Scan...")
    seen_ids = get_seen_ids()
    
    # Gather from both sources
    all_articles = fetch_jmir_articles() + fetch_arxiv_articles()
    
    new_count = 0
    for article in all_articles:
        if article['id'] in seen_ids:
            continue
            
        print(f"\n[{article['source']}]: {article['title']}")
        print(f"URL: {article['url']}")
        
        summary = summarize_article(article['title'], article['abstract'])
        print(f"TAKEAWAY:\n{summary}")
        print("-" * 40)
        
        save_seen_id(article['id'], seen_ids)
        new_count += 1
        time.sleep(1) 

    if new_count == 0:
        print("No new relevant papers found today.")
    else:
        print(f"\nJob complete. {new_count} new papers processed.")

if __name__ == "__main__":
    main()
