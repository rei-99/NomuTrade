"""CDP shot: enlarged confirm modal after clicking BUY."""
import asyncio
import base64
import json
import urllib.request

import websockets

DEBUG = "http://localhost:9222"
APP = "http://localhost:5173"


def dev_login() -> str:
    req = urllib.request.Request(
        "http://localhost:8000/api/v1/auth/dev-login",
        data=json.dumps({"email": "trader@demo.nomura"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req))["token"]


async def main() -> None:
    token = dev_login()
    targets = json.load(urllib.request.urlopen(f"{DEBUG}/json"))
    page = next(t for t in targets if t["type"] == "page")
    mid = 0

    async def send(ws, method, params=None):
        nonlocal mid
        mid += 1
        await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("id") == mid:
                return msg.get("result", {})

    async def js(ws, expr):
        r = await send(ws, "Runtime.evaluate", {"expression": expr, "returnByValue": True})
        return r.get("result", {}).get("value")

    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=50 * 1024 * 1024) as ws:
        await send(ws, "Page.enable")
        await send(ws, "Emulation.setDeviceMetricsOverride",
                   {"width": 1680, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
        await send(ws, "Page.navigate", {"url": f"{APP}/login"})
        await asyncio.sleep(2.5)
        await js(ws, f"localStorage.setItem('stp_token','{token}'); 'ok'")
        await send(ws, "Page.navigate", {"url": f"{APP}/?symbol=TSLA"})
        await asyncio.sleep(6)
        print("click:", await js(ws,
            "var b=document.querySelector('.trade-buy'); b?(b.click(),'clicked'):'missing'"))
        await asyncio.sleep(1.2)
        print("confirm-modal present:", await js(ws, "!!document.querySelector('.confirm-modal')"))
        s = await send(ws, "Page.captureScreenshot", {"format": "png"})
        with open("shot_bigmodal.png", "wb") as fh:
            fh.write(base64.b64decode(s["data"]))
        print("saved")


asyncio.run(main())
