from playwright.sync_api import sync_playwright
import re


def clean_offer(text):
    if not text:
        return ""
    cleaned = re.sub(r'(?i)promoted', '', text).strip()
    cleaned = re.sub(r'\n+', ' ', cleaned).strip()
    return cleaned


def parse_price(text):
    match = re.search(r'₹(\d+)\s+for one', text)
    return int(match.group(1)) if match else None


def parse_rating(text):
    match = re.search(r'\b(\d\.\d)\b', text)
    return float(match.group(1)) if match else None


def parse_delivery_time(text):
    match = re.search(r'(\d+)\s+min', text)
    return int(match.group(1)) if match else None


def get_subzone_url(page, city: str, area: str) -> str:
    """Try to resolve area to a delivery_subzone URL, fallback to city."""
    if not area:
        return f"https://www.zomato.com/{city}"

    try:
        result = page.evaluate(f"""
            async () => {{
                const r = await fetch('https://www.zomato.com/webroutes/location/search?q={area}&city={city}');
                return await r.json();
            }}
        """)
        suggestions = result.get('locationSuggestions', [])
        if suggestions:
            subzone_id = suggestions[0].get('delivery_subzone_id')
            place_name = suggestions[0].get('entity_name', area)
            if subzone_id:
                return f"https://www.zomato.com/{city}/restaurants?delivery_subzone={subzone_id}&place_name={place_name}"
    except Exception as e:
        print(f"subzone lookup failed: {e}")

    return f"https://www.zomato.com/{city}"


def scrape_zomato(dish: str, location: str, progress_queue=None):
    def emit(msg):
        if progress_queue:
            progress_queue.put(msg)

    # parse city and area from location string
    if ',' in location:
        parts = location.split(',', 1)
        area = parts[0].strip()
        city = parts[1].strip().lower()
    else:
        area = ""
        city = location.strip().lower()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--disable-http2", "--no-sandbox"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"}
        )
        page = context.new_page()
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        emit({"type": "step", "msg": "Opening Zomato..."})
        page.goto(f"https://www.zomato.com/{city}", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        # resolve subzone if area provided
        if area:
            emit({"type": "step", "msg": f"Setting area: {area}..."})
            try:
                # find location input by class — more reliable than placeholder
                page.click("input.sc-gMcBNU", timeout=5000)
                page.wait_for_timeout(500)
                page.keyboard.press("Control+A")
                page.keyboard.type(area, delay=120)
                page.wait_for_timeout(2000)
                page.keyboard.press("ArrowDown")
                page.wait_for_timeout(500)
                page.keyboard.press("Enter")
                page.wait_for_timeout(3000)
            except Exception as e:
                emit({"type": "step", "msg": f"Area set failed: {str(e)[:50]}"})
        emit({"type": "step", "msg": "Searching on Zomato..."})
        try:
            page.click("input[placeholder='Search for restaurant, cuisine or a dish']", timeout=8000)
        except:
            emit({"type": "step", "msg": "Zomato search box not found"})
            browser.close()
            emit({"type": "done"})
            return []

        page.wait_for_timeout(500)
        page.keyboard.type(dish, delay=120)
        page.wait_for_timeout(2000)
        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(500)
        page.keyboard.press("Enter")
        page.wait_for_timeout(8000)

        emit({"type": "step", "msg": "Parsing Zomato results..."})

        page.evaluate("window.scrollTo(0, 500)")
        page.wait_for_timeout(1500)

        cards = page.evaluate("""
            () => {
                const results = [];
                const anchors = Array.from(document.querySelectorAll('a[href*="/order"]'));

                anchors.forEach(a => {
                    const text = (a.innerText || '').trim();
                    if (text.length < 10) return;

                    const hasRating = text.match(/\\d\\.\\d/);

                    if (!hasRating) {
                        results.push({ type: 'offer', text: text, href: a.href });
                    } else {
                        const allText = Array.from(a.querySelectorAll('*'))
                            .filter(el => el.children.length === 0)
                            .map(el => (el.innerText || '').trim())
                            .filter(t => t.match(/(\\d+%\\s*OFF|₹\\d+\\s*OFF|FLAT|flat)/i) && t.length < 40);
                        const embeddedOffer = allText.length > 0 ? allText[0] : '';
                        results.push({ type: 'card', text: text, href: a.href, embeddedOffer });
                    }
                });
                return results;
            }
        """)

        browser.close()

    results = []
    pending_offer = None

    for item in cards:
        if item['type'] == 'offer':
            pending_offer = clean_offer(item['text'])
        elif item['type'] == 'card':
            text = item['text']
            price = parse_price(text)
            if price is None:
                pending_offer = None
                continue

            lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
            name = lines[0] if lines else "Unknown"
            offer = pending_offer or clean_offer(item.get('embeddedOffer', ''))

            results.append({
                "dish_name": dish,
                "price": price,
                "is_veg": None,
                "in_stock": True,
                "restaurant": name,
                "restaurant_id": None,
                "restaurant_slug": item['href'],
                "locality": area or city,
                "delivery_time_mins": parse_delivery_time(text),
                "offer_header": offer,
                "offer_subheader": "",
                "platform": "zomato",
                "rating": parse_rating(text),
            })
            pending_offer = None

    emit({"type": "step", "msg": "Found dishes on Zomato"})
    emit({"type": "done"})
    return results[:10]


if __name__ == "__main__":
    results = scrape_zomato("burger", "Kazhakkoottam, trivandrum")
    for r in results:
        print(f"{r['restaurant']} | ₹{r['price']} | {r['delivery_time_mins']} min | offer: '{r['offer_header']}' | rating: {r['rating']}")