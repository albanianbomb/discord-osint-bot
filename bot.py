import discord
from discord.ext import commands
from datetime import datetime, timedelta
import aiohttp
import asyncio
import uuid
import json
import io
import traceback
import requests
import os

# ---------------- CONFIG ----------------
API_KEY = os.environ.get("OSINT_API_KEY", "fallback_api_key")
SECRET_MASTER_KEY = os.environ.get("SECRET_MASTER_KEY", "fallback_secret")
MAX_SEARCHES_PER_DAY = 10
FIREBASE_URL = os.environ.get("FIREBASE_URL")
if not FIREBASE_URL:
    raise ValueError("⚠️ Keine FIREBASE_URL gesetzt! Bitte in den Environment Variables hinterlegen.")
USER_AGENT_REQUIRED = {"email-osint"}
ALL_SERVICES = [
    "discord", "discord-stalker", "email-osint", "npd",
    "domain", "github-osint", "username", "dns-resolver"
]
PREFIX = "*"

# ---------------- HWID ----------------
def get_hwid():
    return str(uuid.getnode())
HWID = get_hwid()

# ---------------- FIREBASE ----------------
def firebase_get(path):
    try:
        r = requests.get(f"{FIREBASE_URL}/{path}.json", timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"[DEBUG] Firebase GET {path} Exception: {e}")
    return None

def firebase_patch(path, data):
    try:
        r = requests.patch(f"{FIREBASE_URL}/{path}.json", json=data, timeout=10)
        if r.status_code != 200:
            print(f"[DEBUG] Firebase PATCH {path} Status {r.status_code}")
    except Exception as e:
        print(f"[DEBUG] Firebase PATCH {path} Exception: {e}")

def get_server_time():
    try:
        r = requests.get("http://worldtimeapi.org/api/ip", timeout=8)
        if r.status_code == 200:
            return datetime.fromisoformat(r.json()["utc_datetime"].replace("Z", "+00:00"))
    except Exception as e:
        print(f"[DEBUG] get_server_time exception: {e}")
    return datetime.utcnow()

# ---------------- KEY SYSTEM ----------------
def set_user_key(discord_id, input_key):
    try:
        users = firebase_get("users") or {}
        now = get_server_time()
        users[str(discord_id)] = {
            "key": input_key,
            "hwid": HWID,
            "search_count": 0,
            "last_reset": now.isoformat(),
            "first_use": now.isoformat()
        }
        firebase_patch("users", users)
        return True
    except Exception as e:
        print(f"[DEBUG] set_user_key exception: {e}")
        traceback.print_exc()
        return False

def get_user_key(discord_id):
    users = firebase_get("users") or {}
    entry = users.get(str(discord_id))
    if entry:
        return entry.get("key")
    return None

def validate_key_user(input_key, discord_id):
    try:
        if input_key == SECRET_MASTER_KEY:
            return True, "Master key: unlimited searches"

        keys = firebase_get("keys") or {}
        if input_key not in keys:
            return False, "Key existiert nicht"

        key_entry = keys[input_key]
        if not isinstance(key_entry, dict):
            key_entry = {"value": key_entry}

        bound_id = key_entry.get("discord_id")
        if not bound_id:
            key_entry["discord_id"] = str(discord_id)
            key_entry["bound_at"] = get_server_time().isoformat()
            firebase_patch(f"keys/{input_key}", key_entry)
        elif bound_id != str(discord_id):
            return False, "Key gehört jemand anderem"

        users = firebase_get("users") or {}
        user_entry = users.get(str(discord_id))
        now = get_server_time()

        if not user_entry:
            users[str(discord_id)] = {
                "key": input_key,
                "hwid": HWID,
                "search_count": 0,
                "last_reset": now.isoformat(),
                "first_use": now.isoformat()
            }
            firebase_patch("users", users)
        else:
            if user_entry.get("hwid") != HWID:
                return False, "HWID mismatch"
            user_entry["key"] = input_key
            firebase_patch(f"users/{discord_id}", user_entry)

            last_reset = datetime.fromisoformat(user_entry.get("last_reset"))
            if now - last_reset >= timedelta(hours=24):
                user_entry["search_count"] = 0
                user_entry["last_reset"] = now.isoformat()
                firebase_patch(f"users/{discord_id}", user_entry)

            if user_entry.get("search_count", 0) >= MAX_SEARCHES_PER_DAY:
                return False, "Daily limit reached"

        return True, "Key valid"
    except Exception as e:
        print(f"[DEBUG] validate_key_user Exception: {e}")
        traceback.print_exc()
        return False, "Fehler bei der Key-Validierung"

