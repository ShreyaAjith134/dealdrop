from fastapi import FastAPI
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import asyncio
import json
import queue
import os
from concurrent.futures import ThreadPoolExecutor

from core.cache import get_cached, set_cache
from core.optimizer import optimize
from scraper.swiggy import scrape_swiggy
from scraper.zomato import scrape_zomato

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI()
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

executor = ThreadPoolExecutor(max_workers=1)


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


def send(event: str, data: dict) -> str:
    return f"data: {json.dumps({'event': event, **data})}\n\n"


def run_swiggy(dish, location, pq):
    try:
        return scrape_swiggy(dish, location, pq)
    except Exception as e:
        pq.put({"type": "step", "msg": f"Swiggy error: {str(e)}"})
        return []


def run_zomato(dish, location, pq):
    try:
        return scrape_zomato(dish, location, pq)
    except Exception as e:
        pq.put({"type": "step", "msg": f"Zomato error: {str(e)}"})
        return []


async def drain_queue(pq):
    msgs = []
    while not pq.empty():
        msgs.append(pq.get_nowait())
    return msgs


async def run_search_stream(dish: str, city: str, area: str = ""):
    location = f"{area}, {city}" if area else city
    yield send("step", {"msg": "Checking cache..."})
    await asyncio.sleep(0.2)

    cached = get_cached(dish, location)
    if cached:
        yield send("step", {"msg": "Cache hit — returning saved results"})
        await asyncio.sleep(0.3)
        yield send("result", {"data": cached})
        return

    loop = asyncio.get_event_loop()

    # ── SWIGGY ──
    yield send("step", {"msg": "Running Swiggy..."})
    await asyncio.sleep(0.2)

    swiggy_pq = queue.Queue()
    swiggy_future = loop.run_in_executor(executor, run_swiggy, dish, location, swiggy_pq)

    while not swiggy_future.done():
        await asyncio.sleep(0.15)
        for msg in await drain_queue(swiggy_pq):
            t = msg.get("type")
            if t == "step":
                yield send("step", {"msg": msg["msg"]})
            elif t == "restaurant_progress":
                yield send("restaurant_progress", {
                    "current": msg["current"],
                    "total": msg["total"],
                    "name": msg["name"]
                })

    # drain any remaining
    for msg in await drain_queue(swiggy_pq):
        if msg.get("type") == "step":
            yield send("step", {"msg": msg["msg"]})

    try:
        swiggy_results = swiggy_future.result() or []
    except Exception as e:
        swiggy_results = []
        yield send("step", {"msg": f"Swiggy failed: {str(e)}", "status": "error"})

    yield send("step", {"msg": f"Swiggy done — {len(swiggy_results)} results"})
    await asyncio.sleep(0.2)

    # ── ZOMATO ──
    yield send("step", {"msg": "Running Zomato..."})
    await asyncio.sleep(0.2)

    zomato_pq = queue.Queue()
    zomato_future = loop.run_in_executor(executor, run_zomato, dish, location, zomato_pq)

    while not zomato_future.done():
        await asyncio.sleep(0.15)
        for msg in await drain_queue(zomato_pq):
            if msg.get("type") == "step":
                yield send("step", {"msg": msg["msg"]})

    for msg in await drain_queue(zomato_pq):
        if msg.get("type") == "step":
            yield send("step", {"msg": msg["msg"]})

    try:
        zomato_results = zomato_future.result() or []
    except Exception as e:
        zomato_results = []
        yield send("step", {"msg": f"Zomato failed: {str(e)}", "status": "error"})

    yield send("step", {"msg": f"Zomato done — {len(zomato_results)} results"})
    await asyncio.sleep(0.2)

    # ── MERGE + OPTIMIZE ──
    for r in swiggy_results:
        r.setdefault("platform", "swiggy")

    all_results = swiggy_results + zomato_results

    if not all_results:
        yield send("step", {"msg": "No results found", "status": "error"})
        return

    yield send("step", {"msg": "Optimizing across both platforms..."})
    await asyncio.sleep(0.2)

    ranked = optimize(all_results)
    set_cache(dish, location, ranked)

    yield send("step", {"msg": "Done!"})
    await asyncio.sleep(0.2)
    yield send("result", {"data": ranked})


@app.get("/search")
async def search(dish: str, city: str, area: str = ""):
    return StreamingResponse(
        run_search_stream(dish, city, area),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )