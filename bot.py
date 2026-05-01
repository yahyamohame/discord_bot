import discord
from discord.ext import commands
from discord import app_commands
import json
import os
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────
TOKENS_PER_MESSAGE = 1
DATA_FILE = "data.json"

# ── Data helpers ──────────────────────────────────────────────────────────────
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"users": {}, "trusted_roles": [], "transactions": []}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def get_user(data, user_id: str):
    if user_id not in data["users"]:
        data["users"][user_id] = {"tokens": 0, "messages": 0}
    return data["users"][user_id]

def is_trusted(interaction: discord.Interaction, data: dict) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    member_role_ids = [str(r.id) for r in interaction.user.roles]
    return any(rid in data.get("trusted_roles", []) for rid in member_role_ids)

# ── Bot setup ─────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} ({bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"❌ Sync error: {e}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    data = load_data()
    user = get_user(data, str(message.author.id))
    user["tokens"] += TOKENS_PER_MESSAGE
    user["messages"] += 1
    save_data(data)
    await bot.process_commands(message)

# ── /tokens ───────────────────────────────────────────────────────────────────
@bot.tree.command(name="tokens", description="Check your tokens (or another user's)")
@app_commands.describe(member="Leave empty to check your own tokens")
async def tokens_cmd(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    data = load_data()
    user = get_user(data, str(target.id))
    save_data(data)

    embed = discord.Embed(title="💰 Tokens Balance", color=discord.Color.gold())
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="User", value=target.mention, inline=True)
    embed.add_field(name="Tokens", value=f"**{user['tokens']:,}** 🪙", inline=True)
    embed.add_field(name="Messages Sent", value=f"{user['messages']:,}", inline=True)
    await interaction.response.send_message(embed=embed)

# ── /leaderboard ──────────────────────────────────────────────────────────────
@bot.tree.command(name="leaderboard", description="Top 10 users by tokens")
async def leaderboard_cmd(interaction: discord.Interaction):
    data = load_data()
    guild = interaction.guild
    sorted_users = sorted(data["users"].items(), key=lambda x: x[1]["tokens"], reverse=True)[:10]

    embed = discord.Embed(title="🏆 Tokens Leaderboard", color=discord.Color.gold())
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (uid, udata) in enumerate(sorted_users):
        member = guild.get_member(int(uid))
        name = member.display_name if member else f"Unknown ({uid})"
        medal = medals[i] if i < 3 else f"`#{i+1}`"
        lines.append(f"{medal} **{name}** — {udata['tokens']:,} tkns")
    embed.description = "\n".join(lines) if lines else "No data yet!"
    await interaction.response.send_message(embed=embed)

# ── /taketokens — trusted roles + admins ─────────────────────────────────────
@bot.tree.command(name="taketokens", description="Take tokens from a user after delivering their reward (mods/admins)")
@app_commands.describe(
    member="The user to take tokens from",
    amount="How many tokens to take",
    reason="What did they redeem? (e.g. CS2 Steam key)"
)
async def taketokens_cmd(interaction: discord.Interaction, member: discord.Member, amount: int, reason: str = ""):
    data = load_data()

    if not is_trusted(interaction, data):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return

    if amount <= 0:
        await interaction.response.send_message("❌ Amount must be a positive number.", ephemeral=True)
        return

    user = get_user(data, str(member.id))

    if user["tokens"] < amount:
        await interaction.response.send_message(
            f"❌ **{member.display_name}** only has **{user['tokens']:,}** tokens — not enough to take {amount:,}.",
            ephemeral=True
        )
        return

    user["tokens"] -= amount
    data["transactions"].append({
        "type": "take",
        "target_id": str(member.id),
        "target_name": str(member),
        "mod_id": str(interaction.user.id),
        "mod_name": str(interaction.user),
        "amount": amount,
        "reason": reason,
        "timestamp": datetime.utcnow().isoformat()
    })
    save_data(data)

    embed = discord.Embed(title="✅ Tokens Taken", color=discord.Color.red())
    embed.add_field(name="User", value=member.mention, inline=True)
    embed.add_field(name="Tokens Taken", value=f"{amount:,} 🪙", inline=True)
    embed.add_field(name="New Balance", value=f"{user['tokens']:,} tkns", inline=True)
    if reason:
        embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text=f"Done by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)

    # DM the user
    try:
        dm_embed = discord.Embed(
            title="🛍️ Tokens Redeemed!",
            description=f"**{amount:,}** tokens were taken from your balance by a moderator.",
            color=discord.Color.orange()
        )
        if reason:
            dm_embed.add_field(name="Item/Reason", value=reason)
        dm_embed.add_field(name="Remaining Balance", value=f"{user['tokens']:,} tkns")
        await member.send(embed=dm_embed)
    except discord.Forbidden:
        pass

