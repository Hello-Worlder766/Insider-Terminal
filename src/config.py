import os
from dotenv import load_dotenv

# --- Step 1: Load Environment Variables ---
dotenv_path = os.path.join(os.getcwd(), '.env')

print(f"DIAGNOSTIC: Checking for .env file at: {dotenv_path}")
if load_dotenv(dotenv_path=dotenv_path):
    print("DIAGNOSTIC: ✅ python-dotenv LOAD SUCCESSFUL.")
else:
    print("DIAGNOSTIC: ❌ .env file not found or load failed. Relying on OS environment variables.")

# --- Step 2: Define Core Configuration Variables ---

# 1. API Key for the Dashboard (Authorization for the client/scraper to upload data)
DASHBOARD_API_KEY = os.environ.get('DASHBOARD_API_KEY') or os.environ.get('DASHBOARD_PRIVATE_KEY')

if not DASHBOARD_API_KEY:
    raise RuntimeError("DASHBOARD_API_KEY not found in environment or .env. Please configure this key.")
else:
    print(f"DIAGNOSTIC: ✅ Found key in os.environ under: {'DASHBOARD_API_KEY' if 'DASHBOARD_API_KEY' in os.environ else 'DASHBOARD_PRIVATE_KEY'}")

# 2. SEC Scraper Requirements
# The SEC mandates a User-Agent string for identification.
# Format: App Name (Name of Individual/Company; Email)
# *** ACTION REQUIRED: Update this with your actual name and email. ***
SEC_USER_AGENT = os.environ.get('SEC_USER_AGENT') or "InsiderTradingMonitor (Your Name; your.email@example.com)"

if SEC_USER_AGENT == "InsiderTradingMonitor (Nate Cook; nathanrcook766@gmail.com)":
    print("WARNING: Please update SEC_USER_AGENT in config.py or your .env file with your contact info to comply with SEC rules.")

# 3. Dashboard API Endpoint
DASHBOARD_HOST = 'http://127.0.0.1' 
DASHBOARD_PORT = 8000 # WARNING: Update this if your Flask server runs on a different port!

# CRITICAL: This is the target URL for uploading trades
API_ENDPOINT = f"{DASHBOARD_HOST}:{DASHBOARD_PORT}/api/upload_trades"

# 4. Data Storage File (Fixes the ImportError in dashboard.py)
# This is the path where the Flask dashboard will store the trade data locally.
DATA_FILE = os.environ.get('DATA_FILE') or "data/insider_trades.csv"

print("DIAGNOSTIC: Config loading successfully completed.")