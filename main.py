import discord
from discord import app_commands
from discord.ext import commands
import json
import asyncio
import requests
from typing import Optional
import os
import logging
import webserver

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# Data file
DATA_FILE = os.getenv("DATA_FILE", "items_data.json")


# Load/Save data functions
def load_data():
    """Load data from JSON file"""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            logger.error(f"Error decoding {DATA_FILE}, creating new file")
            return {"items": []}
    return {"items": []}


def save_data(data):
    """Save data to JSON file"""
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(data, f, indent=4)
        logger.info("Data saved successfully")
    except Exception as e:
        logger.error(f"Error saving data: {e}")


# Roblox API functions
def get_user_id_from_username(username):
    """Convert Roblox username to user ID"""
    url = "https://users.roblox.com/v1/usernames/users"
    payload = {"usernames": [username]}

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data.get("data") and len(data["data"]) > 0:
            return data["data"][0]["id"]
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Error getting user ID: {e}")
        return None


def check_gamepass_ownership(user_id, gamepass_id):
    """Check if user owns a specific gamepass"""
    url = f"https://apis.roblox.com/game-passes/v1/users/{user_id}/game-passes?count=100"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        logger.info(f"Checking gamepass ownership for user {user_id}")
        logger.debug(f"Looking for gamepass ID: {gamepass_id}")

        if "gamePasses" in data:
            for gamepass in data["gamePasses"]:
                if str(gamepass.get("gamePassId")) == str(gamepass_id):
                    logger.info(f"✅ User owns gamepass {gamepass_id}")
                    return True
            logger.info(f"❌ User does not own gamepass {gamepass_id}")
        else:
            logger.warning("No 'gamePasses' key in response")

        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Error checking gamepass ownership: {e}")
        return False


# View for item selection (DM interaction)
class ItemSelectView(discord.ui.View):
    def __init__(self, items, user):
        super().__init__(timeout=180)
        self.user = user

        # Create a select menu with all items
        options = [
            discord.SelectOption(label=item["name"], value=item["name"])
            for item in items
        ]

        select = discord.ui.Select(
            placeholder="Choose an item to purchase...",
            options=options
        )
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("This menu is not for you!", ephemeral=True)
            return

        selected_item_name = interaction.data["values"][0]
        data = load_data()

        # Find the selected item
        item = next((i for i in data["items"] if i["name"] == selected_item_name), None)

        if not item:
            await interaction.response.send_message("Item not found!", ephemeral=True)
            return

        # Show purchase confirmation
        gamepass_url = f"https://www.roblox.com/game-pass/{item['gamepass_id']}"

        view = PurchaseConfirmView(item, self.user, gamepass_url)

        embed = discord.Embed(
            title=f"Purchase: {item['name']}",
            description=f"**Buy the gamepass, the item will be delivered after verification.**\n\n[Click here to buy the gamepass]({gamepass_url})",
            color=discord.Color.blue()
        )
        embed.add_field(name="Gamepass ID", value=item['gamepass_id'])

        await interaction.response.send_message(embed=embed, view=view)


# View for purchase confirmation
class PurchaseConfirmView(discord.ui.View):
    def __init__(self, item, user, gamepass_url):
        super().__init__(timeout=300)
        self.item = item
        self.user = user
        self.gamepass_url = gamepass_url
        self.is_processing = False

    @discord.ui.button(label="I Bought It", style=discord.ButtonStyle.green)
    async def bought_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("This is not for you!", ephemeral=True)
            return

        if self.is_processing:
            await interaction.response.send_message("⏳ Verification already in progress...", ephemeral=True)
            return

        self.is_processing = True

        await interaction.response.send_message("🔄 Verifying your purchase... Please wait.", ephemeral=True)
        await interaction.followup.send("Please provide your Roblox username:")

        def check(m):
            return m.author.id == interaction.user.id and isinstance(m.channel, discord.DMChannel)

        try:
            msg = await bot.wait_for('message', timeout=60.0, check=check)
            roblox_username = msg.content.strip()

            logger.info(f"User provided username: {roblox_username}")

            user_id = get_user_id_from_username(roblox_username)

            if not user_id:
                await interaction.followup.send("❌ Could not find that Roblox username. Please try again.")
                self.is_processing = False
                return

            logger.info(f"Converted to user ID: {user_id}")

            verified = False
            for attempt in range(5):
                logger.info(f"Verification attempt {attempt + 1}/5")

                if check_gamepass_ownership(user_id, self.item['gamepass_id']):
                    verified = True
                    break

                if attempt < 4:
                    await interaction.followup.send(
                        f"⏳ Verification attempt {attempt + 1}/5... Checking again in 5 seconds.")
                    await asyncio.sleep(5)

            if verified:
                await interaction.followup.send(f"✅ Purchase verified! Here's your item: **{self.item['name']}**")
                await interaction.followup.send(self.item['file_url'])

                for child in self.children:
                    child.disabled = True
                await interaction.message.edit(view=self)
            else:
                await interaction.followup.send(
                    f"❌ **Verification Failed**\n\n"
                    f"Possible reasons:\n"
                    f"• You haven't purchased the gamepass yet\n"
                    f"• The gamepass purchase hasn't processed (can take up to 30 seconds)\n"
                    f"• The gamepass ID might be incorrect\n\n"
                    f"If you bought the gamepass but didn't receive the file, contact the server owner.\n\n"
                    f"**Your Roblox Username:** {roblox_username}\n"
                    f"**Gamepass ID:** {self.item['gamepass_id']}"
                )

        except asyncio.TimeoutError:
            await interaction.followup.send("❌ Verification timed out. Please try again.")
        except Exception as e:
            logger.error(f"Error during verification: {e}")
            await interaction.followup.send("❌ An error occurred during verification. Please try again.")
        finally:
            self.is_processing = False

    @discord.ui.button(label="Nevermind", style=discord.ButtonStyle.gray)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("This is not for you!", ephemeral=True)
            return

        await interaction.response.send_message("Purchase cancelled.", ephemeral=True)

        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)


