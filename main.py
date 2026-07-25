import os
import io
import math
import uuid
import random
import logging
import json
import base64
import urllib.request
import urllib.parse
from typing import List, Dict, Optional
from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from PIL import Image
from google import genai
from google.genai import types

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("storeassociate")

# Load environment variables
# Note: In a production environment, use a secure secret manager.
# For local development we read from .env if present.
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            if line.strip() and not line.startswith("#"):
                key, val = line.strip().split("=", 1)
                os.environ[key] = val

# Initialize Google GenAI client
try:
    api_key = os.getenv("GEMINI_API_KEY")
    use_vertex = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "false").lower() == "true"
    
    if use_vertex:
        client = genai.Client()
        logger.info("Successfully initialized Gemini Client using Vertex AI")
    else:
        # Pass api_key explicitly to force Gemini Developer API endpoint usage
        client = genai.Client(api_key=api_key)
        logger.info("Successfully initialized Gemini Client using API Key")
except Exception as e:
    logger.error(f"Failed to initialize Gemini Client: {e}")
    client = None

app = FastAPI(title="StoreAssociate Action Figures Trade-In App")

# Define target model
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-flash-latest")

# Define Pydantic models for request/response structured outputs
class EbaySale(BaseModel):
    title: str = Field(description="The listing title of the completed/sold eBay auction")
    price: float = Field(description="The final sold price in USD")
    date: Optional[str] = Field(default="Recent", description="Approximate date or time ago of the sale (e.g. '3 days ago')")
    shipping: Optional[float] = Field(default=0.0, description="Shipping cost in USD, or 0.0 if free shipping or not visible")
    url: str = Field(description="The source URL of the eBay listing (e.g., https://www.ebay.com/itm/...)")
    ebayCondition: Optional[str] = Field(default="Unknown", description="Condition of the item in this listing as described on eBay (e.g., PSA 10 Gem Mint, Near Mint, Worn)")

class ActionFigureScanResult(BaseModel):
    productName: str = Field(description="Primary identified name of the collectible item")
    possibleAlternativeNames: List[str] = Field(description="List of possible alternative titles or search names for this item")
    series: str = Field(description="Toy line, series, or trading card set name (e.g., Hazbin Hotel Promo Cards, The Vintage Collection)")
    manufacturer: str = Field(description="Manufacturer name (e.g. PSA, Kenner, Hasbro, Toy Biz)")
    year: int = Field(description="Release or print year of the collectible")
    barcode: str = Field(description="UPC, barcode digits, or serial/certificate numbers if visible, otherwise 'Not Visible'")
    conditionGrade: str = Field(description="Condition grade: 'Mint/Near Mint', 'Fine/Very Fine', 'Good', 'Fair/Poor'")
    conditionNotes: str = Field(description="Brief notes describing item/packaging wear, creases, dents, scratches, or graded status")
    ebayMatchFound: bool = Field(description="True if matching completed sales were found on eBay, otherwise False")
    recentSales: List[EbaySale] = Field(description="List of up to 15 actual completed/sold eBay listings found via Google Search. If none are found, leave empty.")

class TradeInQuote(BaseModel):
    productName: str
    possibleAlternativeNames: List[str]
    series: str
    manufacturer: str
    year: int
    barcode: str
    conditionGrade: str
    conditionNotes: str
    ebayMatchFound: bool
    ebayAveragePrice: float
    ebayMedianPrice: float
    conditionAdjustedValue: float
    cashOffer: float
    creditOffer: float
    recentSales: List[Dict]
    isFallbackPricing: bool

# Helpers for live eBay Buy Browse API integration
def get_ebay_access_token(client_id: str, client_secret: str) -> Optional[str]:
    try:
        url = "https://api.ebay.com/identity/v1/oauth2/token"
        auth_str = f"{client_id}:{client_secret}"
        encoded_auth = base64.b64encode(auth_str.encode()).decode()
        
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {encoded_auth}"
        }
        data = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope"
        }).encode("utf-8")
        
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            return res_data.get("access_token")
    except Exception as e:
        logger.error(f"Failed to get eBay access token: {e}")
        return None

