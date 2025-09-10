import discord
from discord.ext import commands
import random
import asyncio
from dotenv import load_dotenv
import os
import resend
from aiohttp import web

load_dotenv()

# Configuration
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
resend.api_key = os.getenv("RESEND_API_KEY")
ALLOWED_EMAILS_FILEPATH = os.getenv('EMAILS_FILEPATH', 'allowed_emails.txt')
HOST = os.getenv('HOST', '0.0.0.0')
PORT = int(os.getenv('PORT', '10000'))

# Allowed emails list - only these emails can verify
# Load allowed emails from file
def load_allowed_emails():
    try:
        with open(ALLOWED_EMAILS_FILEPATH, 'r') as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        print(f"Warning: {ALLOWED_EMAILS_FILEPATH} not found. No emails will be allowed for verification.")
        return []

ALLOWED_EMAILS = load_allowed_emails()

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Storage for verification codes and verified users
pending_codes = {}  # {user_id: {"email": str, "code": str, "guild_id": int}}
verified_users = {}  # {user_id: email}

VERIFIED_ROLE = "verified"

web_server_started = False

async def start_web_server():
    app = web.Application()

    async def health(_request):
        return web.Response(text="OK")

    app.router.add_get('/', health)
    app.router.add_get('/health', health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, HOST, PORT)
    await site.start()
    print(f"HTTP server listening on {HOST}:{PORT}")

@bot.event
async def on_ready():
    print(f"Email verification bot ready: {bot.user.name if bot.user else 'Unknown'}")
    global web_server_started
    if not web_server_started:
        asyncio.create_task(start_web_server())
        web_server_started = True

@bot.event
async def on_member_join(member):
    """Send welcome DM with verification instructions when a user joins"""
    try:
        # Create welcome embed
        embed = discord.Embed(
            title="Welcome to QuantChallenge 2025! 🎉",
            description="To gain full access to the server, you'll need to verify your email address.",
            color=0x00ff00
        )
        
        embed.add_field(
            name="📧 How to Verify:",
            value="Use the command `!verify your@email.com` (replace with your actual email)\n"
                  "Make sure to use the same email address you received the QuantChallenge 2025 invitation with!",
            inline=False
        )
        
        embed.add_field(
            name="🔐 Verification Process:",
            value="1. Send `!verify your@email.com` in DMs with QuantChallengeBot (me!)\n"
                  "2. Check your email for a 6-digit verification code\n"
                  "3. Send `!code 123456` (replace with your actual code) in our DM\n"
                  "4. You'll automatically get the verified role!",
            inline=False
        )
        
        embed.add_field(
            name="❓ Need Help?",
            value="Use `!help_verify` for more information or contact an administrator if you're having trouble.",
            inline=False
        )
        
        embed.set_footer(text="All verification happens privately in DMs for your security!")
        
        # Send welcome DM
        await member.send(embed=embed)
        print(f"Sent welcome DM to {member.name} ({member.id})")
        
    except discord.Forbidden:
        # User has DMs disabled, log this but don't error out
        print(f"Could not send welcome DM to {member.name} ({member.id}) - DMs disabled")
    except Exception as e:
        # Log any other errors
        print(f"Error sending welcome DM to {member.name} ({member.id}): {e}")

def send_verification_email(email, code):
    """Send verification code to email"""
    try:
        r = resend.Emails.send({
            "from": "donotreply@quantchallenge.org",
            "to": email,
            "subject": "QuantChallenge 2025 Discord Verification Code",
            "html": f"<p>Your QuantChallenge 2025 Discord verification code is: <strong>{code}</strong></p>"
        })

        return True
    except Exception as e:
        print(f"Email send failed: {e}")
        return False

@bot.command(name='verify')
async def verify_email(ctx, email: str | None = None):
    """Start email verification process"""
    # If used in a channel, redirect to DMs
    if ctx.guild is not None:
        try:
            await ctx.author.send("📨 Let's continue the verification process in DMs! Please use `!verify your@email.com` here.")
            await ctx.send("📨 I've sent you a DM to continue the verification process!")
            return
        except discord.Forbidden:
            await ctx.send("❌ I couldn't send you a DM. Please enable DMs from server members and try again.")
            return
    
    if not email:
        await ctx.send("Usage: `!verify your@email.com`")
        return
    
    # Check if email is in allowed list
    if email.lower() not in [e.lower() for e in ALLOWED_EMAILS]:
        await ctx.send("❌ Please check to see this is the same email address with which you received the QuantChallenge 2025 accepted invite.")
        return
    
    # We need to find which guild the user wants to verify for
    # For now, we'll use the most recent guild they share with the bot
    user_guilds = [guild for guild in bot.guilds if guild.get_member(ctx.author.id)]
    if not user_guilds:
        await ctx.send("❌ You don't seem to be in any servers with this bot.")
        return
    
    # Use the first guild found (in a real implementation, you might want to ask the user)
    guild = user_guilds[0]
    
    # Check if user is already verified
    member = guild.get_member(ctx.author.id)
    if member:
        role = discord.utils.get(guild.roles, name=VERIFIED_ROLE)
        if role and role in member.roles:
            await ctx.send("✅ You are already verified!")
            return
    
    # Generate 6-digit code
    code = str(random.randint(100000, 999999))
    
    # Send email
    if send_verification_email(email, code):
        pending_codes[ctx.author.id] = {"email": email.lower(), "code": code, "guild_id": guild.id}
        await ctx.send(f"📧 Verification code sent to {email}. Use `!code <your_code>` to verify.")
        
        # Auto-delete pending code after 10 minutes
        await asyncio.sleep(600)
        if ctx.author.id in pending_codes:
            del pending_codes[ctx.author.id]
    else:
        await ctx.send("❌ Failed to send verification email. Please try again.")