def increment_search_count_user(discord_id):
    try:
        users = firebase_get("users") or {}
        user_entry = users.get(str(discord_id))
        if not user_entry:
            return 0
        current = user_entry.get("search_count", 0) + 1
        firebase_patch(f"users/{discord_id}", {"search_count": current})
        return current
    except Exception as e:
        print(f"[DEBUG] increment_search_count_user Exception: {e}")
        traceback.print_exc()
        return 0

# ---------------- ASYNC OSINT API ----------------
async def perform_service_search(session, service, query):
    url = f"https://www.osintcat.net/api/{service}"
    headers = {}
    params = {"query": query, "id": API_KEY}

    if service in USER_AGENT_REQUIRED:
        headers["User-Agent"] = "Purpose: your-purpose"

    try:
        async with session.get(url, params=params, headers=headers, timeout=20) as r:
            data = await r.json()
            if isinstance(data, dict):
                rdata = data.get("results", [data])
                if isinstance(rdata, dict):
                    rdata = [rdata]
                return rdata
            elif isinstance(data, list):
                return data
    except Exception as e:
        print(f"[DEBUG] Service '{service}' failed: {e}")
    return []

# ---------------- DISCORD BOT ----------------
intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.message_content = True
bot = commands.Bot(command_prefix="*", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Bot online als {bot.user}")
    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="https://2sint.shop"
        )
    )

# ---------------- Commands ----------------
@bot.command(name="setkey")
async def setkey(ctx, key: str):
    valid, msg = validate_key_user(key, ctx.author.id)
    if valid:
        set_user_key(ctx.author.id, key)
        await ctx.send(f"✅ {ctx.author.mention} Key wurde gesetzt und validiert! {msg}")
    else:
        await ctx.send(f"❌ {ctx.author.mention} Key ungültig: {msg}")

@bot.command(name="searches")
async def searches(ctx, *, arg=None):
    if arg and arg.lower() == "left":
        users = firebase_get("users") or {}
        user_data = users.get(str(ctx.author.id))
        searches_left = 0
        if user_data:
            searches_left = MAX_SEARCHES_PER_DAY - user_data.get("search_count", 0)
        await ctx.send(f"🔢 {ctx.author.mention} Du hast noch **{searches_left} searches left**.")
    else:
        await ctx.send("❌ Nutze: `*searches left`")

@bot.command(name="osint")
async def osint(ctx, *, query: str):
    user_key = get_user_key(ctx.author.id)
    if not user_key:
        await ctx.send(f"❌ {ctx.author.mention} Du hast noch keinen Key gesetzt. Nutze `*setkey <dein_key>` zuerst.")
        return

    valid, msg = validate_key_user(user_key, ctx.author.id)
    if not valid:
        await ctx.send(f"❌ {ctx.author.mention} Key invalid: {msg}")
        return

    searches_done = increment_search_count_user(ctx.author.id)
    searches_left = MAX_SEARCHES_PER_DAY - searches_done

    status_msg = await ctx.send(f"🔎 {ctx.author.mention} Suche für `{query}` gestartet... 0/{len(ALL_SERVICES)} Services fertig.")

    final_results = {}
    finished_count = 0

    async def fetch_service(session, service):
        nonlocal finished_count
        results = await perform_service_search(session, service, query)
        final_results[service] = results
        finished_count += 1
        result_status = f"✅ {len(results)} results" if results else "❌ Nothing found"
        await status_msg.edit(content=f"🔎 {ctx.author.mention} `{service}` fertig: {result_status} | {finished_count}/{len(ALL_SERVICES)} Services fertig.")

    async with aiohttp.ClientSession() as session:
        tasks = [asyncio.create_task(fetch_service(session, svc)) for svc in ALL_SERVICES]
        await asyncio.gather(*tasks)

    dm_text = ""
    for svc, res in final_results.items():
        dm_text += f"{'✅' if res else '❌'} {svc}: {len(res) if res else 0} results\n"

    try:
        user = ctx.author
        await user.send(f"OSINT search summary for `{query}`:\n{dm_text}")

        text = json.dumps(final_results, ensure_ascii=False, indent=2)
        if len(text) > 1900:
            file = discord.File(io.BytesIO(text.encode('utf-8')), filename="osint_results.json")
            await user.send("✅ Full results:", file=file)
        else:
            await user.send(f"✅ Full results:\n```\n{text}\n```")

        await ctx.send(f"✅ {ctx.author.mention} DM wurde gesendet! Du hast noch **{searches_left} searches left**.")
    except discord.Forbidden:
        await ctx.send(f"❌ {ctx.author.mention} Konnte keine DM senden. Bitte erlaube DMs.\nDu hast noch **{searches_left} searches left**.")

# ---------------- RUN ----------------
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("⚠️ Kein DISCORD_TOKEN gefunden. Bitte als Environment Variable setzen.")
bot.run(TOKEN)