def search_ebay_active_listings(query: str, token: str) -> List[Dict]:
    try:
        params = urllib.parse.urlencode({
            "q": query,
            "limit": 10
        })
        url = f"https://api.ebay.com/buy/browse/v1/item_summary/search?{params}"
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=10) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            items = res_data.get("itemSummaries", [])
            
            sales = []
            for item in items:
                price_val = 0.0
                try:
                    price_val = float(item.get("price", {}).get("value", 0.0))
                except (ValueError, TypeError):
                    pass
                
                shipping_val = 0.0
                shipping_options = item.get("shippingOptions", [])
                if shipping_options:
                    try:
                        shipping_val = float(shipping_options[0].get("shippingCost", {}).get("value", 0.0))
                    except (ValueError, TypeError):
                        pass
                
                condition_str = item.get("condition", "Unknown")
                
                sales.append({
                    "title": item.get("title", ""),
                    "price": price_val,
                    "date": "Active Listing",
                    "shipping": shipping_val,
                    "url": item.get("itemWebUrl", ""),
                    "ebayCondition": condition_str
                })
            return sales
    except Exception as e:
        logger.error(f"Failed to search eBay active listings: {e}")
        return []

# Deterministic helper to generate mock eBay completed sales
def fetch_ebay_completed_prices(product_name: str, series: str, manufacturer: str, year: int) -> List[Dict]:
    """
    Simulates eBay completed sales data scraper.
    Uses product metadata to seed random pricing for consistency.
    """
    search_query = f"{manufacturer} {series} {product_name} {year}".lower()
    
    # Create a stable seed based on product details so the same toy gets consistent prices
    seed_val = sum(ord(c) for c in product_name) + year
    # Also incorporate manufacturer and series characters
    seed_val += sum(ord(c) for c in series) + sum(ord(c) for c in manufacturer)
    
    random.seed(seed_val)
    
    # Calculate base market value based on release age (vintage is worth more)
    age = max(1, 2026 - year)
    
    # Base price calculation
    base_price = 10.0 + (age * random.uniform(1.5, 12.0))
    
    # Apply brand/franchise multipliers
    if "star wars" in search_query or "kenner" in search_query:
        base_price *= random.uniform(1.3, 2.8)
    elif "transformers" in search_query or "hasbro" in search_query:
        base_price *= random.uniform(1.2, 2.2)
    elif "motu" in search_query or "he-man" in search_query or "mattel" in search_query:
        base_price *= random.uniform(1.4, 2.5)
        
    base_price = round(base_price, 2)
    
    # Generate 5 realistic completed sales
    sales = []
    for i in range(5):
        # Allow +/- 15% variation in sale prices
        variation = random.uniform(-0.15, 0.15)
        sale_price = round(base_price * (1.0 + variation), 2)
        days_ago = random.randint(1, 30)
        
        sales.append({
            "title": f"VINTAGE {year} {manufacturer.upper()} {series.upper()} {product_name.upper()} Action Figure - Sold",
            "price": sale_price,
            "date": f"{days_ago} days ago",
            "shipping": round(random.uniform(4.99, 12.99), 2),
            "url": f"https://www.ebay.com/itm/{random.randint(100000000000, 999999999999)}",
            "ebayCondition": "Mint / Sealed"
        })
    return sales
        
def calculate_median(prices: List[float]) -> float:
    if not prices:
        return 0.0
    sorted_prices = sorted(prices)
    n = len(sorted_prices)
    m = n // 2
    if n % 2 == 0:
        return round((sorted_prices[m - 1] + sorted_prices[m]) / 2, 2)
    return round(sorted_prices[m], 2)

