import json
import os
import sys
from flask import Flask, request, jsonify, render_template_string

# --- FILTER CONSTANTS ---
# Only display trades with a reported value greater than or equal to this amount.
# Default is set to $100,000.00 to eliminate low-value/grant trades.
MIN_TRADE_VALUE = 100000.00 

# PULLS KEY: Imports DATA_FILE and DASHBOARD_API_KEY from config.py, but provide a
# friendly runtime error if the environment variable is missing so developers see
# a concise message rather than a stack trace.
try:
    from config import DATA_FILE, DASHBOARD_API_KEY
except RuntimeError as e:
    # Provide a helpful startup message (include accepted env var names so developers know
    # which vars the app will accept). We do not print the actual secret value.
    print("\nERROR: Missing required environment configuration: set DASHBOARD_API_KEY (preferred) or DASHBOARD_PRIVATE_KEY")
    print("Set one of these in the environment, or use a local .env and python-dotenv to auto-load them.\n")
    # Exit with a non-zero code so exec wrappers / CI recognize a failed startup
    sys.exit(1)

# --- Flask Setup ---
app = Flask(__name__)
# from flask_cors import CORS; CORS(app)

# Helper function to ensure the directory for DATA_FILE exists
def ensure_data_directory_exists():
    """Checks for and creates the directory path for DATA_FILE."""
    data_dir = os.path.dirname(DATA_FILE)
    if data_dir and not os.path.exists(data_dir):
        print(f"DIAGNOSTIC: Creating missing data directory: {data_dir}")
        os.makedirs(data_dir, exist_ok=True)


def load_data():
    """Loads trade data from the JSON file."""
    # Ensure directory exists before checking for file existence
    ensure_data_directory_exists()

    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            try:
                # Load the JSON array of trades.
                return json.load(f)
            except json.JSONDecodeError:
                print(f"Error decoding JSON from {DATA_FILE}. Returning empty list.")
                return []
    return []

def save_data(data):
    """
    Saves trade data to the JSON file.
    """
    ensure_data_directory_exists()
    
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=4)
    print(f"DIAGNOSTIC: Data successfully saved to {DATA_FILE} (Total records: {len(data)})")


# Helper function to safely convert value strings to sortable floats
def clean_and_convert_value(value_str):
    """Cleans a dollar-formatted string and returns a float, or 0.0 on failure."""
    try:
        if value_str is None:
            return 0.0
        # Ensure it's treated as a string, then clean it
        cleaned = str(value_str).replace('$', '').replace(',', '').strip()
        return float(cleaned)
    except (ValueError, TypeError):
        return 0.0


def deduplicate_trades(trades):
    """
    Removes duplicate trades based on a composite key:
    (date, ticker, filer, code, shares, price).
    """
    unique_trades = {}
    
    for trade in trades:
        # Create a tuple key that uniquely identifies the transaction
        # Uses date, issuer, filer, transaction type, quantity, and price.
        unique_key = (
            trade.get('date'),
            trade.get('ticker'),
            trade.get('filer'),
            trade.get('code'),
            # Convert shares and price to strings to make them hashable
            str(trade.get('shares', 0.0)),
            str(trade.get('price', 0.0))
        )
        
        # Use the dictionary to enforce uniqueness. If the key is already in 
        # the dict, we skip the trade, ensuring the first instance is kept.
        if unique_key not in unique_trades:
            unique_trades[unique_key] = trade
            
    return list(unique_trades.values())


# --- API Endpoint 1: Permanent Data Ingestion with Deduplication ---

@app.route('/api/upload_trades', methods=['POST'])
def upload_trades():
    """
    Receives new trade data, merges it with existing data, and deduplicates the
    ENTIRE set before saving. This is the permanent fix for duplicates.
    """

    if not request.is_json:
        return jsonify({"message": "Missing JSON in request"}), 400

    payload = request.get_json()
    
    # 1. AUTHENTICATION: Validate the incoming API key from the HTTP Header (X-API-KEY)
    received_key = request.headers.get('X-API-KEY')

    if received_key != DASHBOARD_API_KEY:
        print(f"Upload attempt failed. Invalid key received: {received_key}")
        return jsonify({"message": "Unauthorized: Invalid API Key"}), 403

    new_trades = payload.get('trades', [])

    if not isinstance(new_trades, list):
        return jsonify({"message": "Invalid data format: 'trades' must be a list"}), 400

    # Load existing data
    existing_trades = load_data()
    
    # Merge existing trades and new trades
    combined_trades = existing_trades + new_trades
    
    # Deduplicate the entire combined set
    initial_count = len(combined_trades)
    final_trades = deduplicate_trades(combined_trades)
    
    # Save the cleaned, final data, overwriting the old file
    save_data(final_trades)

    deduped_count = len(final_trades)
    duplicates_removed = initial_count - deduped_count
        
    return jsonify({"message": f"Successfully processed. Total unique trades saved: {deduped_count}. Removed {duplicates_removed} duplicates during merge."}), 200


