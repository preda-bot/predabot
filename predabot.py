import os
import math
import discord
from discord import app_commands
from datetime import datetime, timedelta

# ---------------------------------------
# Constants for Leaderboard Calculations
# ---------------------------------------
TIME_WEIGHT_LAMBDA = 1.0       # Controls the decay of the time bonus over the market's lifetime.
INACTIVITY_THRESHOLD_DAYS = 30 # No decay if the last submission was within 30 days.
DECAY_MU = 0.01                # Decay rate per day beyond the threshold.
MIN_DECAY_FACTOR = 0.5         # Minimum decay factor for an agent's score.

# ---------------------------------------
# Testing Guild ID (your test server)
# ---------------------------------------
TEST_GUILD_ID = 1349495814816403478

# ---------------------------------------
# Create a basic Discord client and set up the CommandTree.
# ---------------------------------------
intents = discord.Intents.default()
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# ---------------------------------------
# Google Sheets Manager (Real Implementation)
# ---------------------------------------
import gspread_asyncio
from oauth2client.service_account import ServiceAccountCredentials

GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")
GOOGLE_SHEET_ID    = os.getenv("GOOGLE_SHEET_ID")

def get_creds():
    scope = ["https://www.googleapis.com/auth/spreadsheets"]
    return ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_CREDS_JSON, scope)

AGM = gspread_asyncio.AsyncioGspreadClientManager(get_creds)

async def get_sheet(tab_name: str):
    client = await AGM.authorize()
    ss     = await client.open_by_key(GOOGLE_SHEET_ID)
    return await ss.worksheet(tab_name)

class SheetManager:
    def __init__(self):
        pass

    # --- Agent functions ---
    async def get_agent(self, user_id: str):
        ws = await get_sheet("agents")
        try:
            cell = await ws.find(user_id)
            row  = await ws.row_values(cell.row)
            return {
                "agent_name":      row[1],
                "training_source": row[2],
                "bio":             row[3] or None
            }
        except Exception:
            return None

    async def register_agent(self, user_id: str, agent_name: str, training_source: str, bio: str = None):
        ws = await get_sheet("agents")
        await ws.append_row([user_id, agent_name, training_source, bio or ""])

    # --- Prediction functions ---
    async def submit_prediction(self, user_id: str, market_id: str, probability: int):
        ws   = await get_sheet("predictions")
        rows = await ws.get_all_values()
        for r in rows[1:]:
            if r[0] == user_id and r[1] == market_id:
                return False
        ts = datetime.utcnow().isoformat()
        await ws.append_row([user_id, market_id, str(probability), ts, "FALSE"])
        return True

    # --- Market functions ---
    async def get_market(self, market_id: str):
        ws = await get_sheet("markets")
        try:
            cell = await ws.find(market_id)
            row  = await ws.row_values(cell.row)
            return {
                "title":              row[1],
                "source":             row[2],
                "closes":             row[3],
                "close_time":         datetime.fromisoformat(row[3]),
                "open_time":          datetime.fromisoformat(row[4]),
                "active":             row[5] == "TRUE",
                "resolved":           row[6] == "TRUE",
                "outcome":            row[7] or None,
                "resolution_source":  row[8] or None,
                "resolution_note":    row[9] or None
            }
        except Exception:
            return None

    async def get_active_markets(self):
        ws   = await get_sheet("markets")
        rows = await ws.get_all_values()
        out  = {}
        for r in rows[1:]:
            if r[5] == "TRUE":
                out[r[0]] = {"title": r[1], "closes": r[3], "source": r[2]}
        return out

    async def add_market(self, market_id: str, title: str, source_link: str, close_date: str):
        ws = await get_sheet("markets")
        ot = datetime.utcnow().isoformat()
        await ws.append_row([market_id, title, source_link, close_date, ot, "TRUE", "FALSE", "", "", ""])

    # --- Leaderboard functions ---
    async def fetch_leaderboard(self):
        ws   = await get_sheet("leaderboard")
        rows = await ws.get_all_values()
        data = {}
        for r in rows[1:]:
            data[r[0]] = {
                "agent_name":      r[1],
                "score":           float(r[2]),
                "submissions":     int(r[3]),
                "last_submission": datetime.fromisoformat(r[4])
            }
        return data

    async def update_leaderboard_sheet(self, lb: dict):
        ws = await get_sheet("leaderboard")
        await ws.clear()
        await ws.append_row(["user_id","agent_name","score","submissions","last_submission"])
        for uid, d in lb.items():
            await ws.append_row([
                uid,
                d["agent_name"],
                str(d["score"]),
                str(d["submissions"]),
                d["last_submission"].isoformat()
            ])

# Global instance.
sheets_manager = SheetManager()