# Admin Commands
@tree.command(name="upload", description="Upload a new item to sell (Server Owner Only)")
@app_commands.describe(
    item_name="Name of the item",
    file="The file to sell",
    gamepass_id="Roblox Gamepass ID"
)
@app_commands.guild_only()
async def upload(interaction: discord.Interaction, item_name: str, file: discord.Attachment, gamepass_id: str):
    if interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("❌ Only the server owner can use this command!", ephemeral=True)
        return

    data = load_data()

    if any(item["name"] == item_name for item in data["items"]):
        await interaction.response.send_message(f"❌ An item with the name '{item_name}' already exists!",
                                                ephemeral=True)
        return

    new_item = {
        "name": item_name,
        "file_url": file.url,
        "gamepass_id": gamepass_id
    }

    data["items"].append(new_item)
    save_data(data)

    embed = discord.Embed(
        title="✅ Item Uploaded",
        description=f"**{item_name}** has been added to the store!",
        color=discord.Color.green()
    )
    embed.add_field(name="Gamepass ID", value=gamepass_id)
    embed.add_field(name="File", value=file.filename)

    await interaction.response.send_message(embed=embed)
    logger.info(f"Item uploaded: {item_name}")


@tree.command(name="edit", description="Edit an existing item (Server Owner Only)")
@app_commands.describe(
    item_name="Name of the item to edit",
    new_file="New file (optional)",
    new_gamepass_id="New Gamepass ID (optional)"
)
@app_commands.guild_only()
async def edit(interaction: discord.Interaction, item_name: str, new_file: Optional[discord.Attachment] = None,
               new_gamepass_id: Optional[str] = None):
    if interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("❌ Only the server owner can use this command!", ephemeral=True)
        return

    data = load_data()

    item = next((i for i in data["items"] if i["name"] == item_name), None)

    if not item:
        await interaction.response.send_message(f"❌ Item '{item_name}' not found!", ephemeral=True)
        return

    if new_file:
        item["file_url"] = new_file.url
    if new_gamepass_id:
        item["gamepass_id"] = new_gamepass_id

    save_data(data)

    embed = discord.Embed(
        title="✅ Item Updated",
        description=f"**{item_name}** has been updated!",
        color=discord.Color.blue()
    )
    if new_file:
        embed.add_field(name="New File", value=new_file.filename)
    if new_gamepass_id:
        embed.add_field(name="New Gamepass ID", value=new_gamepass_id)

    await interaction.response.send_message(embed=embed)
    logger.info(f"Item edited: {item_name}")


@tree.command(name="delete", description="Delete an item (Server Owner Only)")
@app_commands.describe(item_name="Name of the item to delete")
@app_commands.guild_only()
async def delete(interaction: discord.Interaction, item_name: str):
    if interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("❌ Only the server owner can use this command!", ephemeral=True)
        return

    data = load_data()

    initial_length = len(data["items"])
    data["items"] = [i for i in data["items"] if i["name"] != item_name]

    if len(data["items"]) == initial_length:
        await interaction.response.send_message(f"❌ Item '{item_name}' not found!", ephemeral=True)
        return

    save_data(data)

    embed = discord.Embed(
        title="✅ Item Deleted",
        description=f"**{item_name}** has been removed from the store.",
        color=discord.Color.red()
    )

    await interaction.response.send_message(embed=embed)
    logger.info(f"Item deleted: {item_name}")


@tree.command(name="showall", description="Show all uploaded items (Server Owner Only)")
@app_commands.guild_only()
async def showall(interaction: discord.Interaction):
    if interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("❌ Only the server owner can use this command!", ephemeral=True)
        return

    data = load_data()

    if not data["items"]:
        await interaction.response.send_message("📭 No items uploaded yet.", ephemeral=True)
        return

    embed = discord.Embed(
        title="📦 All Items",
        description=f"Total items: {len(data['items'])}",
        color=discord.Color.purple()
    )

    for item in data["items"]:
        embed.add_field(
            name=item["name"],
            value=f"**Gamepass ID:** {item['gamepass_id']}\n[File Link]({item['file_url']})",
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if isinstance(message.channel, discord.DMChannel):
        if message.content.lower() in ['buyitem']:
            data = load_data()

            if not data["items"]:
                await message.channel.send("📭 No items available for sale.")
                return

            embed = discord.Embed(
                title="🛒 Welcome to the Store!",
                description="Select an item you'd like to purchase:",
                color=discord.Color.gold()
            )

            view = ItemSelectView(data["items"], message.author)
            await message.channel.send(embed=embed, view=view)

    await bot.process_commands(message)


@bot.event
async def on_ready():
    try:
        await tree.sync()
        logger.info(f'✅ Bot is ready! Logged in as {bot.user}')
        logger.info(f'Bot ID: {bot.user.id}')
    except Exception as e:
        logger.error(f"Error syncing commands: {e}")


@bot.event
async def on_error(event, *args, **kwargs):
    logger.error(f"Error in {event}", exc_info=True)


def main():
    """Main entry point for the bot"""
    token = os.getenv("DISCORD_TOKEN")

    if not token:
        logger.error("DISCORD_TOKEN environment variable not set!")
        raise ValueError("DISCORD_TOKEN environment variable is required")

    try:
        bot.run(token)
    except discord.LoginFailure:
        logger.error("Invalid Discord token provided")
        raise
    except Exception as e:
        logger.error(f"Error running bot: {e}")
        raise



if __name__ == "__main__":
    webserver.keep_alive()
    main()
