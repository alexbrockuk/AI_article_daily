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
    "max_results": 5, 
    "jmir_feed": "https://ai.jmir.org/feed/atom",
    "arxiv_query": (
        "(cat:cs.AI OR cat:cs.CL OR cat:cs.LG) AND "
        "(all:advertising OR all:marketing OR all:healthcare OR all:clinical)"
    ),
    # Email Settings
    "email_sender": os.environ.get("EMAIL_ADDRESS"),
    "email_password": os.environ.get("EMAIL_PASSWORD"),
    "email_recipient": os.environ.get("EMAIL_ADDRESS") # Sending to yourself
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

def send_email(subject, body):
    if not CONFIG["email_sender"] or not CONFIG["email_password"]:
        print("Skipping email: Credentials not set.")
        return

    msg = MIMEMultipart()
    msg['From'] = CONFIG["email_sender"]
    msg['To'] = CONFIG["email_recipient"]
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'html')) # Sending as HTML for nice formatting

    try:
        # Connect to Gmail Server
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(CONFIG["email_sender"], CONFIG["email_password"])
        text = msg.as_string()
        server.sendmail(CONFIG["email_sender"], CONFIG["email_recipient"], text)
        server.quit()
        print("Email sent successfully!")
    except Exception as e:
        print(f"Email failed: {e}")

def fetch_jmir_articles():
    try:
        feed = feedparser.parse(CONFIG["jmir_feed"])
        results = []
        for entry in feed.entries[:CONFIG["max_results"]]:
            clean_id = entry.id.strip("/").split("/")[-1]
            results.append({
                "source": "JMIR",
                "id": clean_id,
                "title": entry.title,
                "abstract": entry.summary,
                "url": entry.link
            })
        return results
    except Exception:
        return []

def fetch_arxiv_articles():
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
    except Exception:
        return []

def summarize_article(title, abstract):
    if not abstract: return "No abstract available."
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
    print("Starting Scan...")
    seen_ids = get_seen_ids()
    all_articles = fetch_jmir_articles() + fetch_arxiv_articles()
    
    new_articles_found = []

    for article in all_articles:
        if article['id'] in seen_ids:
            continue
            
        print(f"Processing: {article['title']}")
        summary = summarize_article(article['title'], article['abstract'])
        
        # Add to our email list
        new_articles_found.append({
            "title": article['title'],
            "url": article['url'],
            "source": article['source'],
            "summary": summary
        })
        
        save_seen_id(article['id'], seen_ids)
        time.sleep(1)

    # If we found anything, email it!
    if new_articles_found:
        print(f"Found {len(new_articles_found)} new articles. Sending email...")
        
        # Build HTML Email Body
        email_body = f"<h2>Daily AI Research Scan ({len(new_articles_found)})</h2>"
        for item in new_articles_found:
            # Convert markdown bullets to HTML bullets if needed, or just standard text
            clean_summary = item['summary'].replace('\n', '<br>')
            email_body += f"""
            <hr>
            <h3><a href="{item['url']}">{item['title']}</a></h3>
            <p><b>Source:</b> {item['source']}</p>
            <p><b>Takeaway:</b><br>{clean_summary}</p>
            """
            
        send_email(f"AI Research Daily: {len(new_articles_found)} New Papers", email_body)
    else:
        print("No new articles today. No email sent.")

if __name__ == "__main__":
    main()