# ---------------------------------------
# /register Command
# ---------------------------------------
@tree.command(name="register", description="Register your AI agent")
async def register(interaction: discord.Interaction, agent_name: str, training_source: str, bio: str = None):
    user_id = str(interaction.user.id)
    if await sheets_manager.get_agent(user_id):
        await interaction.response.send_message("You have already registered an agent. Contact admin for edits.")
        return
    if not training_source.startswith("http"):
        await interaction.response.send_message("Please specify a valid training source or dataset.")
        return
    await sheets_manager.register_agent(user_id, agent_name, training_source, bio)
    await interaction.response.send_message(f"{agent_name} is registered as your agent in the prediction arena.")

# ---------------------------------------
# /submit Command
# ---------------------------------------
@tree.command(name="submit", description="Submit a prediction for a market")
async def submit(interaction: discord.Interaction, market_id: str, probability: int):
    await interaction.response.defer(ephemeral=False)
    user_id = str(interaction.user.id)
    agent   = await sheets_manager.get_agent(user_id)
    if not agent:
        await interaction.followup.send("You must register your agent first using /register.")
        return
    if probability < 0 or probability > 100:
        await interaction.followup.send("Enter a percentage between 0 and 100.")
        return
    market = await sheets_manager.get_market(market_id)
    if market is None or not market.get("active", False):
        await interaction.followup.send("This market is closed or does not exist.")
        return
    success = await sheets_manager.submit_prediction(user_id, market_id, probability)
    if success:
        await interaction.followup.send(f"{agent['agent_name']} locks in {probability}%. The arena sharpens.")
    else:
        await interaction.followup.send("You’ve already submitted a prediction for this market.")

# ---------------------------------------
# /markets Command
# ---------------------------------------
@tree.command(name="markets", description="List all active markets")
async def markets(interaction: discord.Interaction):
    active_markets = await sheets_manager.get_active_markets()
    if not active_markets:
        await interaction.response.send_message("There are no active markets right now. Check back later.")
        return
    response_lines = []
    for m_id, market in active_markets.items():
        response_lines.append(f"**market-id**: {m_id}")
        response_lines.append(f"  **title**: {market['title']}")
        response_lines.append(f"  **closes**: {market['closes']}")
        response_lines.append(f"  **source**: {market['source']}\n")
    await interaction.response.send_message("\n".join(response_lines))

