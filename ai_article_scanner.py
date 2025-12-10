import os
import json
import time
from datetime import datetime
import requests
import feedparser
from Bio import Entrez
from openai import OpenAI
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from dotenv import load_dotenv

# Load local .env file if present (useful for local testing)
load_dotenv()

# --- CONFIGURATION ---
CONFIG = {
    # This is the specific line required for the GitHub Action to work
    "storage_dir": os.environ.get("AI_SCANNER_STORAGE", "ai_scanner_storage"),
    "seen_file": "seen_store.json",
    "search_term": "artificial intelligence AND medicine", # <--- CHANGE THIS to your topic
    "max_results": 5
}

# --- SETUP CLIENTS ---
# Initialize OpenAI
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# Initialize Slack
slack_token = os.environ.get("SLACK_BOT_TOKEN")
slack_client = WebClient(token=slack_token)
slack_channel = os.environ.get("SLACK_CHANNEL")

# Setup PubMed/Entrez
Entrez.email = os.environ.get("PUBMED_EMAIL")
Entrez.api_key = os.environ.get("PUBMED_API_KEY")

# --- FUNCTIONS ---

def get_seen_ids():
    """Load the list of previously processed article IDs."""
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
    """Save a new ID to the storage file immediately."""
    if article_id not in seen_ids:
        seen_ids.append(article_id)
        
    path = os.path.join(CONFIG["storage_dir"], CONFIG["seen_file"])
    
    # Ensure directory exists (redundant safety for local runs)
    os.makedirs(CONFIG["storage_dir"], exist_ok=True)
    
    with open(path, 'w') as f:
        json.dump({"seen_ids": seen_ids, "last_updated": str(datetime.now())}, f, indent=2)

def fetch_pubmed_articles(query, max_results=5):
    """Search PubMed for new articles."""
    print(f"Searching PubMed for: {query}")
    try:
        # 1. Search for IDs
        handle = Entrez.esearch(db="pubmed", term=query, retmax=max_results, sort="relevance")
        record = Entrez.read(handle)
        handle.close()
        id_list = record["IdList"]
        
        if not id_list:
            return []

        # 2. Fetch details for those IDs
        handle = Entrez.efetch(db="pubmed", id=",".join(id_list), retmode="xml")
        articles = Entrez.read(handle)
        handle.close()
        
        results = []
        for article in articles['PubmedArticle']:
            try:
                # Extract basic info
                medline = article['MedlineCitation']
                pmid = str(medline['PMID'])
                title = medline['Article']['ArticleTitle']
                abstract = ""
                if 'Abstract' in medline['Article']:
                    abstract_list = medline['Article']['Abstract']['AbstractText']
                    abstract = " ".join(abstract_list)
                
                results.append({
                    "id": pmid,
                    "title": title,
                    "abstract": abstract,
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
                })
            except Exception as e:
                print(f"Error parsing article: {e}")
                continue
        return results

    except Exception as e:
        print(f"Error fetching from PubMed: {e}")
        return []

def summarize_article(title, abstract):
    """Use OpenAI to generate a short summary."""
    if not abstract:
        return "No abstract available."

    prompt = (
        f"Please provide a 2-sentence summary of this scientific article for a general audience. "
        f"Focus on the main finding.\n\n"
        f"Title: {title}\n"
        f"Abstract: {abstract}"
    )

    try:
        response = client.chat.completions.create(
            messages=[
                {"role": "system", "content": "You are a helpful research assistant."},
                {"role": "user", "content": prompt}
            ],
            model="gpt-3.5-turbo", # Or gpt-4o if preferred
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"OpenAI Error: {e}")
        return "Could not generate summary."

def post_to_slack(article, summary):
    """Send a formatted message to Slack."""
    if not slack_token or not slack_channel:
        print("Slack token or channel not set. Skipping notification.")
        return

    text = f"*New Article Found*\n<{article['url']}|{article['title']}>\n\n*Summary:* {summary}"
    
    try:
        slack_client.chat_postMessage(channel=slack_channel, text=text)
        print(f"Posted to Slack: {article['id']}")
    except SlackApiError as e:
        print(f"Slack API Error: {e.response['error']}")

# --- MAIN EXECUTION ---

def main():
    print("Starting AI Article Scanner...")
    
    # 1. Load State
    seen_ids = get_seen_ids()
    print(f"Loaded {len(seen_ids)} previously seen articles.")

    # 2. Fetch Articles
    articles = fetch_pubmed_articles(CONFIG["search_term"], CONFIG["max_results"])
    
    new_count = 0
    for article in articles:
        if article['id'] in seen_ids:
            print(f"Skipping seen article: {article['id']}")
            continue
            
        print(f"Processing new article: {article['id']}")
        
        # 3. Summarize
        summary = summarize_article(article['title'], article['abstract'])
        
        # 4. Notify
        post_to_slack(article, summary)
        
        # 5. Save State (Immediately, to prevent re-processing if script crashes later)
        save_seen_id(article['id'], seen_ids)
        new_count += 1
        
        # Polite delay
        time.sleep(1)

    print(f"Job complete. Processed {new_count} new articles.")

if __name__ == "__main__":
    main()