# --- API Endpoint 2: One-Time Cleanup Tool ---

@app.route('/api/clean_data', methods=['POST'])
def clean_data():
    """
    One-time tool to clean duplicates from the existing data file.
    Requires API key for execution.
    """
    # 1. AUTHENTICATION: Validate the incoming API key
    received_key = request.headers.get('X-API-KEY')

    if received_key != DASHBOARD_API_KEY:
        return jsonify({"message": "Unauthorized: Invalid API Key"}), 403

    # Load existing data
    existing_trades = load_data()
    
    if not existing_trades:
        return jsonify({"message": "Data file is already empty or unreadable."}), 200

    # Deduplicate the existing set
    initial_count = len(existing_trades)
    cleaned_trades = deduplicate_trades(existing_trades)
    final_count = len(cleaned_trades)
    duplicates_removed = initial_count - final_count

    # Save the cleaned data
    save_data(cleaned_trades)

    if duplicates_removed > 0:
        message = f"Cleanup successful! Removed {duplicates_removed} duplicates. Total unique trades remaining: {final_count}."
    else:
        message = "Cleanup successful! No duplicates found in the existing file."

    print(f"DIAGNOSTIC: {message}")
    return jsonify({"message": message}), 200

# --- Dashboard Frontend with Sorting and Filtering ---

