import re


def parse_offer(header, subheader):
    header = (header or "").strip()
    subheader = (subheader or "").strip()

    # X% OFF UPTO ₹Y
    if re.match(r"\d+%\s*OFF", header) and "UPTO" in subheader:
        pct_match = re.search(r"(\d+)%", header)
        cap_match = re.search(r"[\d]+", subheader.replace(",", ""))
        if pct_match and cap_match:
            return ("pct_with_cap", int(pct_match.group(1)) / 100, float(cap_match.group()))

    return ("none", 0, 0)


def effective_price(dish_price, offer):
    kind, *params = offer
    if kind == "pct_with_cap":
        pct, cap = params
        discount = min(dish_price * pct, cap)
        return round(dish_price - discount, 2)
    return dish_price


def optimize(results: list) -> list:
    ranked = []
    for r in results:
        if not r.get("in_stock", True):
            continue
        offer = parse_offer(r.get("offer_header", ""), r.get("offer_subheader", ""))
        ep = effective_price(r["price"], offer)
        savings = round(r["price"] - ep, 2)

        kind = offer[0]
        if kind == "pct_with_cap":
            pct = int(offer[1] * 100)
            cap = int(offer[2])
            offer_label = f"{pct}% off, upto ₹{cap}"
        else:
            h = r.get("offer_header", "")
            s = r.get("offer_subheader", "")
            offer_label = f"{h} {s}".strip() or None

        ranked.append({
            **r,
            "effective_price": ep,
            "savings": savings,
            "offer_label": offer_label,
        })

    ranked.sort(key=lambda x: x["effective_price"])
    return ranked