@bot.command(name='code')
async def verify_code(ctx, code: str | None = None):
    """Verify the email code"""
    # If used in a channel, redirect to DMs
    if ctx.guild is not None:
        try:
            await ctx.author.send("📨 Please use the `!code` command in our DM conversation!")
            await ctx.send("📨 Please check your DMs to enter your verification code!")
            return
        except discord.Forbidden:
            await ctx.send("❌ I couldn't send you a DM. Please enable DMs from server members and try again.")
            return
    
    if not code:
        await ctx.send("Usage: `!code 123456`")
        return
    
    user_id = ctx.author.id
    if user_id not in pending_codes:
        await ctx.send("❌ No pending verification. Use `!verify <email>` first.")
        return
    
    if pending_codes[user_id]["code"] != code:
        await ctx.send("❌ Invalid verification code.")
        return
    
    # Verification successful
    email = pending_codes[user_id]["email"]
    guild_id = pending_codes[user_id]["guild_id"]
    verified_users[user_id] = email
    del pending_codes[user_id]
    
    # Get the guild and assign verified role
    guild = bot.get_guild(guild_id)
    if guild:
        member = guild.get_member(user_id)
        if member:
            role = discord.utils.get(guild.roles, name=VERIFIED_ROLE)
            if role:
                await member.add_roles(role)
                await ctx.send(f"✅ Email verified! You now have the {VERIFIED_ROLE} role in {guild.name}.")
            else:
                await ctx.send(f"✅ Email verified for {guild.name}! (Note: Verified role not found)")
        else:
            await ctx.send("✅ Email verified! (Note: Could not assign role - you may have left the server)")
    else:
        await ctx.send("✅ Email verified! (Note: Could not find the server to assign role)")

@bot.command(name='status')
async def verification_status(ctx):
    """Check verification status"""
    # If used in a channel, redirect to DMs
    if ctx.guild is not None:
        try:
            await ctx.author.send("📨 Let me check your verification status in DMs!")
            await ctx.send("📨 I've sent your verification status to your DMs!")
            # Now send the actual status in DM
            member = ctx.guild.get_member(ctx.author.id)
            if member:
                role = discord.utils.get(ctx.guild.roles, name=VERIFIED_ROLE)
                if role and role in member.roles:
                    email = verified_users.get(ctx.author.id, "unknown")
                    if email == "unknown":
                        await ctx.author.send("✅ Verified!")
                    else:
                        await ctx.author.send(f"✅ Verified with: {email}")
                else:
                    await ctx.author.send("❌ Not verified. Use `!verify <email>` to start.")
            else:
                await ctx.author.send("❌ Not verified. Use `!verify <email>` to start.")
            return
        except discord.Forbidden:
            await ctx.send("❌ I couldn't send you a DM. Please enable DMs from server members and try again.")
            return
    
    # Handle DM context - need to check all guilds the bot is in
    is_verified = False
    email = "unknown"
    for guild in bot.guilds:
        member = guild.get_member(ctx.author.id)
        if member:
            role = discord.utils.get(guild.roles, name=VERIFIED_ROLE)
            if role and role in member.roles:
                is_verified = True
                email = verified_users.get(ctx.author.id, "unknown")
                break
    
    if is_verified:
        if email == "unknown":
            await ctx.send("✅ Verified!")
        else:
            await ctx.send(f"✅ Verified with: {email}")
    else:
        await ctx.send("❌ Not verified. Use `!verify <email>` to start.")

@bot.command(name='help_verify')
async def help_verify(ctx):
    """Show verification help"""
    embed = discord.Embed(
        title="Email Verification Help",
        description="Only specific emails are allowed to verify. All verification happens in DMs for privacy!",
        color=0x00ff00
    )
    embed.add_field(name="!verify <email>", value="Start verification with your email (redirects to DMs)", inline=False)
    embed.add_field(name="!code <code>", value="Enter the 6-digit code from email (DMs only)", inline=False)
    embed.add_field(name="!status", value="Check your verification status (redirects to DMs)", inline=False)
    embed.add_field(name="!help_verify", value="Show this help message", inline=False)
    
    # If used in a channel, send to DMs
    if ctx.guild is not None:
        try:
            await ctx.author.send(embed=embed)
            await ctx.send("📨 I've sent the help information to your DMs!")
            return
        except discord.Forbidden:
            await ctx.send("❌ I couldn't send you a DM. Please enable DMs from server members and try again.")
            return
    
    # Send in DMs
    await ctx.send(embed=embed)

# Admin command to manage allowed emails
@bot.command(name='add_email')
@commands.has_permissions(administrator=True)
async def add_email(ctx, email: str):
    """Add email to allowed list (Admin only)"""
    if email.lower() not in [e.lower() for e in ALLOWED_EMAILS]:
        ALLOWED_EMAILS.append(email.lower())
        await ctx.send(f"✅ Added {email} to allowed emails list.")
    else:
        await ctx.send(f"❌ {email} is already in the allowed list.")

@bot.command(name='list_emails')
@commands.has_permissions(administrator=True)
async def list_emails(ctx):
    """List all allowed emails (Admin only)"""
    if len(ALLOWED_EMAILS) > 10:
        emails = "\n".join(ALLOWED_EMAILS[:5] + ["..."] + ALLOWED_EMAILS[-5:])
    else:
        emails = "\n".join(ALLOWED_EMAILS)
    embed = discord.Embed(title=f"Allowed Emails ({len(ALLOWED_EMAILS)})", description=emails, color=0x0099ff)
    await ctx.send(embed=embed)

if __name__ == "__main__":
    if DISCORD_TOKEN:
        bot.run(DISCORD_TOKEN)
    else:
        print("Error: DISCORD_TOKEN not found in environment variables")