from fastapi import FastAPI
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import asyncio
import json
import queue
import os

from core.cache import get_cached, set_cache
from core.optimizer import optimize
from scraper.swiggy import scrape_swiggy

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI()
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


def send(event: str, data: dict) -> str:
    return f"data: {json.dumps({'event': event, **data})}\n\n"


async def run_search_stream(dish: str, location: str):
    yield send("step", {"msg": "Checking cache..."})
    await asyncio.sleep(0.2)

    cached = get_cached(dish, location)
    if cached:
        yield send("step", {"msg": "Cache hit — returning saved results"})
        await asyncio.sleep(0.3)
        yield send("result", {"data": cached})
        return

    yield send("step", {"msg": "No cache — starting scraper"})
    await asyncio.sleep(0.2)

    pq = queue.Queue()
    loop = asyncio.get_event_loop()

    # run scraper in thread
    future = loop.run_in_executor(None, scrape_swiggy, dish, location, pq)

    results = None
    while True:
        await asyncio.sleep(0.1)

        # drain queue
        while not pq.empty():
            msg = pq.get_nowait()
            t = msg.get("type")

            if t == "step":
                yield send("step", {"msg": msg["msg"]})

            elif t == "restaurant_progress":
                yield send("restaurant_progress", {
                    "current": msg["current"],
                    "total": msg["total"],
                    "name": msg["name"]
                })

            elif t == "error":
                yield send("step", {"msg": msg["msg"], "status": "error"})

            elif t == "done":
                pass

        if future.done():
            try:
                results = future.result()
            except Exception as e:
                yield send("step", {"msg": f"Error: {str(e)}", "status": "error"})
                return
            break

    if not results:
        yield send("step", {"msg": "No results found", "status": "error"})
        return

    yield send("step", {"msg": "Optimizing deals..."})
    await asyncio.sleep(0.2)

    ranked = optimize(results)
    set_cache(dish, location, ranked)

    yield send("step", {"msg": "Done!"})
    await asyncio.sleep(0.2)
    yield send("result", {"data": ranked})


@app.get("/search")
async def search(dish: str, location: str):
    return StreamingResponse(
        run_search_stream(dish, location),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )