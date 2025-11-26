import requests
import xml.etree.ElementTree as ET
import re
import sys
import time
from datetime import datetime, timedelta
from collections import defaultdict
import json

# --- Configuration Imports ---
# PULLS: DASHBOARD_API_KEY, SEC_USER_AGENT, API_ENDPOINT
from config import DASHBOARD_API_KEY, SEC_USER_AGENT, API_ENDPOINT

# --- Configuration for SEC Scraper ---
HEADERS = {
    'User-Agent': SEC_USER_AGENT
}
REQUEST_DELAY = 0.15 # Adhere to SEC rate limits

# SMART FILTER: Focus on discretionary trades with high informational value.
TARGET_CODES = ['P', 'S', 'M', 'X', 'V'] # P=Purchase, S=Sale, M/X=Exercise/Conversion, V=Volunteer Filing
# Codes used for Estimated Dollar Value calculation (only P and S are typically cash transactions)
VALUE_CODES = ['P', 'S']

# --- FILTER CONSTANTS ---
MIN_TRADE_VALUE = 1000000.00 # $1,000,000.00
MEGA_TRADE_THRESHOLD = 10000000.00 # $10,000,000.00

# NOTE: MAX_FILING_COUNT has been removed to pull all trades for the target day.

NAMESPACE = '{http://www.sec.gov/edgar/v1}'


# --- Utility Functions ---

def get_edgar_archive_date_url(date):
    """Generates the URL for the master index file for a given date."""
    year = date.strftime('%Y')
    quarter = f"QTR{(date.month - 1) // 3 + 1}"
    date_path = date.strftime('%Y%m%d')
    return f"https://www.sec.gov/Archives/edgar/daily-index/{year}/{quarter}/master.{date_path}.idx"


def get_last_business_day():
    """
    Calculates and returns the most recent business day (Mon-Fri) that has passed.
    This starts checking from yesterday to ensure the SEC index is published.
    """
    current_date = datetime.now()
    
    # Start looking back from yesterday
    check_date = current_date - timedelta(days=1)

    while True:
        # 0=Monday, 6=Sunday
        if check_date.weekday() < 5: 
            return check_date # Found a Mon-Fri
        check_date -= timedelta(days=1)


def get_form4_urls_from_index(date):
    """
    Downloads the master.idx file and filters for ALL Form 4 filings for the given date.
    """
    index_url = get_edgar_archive_date_url(date)
    form4_urls = []

    print(f"  -> Downloading Index for {date.strftime('%Y-%m-%d')}...")

    try:
        response = requests.get(index_url, headers=HEADERS, timeout=30)
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        if response.status_code == 404:
            # This is common if the day's index is not yet published
            print(f"  [Warning] Index not found (404) for {date.strftime('%Y-%m-%d')}.", file=sys.stderr)
            return []
        print(f"  [Error] Failed to download index {index_url}: {e}", file=sys.stderr)
        return []
    except requests.exceptions.RequestException as e:
        if "NameResolutionError" in str(e):
            print(f"  [Error] Network error downloading index {index_url}: Name resolution failed. Check network/DNS.", file=sys.stderr)
        else:
            print(f"  [Error] Network error downloading index {index_url}: {e}", file=sys.stderr)
        return []

    # Iterate through index lines
    for line in response.text.splitlines():
        
        parts = line.split('|')
        if len(parts) >= 3 and parts[2] == '4':
            if len(parts) == 5:
                file_path = parts[4]
                full_url = f"https://www.sec.gov/Archives/{file_path}"
                form4_urls.append(full_url)
    
    print(f"  -> Found {len(form4_urls)} Form 4 filings for processing.")
    return form4_urls

def extract_value_via_iteration(parent_element, target_tag_name):
    """
    The most robust way to find a value: iterates over all descendants of the parent
    and checks if the tag name ends with the target_tag_name (e.g., 'transactionShares' or 'value').
    """
    value = None
    
    # 1. First, find the container tag (e.g., transactionShares)
    container_element = None
    for element in parent_element.iter():
        if element.tag.endswith(target_tag_name):
            container_element = element
            break
        
    if container_element is None:
        return None

    # 2. Once the container is found, look for the 'value' tag within its descendants.
    for element in container_element.iter():
        if element.tag.endswith('value') and element.text:
            value = element.text.strip()
            break
    
    if value: 
        try:
            # Clean up the value (remove commas) and convert to float
            return float(value.replace(',', '').strip())
        except ValueError:
            pass
        
    return 0.0
    
