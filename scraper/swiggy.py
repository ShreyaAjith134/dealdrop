from playwright.sync_api import sync_playwright
import json

def parse_results(data):
    results = []
    cards = data["data"]["data"]["cards"]
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
            offer_header = offer_info.get("header", "")
            offer_subheader = offer_info.get("subHeader", "")
            offer_text = f"{offer_header} {offer_subheader}".strip() if offer_header else None

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
                "offer": offer_text,
                "bank_offers": [],
            })
    return results


def parse_bank_offers(data):
    offers = []
    try:
        cards = data["data"]["cards"]
        for card in cards:
            inner = card.get("card", {}).get("card", {})
            if "OfferWidget" in inner.get("@type", "") or "offersV2" in str(inner):
                for o in inner.get("offers", []):
                    offers.append({
                        "header": o.get("header"),
                        "description": o.get("description"),
                        "coupon_code": o.get("couponCode"),
                        "min_order": o.get("minOrderValue", 0) / 100,
                        "discount_type": o.get("offerType"),
                    })
    except Exception as e:
        print(f"offer parse error: {e}")
    return offers


def scrape_swiggy(dish: str, location: str):
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        search_captured = []
        menu_captured = {}

        def handle_response(response):
            if "search" in response.url and "query" in response.url:
                try:
                    data = response.json()
                    search_captured.append({"url": response.url, "data": data})
                    print(f"captured search: {response.url}")
                except:
                    pass
            if "menu/pl" in response.url:
                try:
                    data = response.json()
                    rid = None
                    for part in response.url.split("&"):
                        if "restaurantId" in part:
                            rid = part.split("=")[-1]
                    if rid:
                        menu_captured[rid] = data
                        print(f"captured menu for restaurant {rid}")
                except:
                    pass

        page.on("response", handle_response)

        print("loading swiggy...")
        page.goto("https://www.swiggy.com")
        page.wait_for_timeout(3000)

        page.click("input[placeholder='Enter your delivery location']")
        page.wait_for_timeout(1000)
        page.keyboard.type(location, delay=100)
        page.wait_for_timeout(2000)

        page.wait_for_selector("div._2BgUI", timeout=5000)
        page.locator("div._2BgUI").first.click()
        page.wait_for_timeout(2000)

        page.goto("https://www.swiggy.com/search")
        page.wait_for_timeout(2000)
        page.wait_for_selector("input[placeholder*='Search']", timeout=5000)
        page.click("input[placeholder*='Search']")
        page.keyboard.type(dish, delay=100)
        page.wait_for_timeout(3000)
        page.keyboard.press("Enter")
        page.wait_for_timeout(4000)

        if not search_captured:
            print("no search results captured")
            browser.close()
            return []

        results = parse_results(search_captured[0])
        print(f"\nfound {len(results)} dishes — fetching bank offers for each restaurant...")

        seen = set()
        for r in results:
            rid = r["restaurant_id"]
            slug = r["restaurant_slug"]
            if rid and slug and rid not in seen:
                seen.add(rid)
                url = f"https://www.swiggy.com/restaurants/{slug}/{rid}"
                print(f"visiting: {url}")
                page.goto(url)
                page.wait_for_timeout(3000)

        browser.close()

    for r in results:
        rid = r["restaurant_id"]
        if rid and rid in menu_captured:
            r["bank_offers"] = parse_bank_offers(menu_captured[rid])

    print(f"\nfinal results:\n")
    for r in results:
        offer_str = f" | offer: {r['offer']}" if r['offer'] else ""
        bank_str = f" | bank offers: {len(r['bank_offers'])}" if r['bank_offers'] else ""
        print(f"  {r['dish_name']} | ₹{r['price']} | {r['restaurant']} | {r['delivery_time_mins']} mins{offer_str}{bank_str}")
        for b in r['bank_offers']:
            print(f"      → {b['header']} | {b['description']} | code: {b['coupon_code']} | min: ₹{b['min_order']}")

    with open("swiggy_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print("\nsaved to swiggy_results.json")
    return results


if __name__ == "__main__":
    scrape_swiggy("taro boba", "Bangalore")