@app.route('/')
def dashboard():
    """Renders the main dashboard page, applying sorting and filtering."""
    all_trades = load_data()
    
    # --- 1. Get filter and sort parameters from the URL query string ---
    # Default sort is by date descending
    sort_by = request.args.get('sort_by', 'date') 
    # NEW: Get sort order (default is 'desc')
    sort_order = request.args.get('order', 'desc')
    filter_ticker = request.args.get('filter_ticker', '').upper().strip()
    
    # --- 2. Apply Filtering Logic ---
    
    # A. Filter by Minimum Value (Applies to ALL trades)
    trades_to_display = [
        trade for trade in all_trades
        if clean_and_convert_value(trade.get('value')) >= MIN_TRADE_VALUE
    ]

    # B. Filter by Ticker (Applies only if a ticker is entered in the form)
    if filter_ticker:
        trades_to_display = [
            trade for trade in trades_to_display 
            if trade.get('ticker', '').upper() == filter_ticker
        ]

    # --- 3. Apply Sorting Logic ---
    if trades_to_display:
        # Determine the key to sort by and the reversal direction
        reverse_sort = sort_order == 'desc'
        sort_key = sort_by
        
        if sort_key == 'value':
            # Use the new helper function for cleaner numeric sorting
            trades_to_display.sort(
                key=lambda x: clean_and_convert_value(x.get('value')),
                reverse=reverse_sort
            )
        else:
            # Sort by string keys (date, ticker, filer)
            trades_to_display.sort(key=lambda x: x.get(sort_key, ''), reverse=reverse_sort)
            
        # Get the date of the most recent trade for the header banner
        latest_update_date = max([t.get('date', '0000-00-00') for t in all_trades]) if all_trades else 'N/A'
    else:
        latest_update_date = 'N/A'


    # Simple logic to determine color based on transaction type
    def get_row_class(txn_type):
        # Dark mode colors
        if txn_type in ['P', 'Buy']: # P is typically Purchase, S is Sale
            return 'bg-green-900/40 hover:bg-green-800/40' # Darker, subtle green
        elif txn_type in ['S', 'Sell']:
            return 'bg-red-900/40 hover:bg-red-800/40' # Darker, subtle red
        return 'bg-gray-800/40 hover:bg-gray-700/40'

    # Helper to generate the new URL query string for sorting links
    def get_sort_link(column_name):
        # preserve the current filter if one exists
        filter_param = f"&filter_ticker={filter_ticker}" if filter_ticker else ""

        # Logic to toggle the sort order if the current column is clicked
        current_sort_by = request.args.get('sort_by', 'date')
        current_order = request.args.get('order', 'desc')

        if column_name == current_sort_by:
            # Toggle order: desc -> asc, asc -> desc
            new_order = 'asc' if current_order == 'desc' else 'desc'
        else:
            # New column clicked, reset to default (descending)
            new_order = 'desc'

        return f"/?sort_by={column_name}&order={new_order}{filter_param}"
    
    # Helper to generate the sort indicator for the header
    def get_sort_indicator(column_name):
        current_sort_by = request.args.get('sort_by', 'date')
        current_order = request.args.get('order', 'desc')

        if column_name == current_sort_by:
            return '▲' if current_order == 'asc' else '▼'
        return ''

    trade_rows = ""
    
    if trades_to_display:
        for trade in trades_to_display:
            # --- UPDATED: Fetching new fields ---
            ticker = trade.get('ticker', 'N/A')
            company_name = trade.get('company_name', 'Company Name Missing')
            filer_name = trade.get('filer', 'N/A')
            person_title = trade.get('person_title', 'Title Missing') # Assumes 'person_title' is the key
            trade_date = trade.get('date', 'N/A')
            code = trade.get('code', 'N/A')
            value = trade.get('value', 0.0)

            row_class = get_row_class(code)

            # Format the value for better readability
            try:
                formatted_value = f"${clean_and_convert_value(value):,.2f}"
            except Exception:
                formatted_value = "$N/A"
                    
            trade_rows += f"""
            <tr class="{row_class} border-b border-gray-700 transition duration-150 ease-in-out">
                <td class="px-4 py-3 font-semibold text-blue-300">{ticker}</td>
                <td class="px-4 py-3 text-white">{company_name}</td>
                <td class="px-4 py-3 text-gray-300">{filer_name}</td>
                <td class="px-4 py-3 text-gray-400 text-sm italic">{person_title}</td>
                <td class="px-4 py-3 whitespace-nowrap text-gray-400">{trade_date}</td>
                <td class="px-4 py-3 font-mono text-right text-lg font-bold">{formatted_value}</td>
                <td class="px-4 py-3 font-extrabold text-center">{code}</td>
            </tr>
            """
    else:
        trade_rows = f"""
        <tr>
            <td colspan="7" class="p-4 text-center text-gray-500">
                No trades found matching the minimum value filter (${MIN_TRADE_VALUE:,.2f}) 
                and/or the ticker filter "{filter_ticker}".
            </td>
        </tr>
        """
        

    html_template = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SEC Insider Trading Dashboard | Dark Mode</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;900&display=swap');
        body {{ 
            font-family: 'Inter', sans-serif; 
            background-color: #111827; /* Dark BG */
            color: #d1d5db; /* Light text */
        }}
        .sortable-header:hover {{ cursor: pointer; color: #60a5fa; }}
        .header-link {{ color: #9ca3af; }}
        .header-link:hover {{ color: #e5e7eb; }}
        /* Specific code colors */
        .bg-green-900/40 {{ color: #34d399; }} /* Green text for Buy */
        .bg-red-900/40 {{ color: #f87171; }} /* Red text for Sell */
        .bg-gray-800/40 {{ color: #9ca3af; }} /* Grey text for Other */
    </style>
</head>
<body>
    <div class="container mx-auto p-4 sm:p-8">
        <header class="text-center mb-10">
            <h1 class="text-4xl font-extrabold text-white mb-2">Insider Trading Monitor</h1>
            <p class="text-lg text-gray-400">Latest trades reported to the SEC (Updated via API)</p>
            <div class="text-sm mt-4 p-2 inline-block rounded-full bg-blue-900/50 text-blue-300 font-medium border border-blue-800">
                Latest Trade Date: {latest_update_date}
            </div>
            <p class="text-xs mt-2 text-gray-500">
                Displaying trades greater than or equal to ${MIN_TRADE_VALUE:,.2f}
            </p>
        </header>
        
        <!-- Filter Form -->
        <form method="GET" action="/" class="mb-6 flex flex-col sm:flex-row gap-3 sm:gap-4 p-4 bg-gray-900 shadow-2xl rounded-xl items-center border border-gray-700">
            <!-- Hidden inputs to preserve sort state during filter -->
            <input type="hidden" name="sort_by" value="{sort_by}">
            <input type="hidden" name="order" value="{sort_order}">
            
            <label for="filter_ticker" class="text-gray-300 font-medium whitespace-nowrap w-full sm:w-auto text-left sm:text-center">Filter by Ticker:</label>
            <input type="text" id="filter_ticker" name="filter_ticker" value="{filter_ticker}"
                    placeholder="e.g., AAPL, TSLA" 
                    class="flex-grow w-full p-3 border border-gray-600 bg-gray-800 text-white rounded-lg focus:ring-blue-500 focus:border-blue-500 uppercase transition duration-150">
            <button type="submit" class="w-full sm:w-auto bg-blue-600 hover:bg-blue-500 text-white font-bold py-3 px-6 rounded-lg transition duration-150 shadow-lg shadow-blue-500/30">
                Apply Filter
            </button>
            <a href="/" class="w-full sm:w-auto text-gray-400 hover:text-white py-3 px-6 text-center border border-gray-700 rounded-lg transition duration-150">
                Clear
            </a>
        </form>
            
        <div class="shadow-2xl rounded-xl overflow-hidden bg-gray-900 border border-gray-800">
            <div class="overflow-x-auto">
                <table class="min-w-full divide-y divide-gray-700">
                    <thead class="bg-gray-800">
                        <tr>
                            <!-- Clickable Headers for Sorting -->
                            <th class="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider whitespace-nowrap">
                                <a href="{get_sort_link('ticker')}" class="sortable-header header-link block">Ticker {get_sort_indicator('ticker')}</a>
                            </th>
                            <th class="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider whitespace-nowrap">
                                <a href="{get_sort_link('company_name')}" class="sortable-header header-link block">Company {get_sort_indicator('company_name')}</a>
                            </th>
                            <th class="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider whitespace-nowrap">
                                Insider Name
                            </th>
                            <th class="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider whitespace-nowrap">
                                Title
                            </th>
                            <th class="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider whitespace-nowrap">
                                <a href="{get_sort_link('date')}" class="sortable-header header-link block">Date {get_sort_indicator('date')}</a>
                            </th>
                            <th class="px-4 py-3 text-right text-xs font-medium text-gray-400 uppercase tracking-wider whitespace-nowrap">
                                <a href="{get_sort_link('value')}" class="sortable-header header-link block">Value {get_sort_indicator('value')}</a>
                            </th>
                            <th class="px-4 py-3 text-center text-xs font-medium text-gray-400 uppercase tracking-wider whitespace-nowrap">
                                Type
                            </th>
                        </tr>
                    </thead>
                    <tbody class="divide-y divide-gray-800">
                        {trade_rows}
                    </tbody>
                </table>
            </div>
        </div>
        
        <footer class="mt-10 text-center text-sm text-gray-600">
            Data provided by a mock SEC scraper and transmitted securely via API.
        </footer>
    </div>
</body>
</html>
""" 
    
    return render_template_string(html_template)
            
def get_available_port(preferred=5000, host='127.0.0.1'):
    """Return preferred port if it is free; otherwise return an ephemeral free port.
    This tries to bind then release the socket to avoid port race for dev use only.
    """
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, preferred))
        s.close()
        return preferred
    except OSError:
        # Preferred is taken; get an ephemeral free port
        s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s2.bind((host, 0))
        port = s2.getsockname()[1]
        s2.close()
        return port

if __name__ == '__main__':
    # Initialize the data file and ensure the directory exists
    try:
        # CRITICAL FIX: Ensure the directory exists before attempting any file operation.
        ensure_data_directory_exists()

        if not os.path.exists(DATA_FILE):
            print(f"DIAGNOSTIC: Data file {DATA_FILE} not found. Creating empty file.")
            save_data([]) # This also calls ensure_data_directory_exists
    except Exception as e:
        print(f"FATAL ERROR during data file initialization: {e}", file=sys.stderr)
        sys.exit(1) # Exit if we cannot initialize the file system

    print(f"--- Flask Server Starting ---")
    
    # Allow env-var override and fallback to available ports for dev convenience
    host = os.environ.get('HOST', '127.0.0.1')
    env_port = os.environ.get('PORT')
    # Use 8000 as the new default preferred port
    preferred_port = int(env_port) if env_port else 8000 
    
    port_to_use = get_available_port(preferred=preferred_port, host=host)

    # Now we print the correct port being used
    print(f"Dashboard available at: http://127.0.0.1:{port_to_use}/")
    print(f"API Endpoints: /api/upload_trades (Permanent Fix) | /api/clean_data (One-Time Tool)")

    # For security, do not print the full API key. Print an informational message instead.
    if DASHBOARD_API_KEY:
        print(f"Dashboard API key is configured (length={len(DASHBOARD_API_KEY)}). Not printing it for security reasons.")
    
    if port_to_use != preferred_port:
        print(f"Port {preferred_port} in use; automatically starting on {port_to_use}")
        
    app.run(host=host, port=port_to_use, debug=True, use_reloader=False) # use_reloader=False to prevent double execution