def clean_and_extract_xml(xml_text):
    """
    Isolates the pure XML block from the surrounding TXT container and cleans it up.
    """
    START_TAG = "<ownershipDocument"
    END_TAG = "</ownershipDocument>"
    
    # Find the start and end of the XML block
    start_match = re.search(START_TAG, xml_text, re.IGNORECASE)
    end_match = re.search(END_TAG, xml_text, re.IGNORECASE | re.DOTALL)

    if not start_match:
        raise ValueError("Could not find the starting tag: <ownershipDocument>")

    xml_start_index = start_match.start()

    if end_match:
        xml_end_index = end_match.end()
        cleaned_xml = xml_text[xml_start_index:xml_end_index]
    else:
        # Fallback if end tag is missing (though this shouldn't happen)
        cleaned_xml = xml_text[xml_start_index:]
            
    # Remove XML declaration line (e.g., <?xml version="1.0" ... ?>)     
    cleaned_xml = re.sub(r'<\?xml[^>]*\?>', '', cleaned_xml, flags=re.IGNORECASE)
    
    cleaned_xml = cleaned_xml.strip()
        
    return cleaned_xml
        
def parse_form4_url(xml_url):
    """
    Downloads, cleans, and parses a single Form 4 XML file, extracting trades
    that match the TARGET_CODES filter.
    """
    trades = []
                        
    # Adhere to rate limit
    time.sleep(REQUEST_DELAY)
    
    try:
        response = requests.get(xml_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        xml_text = response.text
    except requests.exceptions.RequestException:
        print(f"    [Error] Failed to download {xml_url.split('/')[-1]}", file=sys.stderr)
        return trades
            
    try:
        cleaned_xml_text = clean_and_extract_xml(xml_text)
        root = ET.fromstring(cleaned_xml_text)
    
        # --- 1. EXTRACT FILER AND ISSUER METADATA ---
        issuer_name = 'UNKNOWN'
        issuer_ticker = 'N/A'
        filer_name = 'UNKNOWN'
        filer_relationship = 'N/A'
        
        # Iterate to find primary identifiers
        for element in root.iter():
            if element.tag.endswith('issuerName') and element.text:
                issuer_name = element.text.strip()
            elif element.tag.endswith('issuerTradingSymbol') and element.text:
                issuer_ticker = element.text.strip().upper()
            elif element.tag.endswith('rptOwnerName') and element.text:
                filer_name = element.text.strip()
            # Try to get explicit title first (this is the most useful field)
            elif element.tag.endswith('rptOwnerTitle') and element.text:
                filer_relationship = element.text.strip()
        
        # If explicit title is missing, infer relationship from boolean flags
        if filer_relationship == 'N/A' or filer_relationship == '':
            relationship_flags = []
    
            # Check for boolean flags (usually 1 for True, 0 for False)
            if root.find('.//isDirector') is not None and root.find('.//isDirector').text.strip() == '1':
                relationship_flags.append('Director')
            if root.find('.//isOfficer') is not None and root.find('.//isOfficer').text.strip() == '1':
                relationship_flags.append('Officer')
            if root.find('.//isTenPercentOwner') is not None and root.find('.//isTenPercentOwner').text.strip() == '1':
                relationship_flags.append('10% Owner')
            
            # --- CHECK FOR ISOTHER AND OTHERTEXT ---
            is_other_flag = root.find('.//isOther')
            if is_other_flag is not None and is_other_flag.text.strip() == '1':
                other_text_element = root.find('.//otherText')
                if other_text_element is not None and other_text_element.text:
                    # If otherText is provided, use it as the relationship
                    relationship_flags.append(other_text_element.text.strip())
                else:
                    # If isOther is checked but no text is given
                    relationship_flags.append('Other (Filer Specified)')
            
            if relationship_flags:
                # Set relationship to the combined list (e.g., "Director, 10% Owner")
                filer_relationship = ", ".join(relationship_flags)
            else:
                # Default to 'Other' only if absolutely no relationship data is found
                filer_relationship = 'Other'
        
        
        # --- 2. EXTRACT TRANSACTIONS ---
                        
        # Find all transaction elements (trying with and without namespace)
        non_derivative_transactions = root.findall(f'.//{NAMESPACE}nonDerivativeTransaction') or root.findall('.//nonDerivativeTransaction')
        derivative_transactions = root.findall(f'.//{NAMESPACE}derivativeTransaction') or root.findall('.//derivativeTransaction')
                        
        # Process transactions
        for transaction in non_derivative_transactions + derivative_transactions:
            
            transaction_code = 'N/A'
            transaction_date = 'N/A'
        
            # Extract Code and Date using iteration
            for element in transaction.iter():
                if element.tag.endswith('transactionCode') and element.text:
                    transaction_code = element.text.strip().upper()
                # Find transaction date. This relies on the internal <value> tag.
                elif element.tag.endswith('transactionDate'):
                    date_value_element = element.find('.//value')
                    if date_value_element is not None and date_value_element.text:
                        transaction_date = date_value_element.text.strip()
            
            # 1. Transaction Code (MANDATORY FILTER)
            if transaction_code not in TARGET_CODES:
                continue
        
            # 2. Shares/Amount (using hyper-robust function)
            shares = extract_value_via_iteration(transaction, 'transactionShares')
    
            # Skip if no shares or amount
            if shares == 0.0:
                continue
        
            # 3. Price per share (using hyper-robust function)
            price = extract_value_via_iteration(transaction, 'transactionPricePerShare')
    
            # Calculate Value (Shares * Price)
            value = shares * price
        
            # Flag if this transaction code should be used for the value summary (still only P and S)
            is_value_trade = transaction_code in VALUE_CODES
        
            trade = {
                'date': transaction_date,
                'code': transaction_code,
                'ticker': issuer_ticker,
                'shares': shares,
                'price': price,
                'value': value,
                'company_name': issuer_name,
                'filer': filer_name,
                'person_title': filer_relationship,
                'is_value_trade': is_value_trade
            }
            # --- APPLY MINIMUM VALUE FILTER ---
            # We only upload trades that are over the $1M threshold
            if trade['is_value_trade'] and trade['value'] >= MIN_TRADE_VALUE:
                trades.append(trade)
            elif not trade['is_value_trade']:
                # Always include non-value trades (M, X, V) for completeness, as their value is often $0
                trades.append(trade)

    except ValueError as ve:
        # Catch errors from clean_and_extract_xml
        print(f"    [Error] XML Cleanup failed for {xml_url.split('/')[-1]}: {ve}", file=sys.stderr)
        pass
    except ET.ParseError as pe:
        # Catch errors from ET.fromstring
        print(f"    [Error] XML Parse failed for {xml_url.split('/')[-1]}: {pe}", file=sys.stderr)
        pass
    except Exception as e:
        # Catch all other errors silently to maximize data extraction
        print(f"    [Error] Unexpected error processing {xml_url.split('/')[-1]}: {e}", file=sys.stderr)
        pass
    
    return trades
    
# --- API Upload Function ---
    
def upload_trades_to_dashboard(trades, api_key, run_time, summary_data):
    """    
    Sends a list of trade dictionaries, run time, and summary data to the API endpoint.
    """
    headers = {
        'X-API-KEY': api_key,
        'Content-Type': 'application/json'
    }        
    
    # CRITICAL: Include the run_time tag and summary data in the payload
    payload = {
        'run_time': run_time,
        'trades': trades,
        'summary': summary_data
    }
    
    print(f"\n--- Attempting to upload {len(trades)} trade(s) to dashboard at {API_ENDPOINT} ---")

    try:
        response = requests.post(API_ENDPOINT, headers=headers, json=payload, timeout=10)
    
        if response.status_code == 200:
            print(f"✅ DASHBOARD UPLOAD SUCCESSFUL. Trades uploaded: {len(trades)}")
        elif response.status_code == 403:
            print(f"❌ UPLOAD FAILED (403 Forbidden): Check DASHBOARD_API_KEY and server configuration.")
        elif response.status_code == 400:
            print(f"❌ UPLOAD FAILED (400 Bad Request): Server rejected data format.")
            print(f"Server Message: {response.json().get('message', 'No JSON message.')}")
        else:
            print(f"❌ UPLOAD FAILED (Status Code: {response.status_code})")
    
    except requests.exceptions.RequestException as e:
        print(f"❌ CONNECTION ERROR. Is the Flask server (app.py) running at {API_ENDPOINT}? Error: {e}")
    
        
# --- Reporting and Main Execution ---
            
def format_report_row(trade):
    """Formats a single trade entry for the final report."""
    date = trade['date'].ljust(10)
    code = trade['code'].ljust(4)
    ticker = trade['ticker'].ljust(8)
    shares = f"{trade['shares']:,.0f}".rjust(12)
                        
    # Format Price: Use currency format, or N/A if 0.0
    price_str = f"${trade['price']:,.2f}" if trade['price'] > 0 else "N/A"
    price_formatted = price_str.rjust(14)
                        
    # Format Value
    value_formatted = f"${trade['value']:,.2f}".rjust(20)
        
    # Format Company Name (truncate to 25 chars)
    company_name_truncated = trade['company_name'][:25].ljust(25) 
            
    # Format Filer Name (truncate to 25 chars)
    filer_name_truncated = trade['filer'][:25].ljust(25)
    
    # Format Title (truncate to 20 chars)
    person_title_truncated = trade['person_title'][:20].ljust(20)

    # Updated return string to use new formatted variables
    return f"{date} {code} {ticker} {shares} {price_formatted} {value_formatted} {company_name_truncated} {filer_name_truncated} {person_title_truncated}"
    
def main():
    print("Initializing SEC Form 4 Insider Trading Monitor...")
    
    # --- Capture the Run Time for Dashboard Tagging ---
    run_time = datetime.now().isoformat()
    print(f"Script run time (ISO 8601): {run_time}")
    
    all_trades = []
    transaction_code_counts = defaultdict(int)
    total_value_all = 0.0
    total_trades_count_all = 0 
    
    # New variables for mega trade tracking
    mega_trade_total_value = 0.0
    mega_trade_count = 0
    
    # --- DYNAMIC LIMITS IMPLEMENTATION ---
    target_date = get_last_business_day()
    
    print(f"Targeting Form 4 filings from the LAST BUSINESS DAY: {target_date.strftime('%Y-%m-%d')}")
    print("Processing ALL available filings for this day (no hard limit).")
    print(f"Only uploading trades with Estimated Dollar Value > ${MIN_TRADE_VALUE:,.2f} USD or non-value trades (M, X, V).")
    
    date = target_date
            
    print(f"\nProcessing date: {date.strftime('%Y-%m-%d')} (Pulling all filings)")
    
    # NO MAX_FILING_COUNT PASSED HERE
    urls = get_form4_urls_from_index(date)
    
    # Process each URL found
    for i, url in enumerate(urls): 
        print(f"    Parsing filing {i+1}/{len(urls)}: {url.split('/')[-1]}", end='\r')
    
        trades = parse_form4_url(url)

        for trade in trades:
            all_trades.append(trade)
            transaction_code_counts[trade['code']] += 1
            total_trades_count_all += 1
        
            # Only include P and S (Purchase/Sale) in the total dollar value calculation
            if trade['is_value_trade']:
                total_value_all += trade['value']
                
                # Check for mega trade status
                if trade['value'] >= MEGA_TRADE_THRESHOLD:
                    mega_trade_count += 1
                    mega_trade_total_value += trade['value']
    
    # Ensure we print a newline after the progress indicator
    print(" " * 80, end='\r') # Clear the line
    print(f"  Finished processing {len(urls)} filings for {date.strftime('%Y-%m-%d')}.")
    
    # --- Summary Data for Dashboard ---
    summary_data = {
        'mega_trade_count': mega_trade_count,
        'mega_trade_total_value': mega_trade_total_value,
        'min_trade_value': MIN_TRADE_VALUE
    }
    
    # --- Upload to Dashboard after data collection is complete ---
    upload_trades_to_dashboard(all_trades, DASHBOARD_API_KEY, run_time, summary_data)

    
    # --- Generate Console Report (Using the uploaded/filtered data) ---

    # Sort trades by value descending
    sorted_trades = sorted(all_trades, key=lambda x: x['value'], reverse=True)
        
    print("\n" + "="*145)
    print(f"AGGREGATE INSIDER TRADING REPORT (Targeting: {target_date.strftime('%Y-%m-%d')})")
    print(f"Data Last Refreshed (Script Runtime): {run_time}")
    print("="*145)
        
    print("SMART FILTER CODES INCLUDED: " + ", ".join(TARGET_CODES))
    print(f"*** CONSOLE REPORT is for trades over ${MIN_TRADE_VALUE:,.2f} USD and non-value trades. (ALL available filings processed) ***")
        
    print("\nSUMMARY OF TRANSACTIONS FOUND:")
    for code, count in sorted(transaction_code_counts.items(), key=lambda item: item[1], reverse=True):
        print(f"  Code {code}: {count} transactions")
            
    print(f"\nTotal Filtered Transactions Found: {total_trades_count_all}")
    print(f"Total Estimated Dollar Value (P/S only, filtered): ${total_value_all:,.2f}")
    print(f"*** MEGA TRADES (> ${MEGA_TRADE_THRESHOLD:,.2f}) Found: {mega_trade_count} valued at ${mega_trade_total_value:,.2f} ***\n")
        
    # Updated console header to reflect the changes
    print("Date              Code Ticker        Shares             Price                  Value (USD)             Company (25 chars)          Filer (25 chars)          Title (20 chars)")
    print("----------------------------------------------------------------------------------------------------------------------------------------------------")

    # Display top 20 trades
    for trade in sorted_trades[:20]:
        print(format_report_row(trade))
    
    print("\n----------------------------------------------------------------------------------------------------------------------------------------------------")
    print(f"NOTE: Displaying top {min(20, total_trades_count_all)} trades by value. Filtering on codes: {', '.join(TARGET_CODES)}. Dollar value based only on P and S codes.")
    
if __name__ == '__main__':
    main()