# ── /givetokens — admins only ─────────────────────────────────────────────────
@bot.tree.command(name="givetokens", description="Give tokens to a user (admins only)")
@app_commands.describe(member="The user to give tokens to", amount="How many tokens to give")
@app_commands.default_permissions(administrator=True)
async def givetokens_cmd(interaction: discord.Interaction, member: discord.Member, amount: int):
    if amount <= 0:
        await interaction.response.send_message("❌ Amount must be a positive number.", ephemeral=True)
        return

    data = load_data()
    user = get_user(data, str(member.id))
    user["tokens"] += amount
    data["transactions"].append({
        "type": "give",
        "target_id": str(member.id),
        "target_name": str(member),
        "mod_id": str(interaction.user.id),
        "mod_name": str(interaction.user),
        "amount": amount,
        "reason": "Manual grant",
        "timestamp": datetime.utcnow().isoformat()
    })
    save_data(data)

    embed = discord.Embed(title="✅ Tokens Given", color=discord.Color.green())
    embed.add_field(name="User", value=member.mention, inline=True)
    embed.add_field(name="Tokens Given", value=f"{amount:,} 🪙", inline=True)
    embed.add_field(name="New Balance", value=f"{user['tokens']:,} tkns", inline=True)
    await interaction.response.send_message(embed=embed)

# ── /addrole — give a role permission to use /taketokens ─────────────────────
@bot.tree.command(name="addrole", description="[Admin] Allow a role to use /taketokens")
@app_commands.describe(role="The role to grant permission")
@app_commands.default_permissions(administrator=True)
async def addrole_cmd(interaction: discord.Interaction, role: discord.Role):
    data = load_data()
    role_id = str(role.id)
    if role_id in data.get("trusted_roles", []):
        await interaction.response.send_message(f"ℹ️ **{role.name}** already has that permission.", ephemeral=True)
        return
    data.setdefault("trusted_roles", []).append(role_id)
    save_data(data)
    await interaction.response.send_message(f"✅ **{role.name}** can now use `/taketokens`.", ephemeral=True)

# ── /removerole ───────────────────────────────────────────────────────────────
@bot.tree.command(name="removerole", description="[Admin] Remove a role's permission to use /taketokens")
@app_commands.describe(role="The role to remove permission from")
@app_commands.default_permissions(administrator=True)
async def removerole_cmd(interaction: discord.Interaction, role: discord.Role):
    data = load_data()
    role_id = str(role.id)
    if role_id not in data.get("trusted_roles", []):
        await interaction.response.send_message(f"ℹ️ **{role.name}** doesn't have that permission.", ephemeral=True)
        return
    data["trusted_roles"].remove(role_id)
    save_data(data)
    await interaction.response.send_message(f"✅ Removed permission from **{role.name}**.", ephemeral=True)

# ── /listroles ────────────────────────────────────────────────────────────────
@bot.tree.command(name="listroles", description="[Admin] See which roles can use /taketokens")
@app_commands.default_permissions(administrator=True)
async def listroles_cmd(interaction: discord.Interaction):
    data = load_data()
    trusted = data.get("trusted_roles", [])
    guild = interaction.guild
    embed = discord.Embed(title="🔑 Trusted Roles (can use /taketokens)", color=discord.Color.blurple())
    if not trusted:
        embed.description = "No trusted roles set. Only admins can use `/taketokens`."
    else:
        lines = []
        for rid in trusted:
            role = guild.get_role(int(rid))
            lines.append(f"• {role.mention if role else f'Deleted role ({rid})'}")
        embed.description = "\n".join(lines)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ── /history ──────────────────────────────────────────────────────────────────
@bot.tree.command(name="history", description="[Admin] View recent transactions for a user")
@app_commands.describe(member="The user to check")
@app_commands.default_permissions(administrator=True)
async def history_cmd(interaction: discord.Interaction, member: discord.Member):
    data = load_data()
    txns = [t for t in data.get("transactions", []) if t["target_id"] == str(member.id)][-10:][::-1]
    embed = discord.Embed(title=f"📋 History — {member.display_name}", color=discord.Color.blurple())
    if not txns:
        embed.description = "No transactions yet."
    else:
        lines = []
        for t in txns:
            icon = "➕" if t["type"] == "give" else "➖"
            ts = t["timestamp"][:10]
            reason = f" ({t['reason']})" if t.get("reason") else ""
            lines.append(f"`{ts}` {icon} **{t['amount']:,} tkns** by {t['mod_name']}{reason}")
        embed.description = "\n".join(lines)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("❌ ERROR: Set the DISCORD_BOT_TOKEN environment variable before running.")
        exit(1)
    bot.run(token)