# API Route to handle image/camera scan uploads
@app.post("/api/scan", response_model=TradeInQuote)
async def scan_action_figure(
    image: UploadFile = File(...),
    manual_barcode: Optional[str] = Form(None)
):
    if not client:
        raise HTTPException(status_code=500, detail="Gemini client is not initialized. Please configure GEMINI_API_KEY.")
        
    try:
        # Read uploaded image bytes
        img_bytes = await image.read()
        pil_img = Image.open(io.BytesIO(img_bytes))
        
        # Convert image to PNG format to pass to Gemini
        img_buffer = io.BytesIO()
        pil_img.save(img_buffer, format="PNG")
        img_payload = img_buffer.getvalue()
        
        logger.info(f"Sending image to Gemini model: {MODEL_NAME}")
        
        # Prompt for Gemini image model to extract toys information, search completed sales & grade card condition
        prompt = """
        You are an expert action figure and trading card appraiser assisting a GameStop store associate.
        Analyze the uploaded image of a collectible (packaged action figure or graded/raw trading card).
        
        Step 1: Identify the product details:
        - productName (Primary identified name of the collectible item)
        - possibleAlternativeNames (List of 2-3 other common names or search queries for this item)
        - series (Toy line, series, or trading card set name)
        - manufacturer (Manufacturer name or grading service, e.g. PSA, Kenner, Hasbro)
        - year (Release or print year)
        - barcode (UPC, barcode digits, or serial/certificate numbers if visible, otherwise 'Not Visible')
        - conditionGrade (Condition grade: 'Mint/Near Mint', 'Fine/Very Fine', 'Good', 'Fair/Poor')
        - conditionNotes (Brief notes describing item/packaging wear, creases, dents, scratches, or graded status)
        - ebayMatchFound (True if actual matching completed sold listings exist on eBay, otherwise False)
        
        Step 2: Use the Google Search tool to search for recently completed, sold eBay auction listings for this exact collectible.
        - First, formulate a broad search query like: "[manufacturer] [series] [productName] [year] sold completed on ebay" to discover matching listings.
        - Second, to prevent price mismatches and confirm exact listing details, perform follow-up targeted search queries using the discovered listing titles or URLs (e.g. search for the specific eBay item ID or title on Google Search) to retrieve the full listing details, sold dates, and exact final transaction prices from Google's cached snippets.
        - Find as many actual completed sold listings as possible (up to 15).
        
        CRITICAL ACCURACY CONSTRAINTS:
        - For every item in `recentSales`, the listing title, sold price, and URL must match the exact same listing. Do NOT mismatch a URL of one item with the price of a recommended or sponsored item from the search snippet.
        - The `price` must be the final sold price of the item itself (excluding shipping).
        - The `url` must be a valid, standard eBay item URL following the exact pattern: `https://www.ebay.com/itm/[12-digit-numerical-id]` (e.g. `https://www.ebay.com/itm/134902500432`). Do NOT use text-based slug URLs (e.g. `https://www.ebay.com/itm/jada-toys-megaman`) as they are invalid and result in 404 page not found errors.
        - If the exact price and a valid numerical URL are not clearly visible in the search metadata or snippet for a specific listing, do not include it in the `recentSales` list. No fake or hallucinated data.
        """
        
        # Call Gemini with structured output constraints and Google Search grounding enabled
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=[
                types.Part.from_bytes(
                    data=img_payload,
                    mime_type="image/png"
                ),
                prompt
            ],
            config=types.GenerateContentConfig(
                tools=[{"google_search": {}}],
                response_mime_type="application/json",
                response_schema=ActionFigureScanResult,
                temperature=0.1
            )
        )
        
        # Parse the structured response
        raw_text = response.text
        logger.info(f"Raw Gemini response: {raw_text}")
        
        scan_data = ActionFigureScanResult.model_validate_json(raw_text)
        
        # Override barcode if manual barcode is submitted
        barcode = scan_data.barcode
        if manual_barcode and manual_barcode.strip():
            barcode = manual_barcode.strip()
            
        # Hybrid eBay sales retrieval flow
        ebay_client_id = os.getenv("EBAY_CLIENT_ID")
        ebay_client_secret = os.getenv("EBAY_CLIENT_SECRET")
        sales_list = []
        used_fallback = False
        
        # 1. Try Official eBay Browse API first if credentials are configured
        if ebay_client_id and ebay_client_secret:
            logger.info("eBay API credentials found in environment. Initiating official eBay Buy Browse API search...")
            token = get_ebay_access_token(ebay_client_id, ebay_client_secret)
            if token:
                search_q = f"{scan_data.manufacturer} {scan_data.series} {scan_data.productName} {scan_data.year}"
                logger.info(f"Querying eBay API with query: '{search_q}'")
                api_sales = search_ebay_active_listings(search_q, token)
                if api_sales:
                    logger.info(f"Successfully retrieved {len(api_sales)} active listings from eBay API.")
                    sales_list = api_sales
                else:
                    logger.warning("eBay API search returned 0 results. Falling back to Google Search grounding.")
            else:
                logger.warning("Failed to obtain eBay API OAuth token. Falling back to Google Search grounding.")
        
        # 2. Try Google Search Grounding if eBay API didn't return results
        if not sales_list:
            sales = scan_data.recentSales
            if scan_data.ebayMatchFound and sales:
                logger.info(f"Successfully retrieved {len(sales)} live eBay completed sales from Google Search grounding.")
                for s in sales:
                    sales_list.append({
                        "title": s.title,
                        "price": s.price,
                        "date": s.date if s.date else "Recent",
                        "shipping": s.shipping if s.shipping is not None else 0.0,
                        "url": s.url,
                        "ebayCondition": s.ebayCondition if s.ebayCondition else "Unknown"
                    })
        
        # 3. If both API and Grounding returned no results, generate deterministic mock listings
        if not sales_list:
            logger.info("No matching sales found via API or Grounding. Generating deterministic simulated completed sales...")
            sales_list = fetch_ebay_completed_prices(
                scan_data.productName,
                scan_data.series,
                scan_data.manufacturer,
                scan_data.year
            )
            used_fallback = True
        
        # Calculate Average and Median eBay price
        prices = [s["price"] for s in sales_list]
        ebay_avg = round(sum(prices) / len(prices), 2) if prices else 0.0
        ebay_med = calculate_median(prices)
        
        # Determine condition grading multiplier
        condition_multipliers = {
            "Mint/Near Mint": 1.0,
            "Fine/Very Fine": 0.85,
            "Good": 0.65,
            "Fair/Poor": 0.40
        }
        multiplier = condition_multipliers.get(scan_data.conditionGrade, 0.65)
        
        # Apply condition adjustments
        adjusted_value = round(ebay_avg * multiplier, 2)
        
        # Apply GameStop specific trade-in values (60% cash, 75% store credit of adjusted value)
        cash_offer = round(adjusted_value * 0.60, 2)
        credit_offer = round(adjusted_value * 0.75, 2)
        
        quote = TradeInQuote(
            productName=scan_data.productName,
            possibleAlternativeNames=scan_data.possibleAlternativeNames,
            series=scan_data.series,
            manufacturer=scan_data.manufacturer,
            year=scan_data.year,
            barcode=barcode,
            conditionGrade=scan_data.conditionGrade,
            conditionNotes=scan_data.conditionNotes,
            ebayMatchFound=not used_fallback,
            ebayAveragePrice=ebay_avg,
            ebayMedianPrice=ebay_med,
            conditionAdjustedValue=adjusted_value,
            cashOffer=cash_offer,
            creditOffer=credit_offer,
            recentSales=sales_list,
            isFallbackPricing=used_fallback
        )
        
        return quote
        
    except Exception as e:
        logger.error(f"Error during scan processing: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to identify product: {str(e)}")

# Serve frontend HTML page and static files
# Make sure static directory exists
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = "static/index.html"
    if os.path.exists(index_file):
        with open(index_file, "r") as f:
            return f.read()
    return """
    <html>
        <head><title>Setup Required</title></head>
        <body style="font-family:sans-serif; text-align:center; padding-top:100px;">
            <h1>Static Files Setup Required</h1>
            <p>Please place index.html inside the static/ folder.</p>
        </body>
    </html>
    """

if __name__ == "__main__":
    import uvicorn
    # Local server MUST listen on 127.0.0.1 for testing security compliance
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
