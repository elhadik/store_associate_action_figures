# GameStop StoreAssociate: Collectibles & Graded Card Appraiser

A modern, high-fidelity AI-powered dashboard designed for GameStop store associates to scan, grade, and instantly price trade-in collectibles (including vintage action figures and graded trading cards) without requiring specialized appraiser knowledge.

## Features

- **Multimodal Identification:** Upload collectible images or snap photos live using desktop/mobile cameras. Gemini automatically recognizes the item name, series, manufacturer, and release/print year.
- **Graded Card & Packaging Analysis:** Analyzes cardback creases, bubble dents, card cracks, graded slabs (e.g. PSA certificate numbers), or figure damage to classify items into collector condition tiers (Mint, Fine, Good, Poor).
- **Grounded Completed Sales Retrieval:** Leverages the **Google Search Grounding Tool** in Gemini to perform real-time searches for completed eBay auction listings, returning actual sold prices, listing titles, shipping costs, listing conditions, and clickable source links.
- **Rich Structured Output Details:**
  - **Possible Alternative Names:** Suggests 2-3 other common search queries or alternative names.
  - **eBay Match Found Flag:** Indicates whether real matched listings were found on eBay.
  - **Median and Average Prices:** Computes both median and average completed sale prices.
  - **Source URLs:** Displays titles as clickable hyperlinks leading directly to the eBay auction page.
- **Strict Verification Check:** If no eBay matches exist, the system returns `NO MATCH FOUND` and displays a warning to the associate rather than inventing mock prices.
- **Deterministic Trade Payouts:** Calculates cash and store credit offers based on condition-adjusted valuation rules (60% cash, 75% credit of final value).
- **Camera Viewfinder Integration:** Native WebRTC webcam integration allows quick mobile-first package scanning.

---

## Getting Started

### 1. Prerequisites

Make sure you have `uv` installed. If not, install it using:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Environment Setup

Create a `.env` file in the root directory:
```env
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-flash-latest
PORT=8000
GOOGLE_GENAI_USE_VERTEXAI=false
```

### 3. Run Locally

Start the FastAPI backend with:
```bash
uv run python main.py
```

The application will launch on:
[http://127.0.0.1:8000](http://127.0.0.1:8000)

> **Note on Security:** As per security compliance, the server listens exclusively on `127.0.0.1` during testing and development.
