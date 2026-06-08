from playwright.sync_api import sync_playwright


def parse_results(data):
    results = []
    try:
        cards = data["data"]["cards"]
    except KeyError:
        try:
            cards = data["data"]["data"]["cards"]
        except KeyError:
            return []

    for card in cards:
        grouped = card.get("groupedCard", {})
        dish_cards = grouped.get("cardGroupMap", {}).get("DISH", {}).get("cards", [])
        for item in dish_cards:
            info = item.get("card", {}).get("card", {})
            if info.get("@type") != "type.googleapis.com/swiggy.presentation.food.v2.Dish":
                continue
            dish = info.get("info", {})
            restaurant = info.get("restaurant", {}).get("info", {})
            offer_info = restaurant.get("aggregatedDiscountInfoV3", {})
            slugs = restaurant.get("slugs", {})

            results.append({
                "dish_name": dish.get("name"),
                "price": dish.get("price", 0) / 100,
                "is_veg": dish.get("isVeg") == 1,
                "in_stock": dish.get("inStock") == 1,
                "restaurant": restaurant.get("name"),
                "restaurant_id": restaurant.get("id"),
                "restaurant_slug": slugs.get("restaurant"),
                "locality": restaurant.get("locality"),
                "delivery_time_mins": restaurant.get("sla", {}).get("deliveryTime"),
                "offer_header": offer_info.get("header", ""),
                "offer_subheader": offer_info.get("subHeader", ""),
            })

    return results[:10]


def scrape_swiggy(dish: str, location: str, progress_queue=None):
    def emit(msg):
        if progress_queue:
            progress_queue.put(msg)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        search_captured = []

        def handle_response(response):
            if "search/v3" in response.url:
                try:
                    data = response.json()
                    search_captured.append(data)
                except:
                    pass

        page.on("response", handle_response)

        emit({"type": "step", "msg": "Opening Swiggy..."})
        page.goto("https://www.swiggy.com")
        page.wait_for_timeout(3000)

        emit({"type": "step", "msg": "Setting location..."})
        page.click("input[placeholder='Enter your delivery location']")
        page.wait_for_timeout(1000)
        page.keyboard.type(location, delay=100)
        page.wait_for_timeout(2000)
        page.wait_for_selector("div._2BgUI", timeout=15000)
        page.locator("div._2BgUI").first.click()
        page.wait_for_timeout(2000)

        emit({"type": "step", "msg": f"Searching for {dish}..."})
        page.goto("https://www.swiggy.com/search")
        page.wait_for_timeout(2000)
        page.wait_for_selector("input[placeholder*='Search']", timeout=5000)
        page.click("input[placeholder*='Search']")
        page.keyboard.type(dish, delay=100)
        page.wait_for_timeout(3000)
        page.keyboard.press("Enter")
        page.wait_for_timeout(4000)

        if not search_captured:
            browser.close()
            emit({"type": "error", "msg": "No results found"})
            return []

        results = parse_results(search_captured[0])
        emit({"type": "step", "msg": "Found dishes"})

        # visit restaurants for any future enrichment, emit per-restaurant progress
        seen = set()
        restaurants = [(r["restaurant_id"], r["restaurant_slug"], r["restaurant"]) 
                       for r in results if r["restaurant_id"] and r["restaurant_slug"]]
        unique = [(rid, slug, name) for rid, slug, name in restaurants if rid not in seen and not seen.add(rid)]
        total = len(unique)

        for i, (rid, slug, name) in enumerate(unique):
            emit({"type": "restaurant_progress", "current": i + 1, "total": total, "name": name})
            url = f"https://www.swiggy.com/restaurants/{slug}/{rid}"
            try:
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(2000)
            except:
                pass

        browser.close()

    emit({"type": "done"})
    return results