# ---------------------------------------
# /addmarket Command (Admin Only)
# ---------------------------------------
@tree.command(name="addmarket", description="Add a new market (Admin only)")
async def addmarket(interaction: discord.Interaction, market_id: str, title: str, source_link: str, close_date: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You don’t have permission to use this command.", ephemeral=True)
        return
    if await sheets_manager.get_market(market_id):
        await interaction.response.send_message("A market with this ID already exists. Choose a unique identifier.")
        return
    await sheets_manager.add_market(market_id, title, source_link, close_date)
    await interaction.response.send_message(f"Market **{market_id}** added successfully.")

# ---------------------------------------
# /leaderboard Command
# ---------------------------------------
@tree.command(name="leaderboard", description="Display the top 10 agents")
async def leaderboard(interaction: discord.Interaction):
    lb_data = await sheets_manager.fetch_leaderboard()
    if not lb_data:
        await interaction.response.send_message("No submissions yet. Be the first to make your mark.")
        return
    now = datetime.utcnow()
    entries = []
    for uid, d in lb_data.items():
        raw = d["score"]
        delta = (now - d["last_submission"]).days
        if delta < INACTIVITY_THRESHOLD_DAYS:
            decay = 1.0
        else:
            decay = math.exp(-DECAY_MU * (delta - INACTIVITY_THRESHOLD_DAYS))
            decay = max(decay, MIN_DECAY_FACTOR)
        final = raw * decay
        entries.append((d["agent_name"], raw, final, d["submissions"]))
    entries.sort(key=lambda e: e[2], reverse=True)
    lines = ["**Leaderboard (Top 10 Agents):**"]
    for i, (name, raw, final, subs) in enumerate(entries[:10], start=1):
        lines.append(f"{i}. {name} — Final Score: {final:.2f} (Raw: {raw:.2f}, Submissions: {subs})")
    await interaction.response.send_message("\n".join(lines))

# ---------------------------------------
# /agent Command
# ---------------------------------------
@tree.command(name="agent", description="Show stats for a registered agent")
async def agent(interaction: discord.Interaction, agent_name: str = None):
    user_id = str(interaction.user.id)
    if agent_name is None:
        data = await sheets_manager.get_agent(user_id)
        if not data:
            await interaction.response.send_message("You don’t have an agent registered. Use /register first or specify another agent’s name.")
            return
    else:
        ws   = await get_sheet("agents")
        rows = await ws.get_all_values()
        data = None
        for r in rows[1:]:
            if r[1].lower() == agent_name.lower():
                data = {"agent_name": r[1], "bio": r[3] or "No bio provided."}
                break
        if not data:
            await interaction.response.send_message("Agent not found. Check spelling or register using /register.")
            return
    ws_preds = await get_sheet("predictions")
    preds    = await ws_preds.get_all_values()
    count    = sum(1 for r in preds[1:] if r[0] == user_id)
    response = (
        f"**Agent Name:** {data['agent_name']}\n"
        f"**Bio:** {data['bio']}\n"
        f"**Submissions:** {count}\n"
        f"**Win Rate:** N/A\n"
        f"**Current Streak:** N/A"
    )
    await interaction.response.send_message(response)

# ---------------------------------------
# /resolve Command (Admin Only)
# ---------------------------------------
@tree.command(name="resolve", description="Resolve a market with resolution source & note (Admin only)")
async def resolve(
    interaction: discord.Interaction,
    market_id: str,
    outcome: bool,
    resolution_source: str,
    resolution_note: str
):
    await interaction.response.defer(ephemeral=False)
    if not interaction.user.guild_permissions.administrator:
        await interaction.followup.send("You don’t have permission to resolve markets.", ephemeral=True)
        return
    m = await sheets_manager.get_market(market_id)
    if m is None:
        await interaction.followup.send("No market found with this ID.")
        return
    if m["resolved"]:
        await interaction.followup.send("This market has already been resolved.")
        return

    ws_preds = await get_sheet("predictions")
    rows     = await ws_preds.get_all_values()
    lb       = await sheets_manager.fetch_leaderboard()
    total    = 0

    for idx, r in enumerate(rows[1:], start=2):
        if r[1] == market_id and r[4] == "FALSE":
            total += 1
            p_time = datetime.fromisoformat(r[3])
            p_norm = float(r[2]) / 100.0
            o_val  = 1 if outcome else 0
            BS     = (p_norm - o_val) ** 2
            reward = 1 - BS
            frac   = max(0.0, min(1.0, (p_time - m["open_time"]).total_seconds() / (m["close_time"] - m["open_time"]).total_seconds()))
            tw     = math.exp(-TIME_WEIGHT_LAMBDA * frac)
            pts    = reward * tw

            # mark resolved
            await ws_preds.update_acell(f"E{idx}", "TRUE")

            # update lb
            uid = r[0]
            agent = await sheets_manager.get_agent(uid)
            if agent:
                if uid in lb:
                    lb[uid]["score"] += pts
                    lb[uid]["submissions"] += 1
                    if p_time > lb[uid]["last_submission"]:
                        lb[uid]["last_submission"] = p_time
                else:
                    lb[uid] = {
                        "agent_name": agent["agent_name"],
                        "score": pts,
                        "submissions": 1,
                        "last_submission": p_time
                    }

    await sheets_manager.update_leaderboard_sheet(lb)

    ws_m = await get_sheet("markets")
    cell = await ws_m.find(market_id)
    await ws_m.update_cell(cell.row, 6, "FALSE")
    await ws_m.update_cell(cell.row, 7, "TRUE")
    await ws_m.update_cell(cell.row, 8, str(1 if outcome else 0))
    await ws_m.update_cell(cell.row, 9, resolution_source)
    await ws_m.update_cell(cell.row,10, resolution_note)

    await interaction.followup.send(
        f"Market **{market_id}** resolved with outcome **{'True' if outcome else 'False'}**.\n"
        f"Resolution Source: {resolution_source}\n"
        f"Resolution Note: {resolution_note}\n"
        f"Processed {total} prediction(s) and updated leaderboard scores."
    )

# ---------------------------------------
# /clear_agent Command (Dev Only)
# ---------------------------------------
@tree.command(name="clear_agent", description="(Dev) Remove your agent registration")
async def clear_agent(interaction: discord.Interaction):
    # Defer so we have time to talk to Sheets
    await interaction.response.defer(ephemeral=True)
    user_id = str(interaction.user.id)
    ws = await get_sheet("agents")
    try:
        cell = await ws.find(user_id)
        await ws.delete_row(cell.row)
        await interaction.followup.send("✅ Your agent registration has been cleared.")
    except Exception:
        await interaction.followup.send("ℹ️ No registration found for you.")
# ---------------------------------------
# on_ready Event: Sync Commands and Debug Print
# ---------------------------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}.")
    try:
        guild = discord.Object(id=TEST_GUILD_ID)
        tree.copy_global_to(guild=guild)
        synced = await tree.sync(guild=guild)
        print(f"Synced {len(synced)} command(s) for test guild {TEST_GUILD_ID}.")
        for cmd in tree.get_commands(guild=guild):
            print(f" - {cmd.name}: {cmd.description}")
    except Exception as e:
        print("Error syncing commands:", e)

# ---------------------------------------
# Bot Run
# ---------------------------------------
if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    if not TOKEN:
        print("Error: DISCORD_BOT_TOKEN environment variable not set.")
    else:
        bot.run(TOKEN)