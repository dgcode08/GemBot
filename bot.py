"""
================================================================================
GemCasino Discord Bot - Complete Single File
================================================================================
Install requirements first:
    pip install discord.py Pillow --break-system-packages

Run with:
    python3 gemcasino_bot.py

Set your bot token in the BOT_TOKEN variable near the bottom, or as an
environment variable DISCORD_BOT_TOKEN.

Data is stored in a local JSON file (gemcasino_data.json) so balances,
promo codes, locks, etc. persist between restarts.
================================================================================
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks

import asyncio
import json
import math
import os
import random
import time
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from PIL import Image, ImageDraw, ImageFont

# ==============================================================================
# CONFIG
# ==============================================================================

BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "MTUxOTE0NDUzNDkxNzc3OTQ2Ng.GYg-2L.VTF3BPHA9sHNHsQ98Uf2V0acF_zQ8OtZwBJ040")

DATA_FILE = "gemcasino_data.json"

OWNER_IDS = (1365731306037711014)      # fill in Discord user IDs (ints) of owners
ADMIN_IDS = (1464973162654597337)      # fill in Discord user IDs (ints) of admins

PVP_TAX_RATE = 0.06
GIFT_TAX_RATE = 0.02
DAILY_AMOUNT = 10_000_000

COMPUTER_COINFLIP_MULTIPLIER = 1.94
BLACKJACK_PAYOUT_MULTIPLIER = 1.95
BLACKJACK_NATURAL_MULTIPLIER = 2.4

RANK_TIERS = [
    ("Gambler", 0),
    ("1B High Roller", 1_000_000_000),
    ("10B High Roller", 10_000_000_000),
    ("25B High Roller", 25_000_000_000),
    ("50B High Roller", 50_000_000_000),
    ("100B High Roller", 100_000_000_000),
]

WIN_COLOR = 0x2ECC71
LOSS_COLOR = 0xE74C3C
NEUTRAL_COLOR = 0x2F3136
GOLD_COLOR = 0xF5A623

SERVER_NAME = ".gg/GemCasino"

FONT_PATH_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


# ==============================================================================
# HELPERS
# ==============================================================================

def fmt_gems(n) -> str:
    n = int(n)
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def get_rank(wagered: int) -> str:
    rank_name = RANK_TIERS[0][0]
    for name, minimum in RANK_TIERS:
        if wagered >= minimum:
            rank_name = name
    return rank_name


def load_font(size):
    try:
        return ImageFont.truetype(FONT_PATH_BOLD, size)
    except OSError:
        return ImageFont.load_default()


# ==============================================================================
# PERSISTENT DATA STORE
# ==============================================================================

class DataStore:
    """
    Simple JSON-backed persistence layer. Holds balances, stats, promo codes,
    bans, blacklist, locked games/commands, race leaderboard, and profit log.
    """

    def __init__(self, path: str):
        self.path = path
        self.data = {
            "balances": {},
            "stats": {},
            "promos": {},
            "redeemed": {},
            "banned": [],
            "blacklisted": {},
            "locked_games": [],
            "locked_commands": [],
            "race": {},
            "profit": {"pvp_tax": 0, "gift_tax": 0},
            "last_daily": {},
            "nonce": {},
        }
        self.load()

    def load(self):
        if os.path.exists(self.path):
            with open(self.path, "r") as f:
                loaded = json.load(f)
                self.data.update(loaded)

    def save(self):
        with open(self.path, "w") as f:
            json.dump(self.data, f, indent=2)

    # ---- balances ----
    def get_balance(self, user_id: int) -> int:
        return self.data["balances"].get(str(user_id), 0)

    def set_balance(self, user_id: int, amount: int):
        self.data["balances"][str(user_id)] = max(0, int(amount))
        self.save()

    def add_balance(self, user_id: int, amount: int):
        self.set_balance(user_id, self.get_balance(user_id) + amount)

    # ---- stats ----
    def get_stats(self, user_id: int) -> dict:
        return self.data["stats"].setdefault(str(user_id), {"wins": 0, "losses": 0, "wagered": 0})

    def record_result(self, user_id: int, won: bool, wagered: int):
        stats = self.get_stats(user_id)
        if won:
            stats["wins"] += 1
        else:
            stats["losses"] += 1
        stats["wagered"] += wagered
        self.data["race"][str(user_id)] = self.data["race"].get(str(user_id), 0) + wagered
        self.save()

    # ---- promos ----
    def add_promo(self, code: str, gems: int):
        self.data["promos"][code.upper()] = {"gems": gems, "active": True}
        self.save()

    def remove_promo(self, code: str):
        self.data["promos"].pop(code.upper(), None)
        self.save()

    def toggle_promo(self, code: str):
        promo = self.data["promos"].get(code.upper())
        if promo:
            promo["active"] = not promo["active"]
            self.save()
        return promo

    def redeem_promo(self, user_id: int, code: str):
        code = code.upper()
        promo = self.data["promos"].get(code)
        if not promo or not promo["active"]:
            return None, "Invalid or inactive code."
        key = f"{user_id}:{code}"
        if self.data["redeemed"].get(key):
            return None, "You already redeemed this code."
        self.data["redeemed"][key] = True
        self.add_balance(user_id, promo["gems"])
        return promo["gems"], None

    # ---- bans / blacklist ----
    def ban(self, user_id: int):
        if user_id not in self.data["banned"]:
            self.data["banned"].append(user_id)
            self.save()

    def unban(self, user_id: int):
        if user_id in self.data["banned"]:
            self.data["banned"].remove(user_id)
            self.save()

    def is_banned(self, user_id: int) -> bool:
        return user_id in self.data["banned"]

    def blacklist(self, user_id: int, reason: str):
        self.data["blacklisted"][str(user_id)] = reason
        self.save()

    def unblacklist(self, user_id: int):
        self.data["blacklisted"].pop(str(user_id), None)
        self.save()

    def blacklist_reason(self, user_id: int):
        return self.data["blacklisted"].get(str(user_id))

    # ---- locks ----
    def lock_game(self, game: str):
        if game not in self.data["locked_games"]:
            self.data["locked_games"].append(game)
            self.save()

    def unlock_game(self, game: str):
        if game in self.data["locked_games"]:
            self.data["locked_games"].remove(game)
            self.save()

    def is_game_locked(self, game: str) -> bool:
        return game in self.data["locked_games"]

    def lock_command(self, command: str):
        if command not in self.data["locked_commands"]:
            self.data["locked_commands"].append(command)
            self.save()

    def unlock_command(self, command: str):
        if command in self.data["locked_commands"]:
            self.data["locked_commands"].remove(command)
            self.save()

    def is_command_locked(self, command: str) -> bool:
        return command in self.data["locked_commands"]

    # ---- race ----
    def reset_race(self):
        self.data["race"] = {}
        self.save()

    def get_race(self, top_n: int = 10):
        items = sorted(self.data["race"].items(), key=lambda x: x[1], reverse=True)
        return items[:top_n]

    # ---- profit ----
    def add_pvp_tax(self, amount: int):
        self.data["profit"]["pvp_tax"] += amount
        self.save()

    def add_gift_tax(self, amount: int):
        self.data["profit"]["gift_tax"] += amount
        self.save()

    def get_profit(self):
        return self.data["profit"]

    # ---- daily ----
    def can_claim_daily(self, user_id: int):
        last = self.data["last_daily"].get(str(user_id))
        if last is None:
            return True, None
        elapsed = time.time() - last
        if elapsed >= 86400:
            return True, None
        remaining = 86400 - elapsed
        return False, remaining

    def claim_daily(self, user_id: int):
        self.data["last_daily"][str(user_id)] = time.time()
        self.add_balance(user_id, DAILY_AMOUNT)
        self.save()

    # ---- nonce (for provably fair) ----
    def next_nonce(self, user_id: int) -> int:
        n = self.data["nonce"].get(str(user_id), 0)
        self.data["nonce"][str(user_id)] = n + 1
        self.save()
        return n


store = DataStore(DATA_FILE)


def is_owner(user_id: int) -> bool:
    return user_id in OWNER_IDS


def is_owner_or_admin(user_id: int) -> bool:
    return user_id in OWNER_IDS or user_id in ADMIN_IDS


# ==============================================================================
# PROVABLY FAIR RNG SYSTEM
# ==============================================================================

class FairRound:
    """
    Server-seed / client-seed / nonce HMAC system.
    The server seed hash is shown before the round. The real seed is only
    revealed after, so the player can recompute and verify the result.
    """

    __slots__ = ("server_seed", "server_seed_hash", "client_seed", "nonce")

    def __init__(self, client_seed: str = None, nonce: int = 0):
        self.server_seed = secrets.token_hex(32)
        self.server_seed_hash = hashlib.sha256(self.server_seed.encode()).hexdigest()
        self.client_seed = client_seed or secrets.token_hex(8)
        self.nonce = nonce

    def _hmac_hex(self) -> str:
        message = f"{self.client_seed}:{self.nonce}".encode()
        return hmac.new(self.server_seed.encode(), message, hashlib.sha256).hexdigest()

    def roll_float(self) -> float:
        digest = self._hmac_hex()
        int_value = int(digest[:13], 16)
        max_value = 16 ** 13
        return int_value / max_value

    def roll_coinflip(self) -> str:
        return "heads" if self.roll_float() < 0.5 else "tails"

    def roll_dice(self, sides: int = 6) -> int:
        return int(self.roll_float() * sides) + 1

    def roll_card_sequence(self, count: int):
        """Generates a deterministic sequence of card draws (0-51) from a single round."""
        cards = []
        message_base = f"{self.client_seed}:{self.nonce}"
        for i in range(count):
            digest = hmac.new(
                self.server_seed.encode(),
                f"{message_base}:{i}".encode(),
                hashlib.sha256,
            ).hexdigest()
            value = int(digest[:13], 16) / (16 ** 13)
            cards.append(int(value * 52))
        return cards

    def reveal_dict(self) -> dict:
        return {
            "server_seed": self.server_seed,
            "server_seed_hash": self.server_seed_hash,
            "client_seed": self.client_seed,
            "nonce": self.nonce,
        }


def verify_round(server_seed: str, client_seed: str, nonce: int, expected_result: str, game: str = "coinflip") -> dict:
    message = f"{client_seed}:{nonce}".encode()
    digest = hmac.new(server_seed.encode(), message, hashlib.sha256).hexdigest()
    int_value = int(digest[:13], 16)
    roll = int_value / (16 ** 13)

    if game == "coinflip":
        computed_result = "heads" if roll < 0.5 else "tails"
    elif game == "dice":
        computed_result = str(int(roll * 6) + 1)
    else:
        computed_result = str(roll)

    return {
        "computed_result": computed_result,
        "matches_claim": computed_result == expected_result,
        "raw_roll": roll,
        "hmac_digest": digest,
    }


class FairnessView(discord.ui.View):
    """Attached to every game result. Lets the player verify the round."""

    def __init__(self, fair_round: FairRound, game: str, result: str):
        super().__init__(timeout=600)
        self.fair_round = fair_round
        self.game = game
        self.result = result

    @discord.ui.button(label="Fairness", style=discord.ButtonStyle.secondary, emoji="🔍")
    async def fairness(self, interaction: discord.Interaction, button: discord.ui.Button):
        reveal = self.fair_round.reveal_dict()
        check = verify_round(
            server_seed=reveal["server_seed"],
            client_seed=reveal["client_seed"],
            nonce=reveal["nonce"],
            expected_result=self.result,
            game=self.game,
        )
        embed = discord.Embed(
            title="🔍 Provably Fair Verification",
            description=(
                "This round's outcome was generated before the bet was final, "
                "and can be independently recomputed by anyone using the values below."
            ),
            color=NEUTRAL_COLOR,
        )
        embed.add_field(name="Server Seed (revealed)", value=f"`{reveal['server_seed']}`", inline=False)
        embed.add_field(name="Server Seed Hash (shown pre-round)", value=f"`{reveal['server_seed_hash']}`", inline=False)
        embed.add_field(name="Client Seed", value=f"`{reveal['client_seed']}`", inline=True)
        embed.add_field(name="Nonce", value=str(reveal["nonce"]), inline=True)
        embed.add_field(
            name="Verified Result",
            value=f"Computed: **{check['computed_result']}** • Matches: **{check['matches_claim']}**",
            inline=False,
        )
        embed.set_footer(text="Recompute with HMAC-SHA256(server_seed, client_seed:nonce)")
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ==============================================================================
# COINFLIP GIF GENERATOR
# ==============================================================================

CANVAS_SIZE = 300
COIN_RADIUS = 95
HEADS_COLOR = (216, 90, 90)
TAILS_COLOR = (90, 130, 216)
EDGE_DARK_HEADS = (150, 50, 50)
EDGE_DARK_TAILS = (50, 80, 150)
TEXT_COLOR = (255, 255, 255)


def _draw_coin_frame(squash_factor, face):
    img = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx, cy = CANVAS_SIZE // 2, CANVAS_SIZE // 2

    ellipse_width = max(6, int(COIN_RADIUS * 2 * squash_factor))
    ellipse_height = COIN_RADIUS * 2

    fill_color = HEADS_COLOR if face == "H" else TAILS_COLOR
    edge_color = EDGE_DARK_HEADS if face == "H" else EDGE_DARK_TAILS

    bbox = [cx - ellipse_width // 2, cy - ellipse_height // 2, cx + ellipse_width // 2, cy + ellipse_height // 2]

    if squash_factor > 0.12:
        edge_bbox = [bbox[0] - 4, bbox[1], bbox[2] + 4, bbox[3]]
        draw.ellipse(edge_bbox, fill=edge_color)
        draw.ellipse(bbox, fill=fill_color)

        if squash_factor > 0.55:
            font = load_font(int(70 * squash_factor))
            tw, th = draw.textbbox((0, 0), face, font=font)[2:]
            draw.text((cx - tw / 2, cy - th / 2 - 6), face, font=font, fill=TEXT_COLOR)

        highlight_bbox = [cx - ellipse_width // 4, cy - ellipse_height // 3, cx, cy - ellipse_height // 6]
        draw.ellipse(highlight_bbox, fill=(255, 255, 255, 40))
    else:
        draw.line([(cx, cy - COIN_RADIUS), (cx, cy + COIN_RADIUS)], fill=edge_color, width=8)

    return img


def generate_coinflip_gif(result: str, output_path="coinflip_result.gif"):
    result_face = "H" if result.lower() == "heads" else "T"
    frames, durations = [], []

    total_spins = 4
    total_frames_spin = 36
    for i in range(total_frames_spin):
        progress = i / total_frames_spin
        angle = progress * total_spins * 2 * math.pi
        squash = abs(math.cos(angle))
        face_showing = "H" if (math.cos(angle) >= 0) else "T"
        frames.append(_draw_coin_frame(max(squash, 0.05), face_showing))
        durations.append(int(20 * (1 + progress * 3)))

    settle_frames = 8
    for i in range(settle_frames):
        progress = i / settle_frames
        squash = 1.0 - (1.0 - progress) * 0.3
        frames.append(_draw_coin_frame(squash, result_face))
        durations.append(60)

    frames.append(_draw_coin_frame(1.0, result_face))
    durations.append(1800)

    frames_rgb = []
    for f in frames:
        bg = Image.new("RGBA", f.size, (35, 35, 40, 255))
        bg.alpha_composite(f)
        frames_rgb.append(bg.convert("RGB"))

    frames_rgb[0].save(
        output_path, save_all=True, append_images=frames_rgb[1:], duration=durations, loop=1, optimize=False,
    )
    return output_path


# ==============================================================================
# DICE GIF GENERATOR
# ==============================================================================

def _draw_die_frame(value, size=240, rotation=0):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = 20
    draw.rounded_rectangle([pad, pad, size - pad, size - pad], radius=28, fill=(235, 235, 240, 255), outline=(40, 40, 45), width=4)

    pip_r = 12
    positions = {
        1: [(0.5, 0.5)],
        2: [(0.28, 0.28), (0.72, 0.72)],
        3: [(0.28, 0.28), (0.5, 0.5), (0.72, 0.72)],
        4: [(0.28, 0.28), (0.72, 0.28), (0.28, 0.72), (0.72, 0.72)],
        5: [(0.28, 0.28), (0.72, 0.28), (0.5, 0.5), (0.28, 0.72), (0.72, 0.72)],
        6: [(0.28, 0.25), (0.72, 0.25), (0.28, 0.5), (0.72, 0.5), (0.28, 0.75), (0.72, 0.75)],
    }
    for (px, py) in positions.get(value, []):
        x = pad + (size - 2 * pad) * px
        y = pad + (size - 2 * pad) * py
        draw.ellipse([x - pip_r, y - pip_r, x + pip_r, y + pip_r], fill=(30, 30, 35, 255))

    if rotation:
        img = img.rotate(rotation, resample=Image.BICUBIC, expand=False)
    return img


def generate_dice_gif(result: int, output_path="dice_result.gif"):
    frames, durations = [], []
    roll_frames = 18
    for i in range(roll_frames):
        progress = i / roll_frames
        fake_value = random.randint(1, 6)
        rotation = (1 - progress) * random.choice([-1, 1]) * 25
        frames.append(_draw_die_frame(fake_value, rotation=rotation))
        durations.append(int(30 + progress * 60))

    settle_frames = 5
    for i in range(settle_frames):
        wobble = (settle_frames - i) * 2 * random.choice([-1, 1])
        frames.append(_draw_die_frame(result, rotation=wobble))
        durations.append(70)

    frames.append(_draw_die_frame(result, rotation=0))
    durations.append(1800)

    frames_rgb = []
    for f in frames:
        bg = Image.new("RGBA", f.size, (35, 35, 40, 255))
        bg.alpha_composite(f)
        frames_rgb.append(bg.convert("RGB"))

    frames_rgb[0].save(
        output_path, save_all=True, append_images=frames_rgb[1:], duration=durations, loop=1, optimize=False,
    )
    return output_path


# ==============================================================================
# BLACKJACK LOGIC (Dealer vs Player only)
# ==============================================================================

RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
SUITS = ["♠", "♥", "♦", "♣"]


def card_index_to_name(index: int) -> str:
    rank = RANKS[index % 13]
    suit = SUITS[index % 4]
    return f"{rank}{suit}"


def card_value(index: int) -> int:
    rank = RANKS[index % 13]
    if rank == "A":
        return 11
    if rank in ("J", "Q", "K"):
        return 10
    return int(rank)


def hand_total(card_indexes):
    total = sum(card_value(c) for c in card_indexes)
    aces = sum(1 for c in card_indexes if RANKS[c % 13] == "A")
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    return total


def format_hand(card_indexes):
    return " ".join(card_index_to_name(c) for c in card_indexes)


class BlackjackView(discord.ui.View):
    """Hit / Stand buttons for an active dealer-vs-player blackjack round."""

    def __init__(self, store: DataStore, fair_round: FairRound, player_id: int,
                 player_cards, dealer_cards, deck_cursor, amount, message_ref):
        super().__init__(timeout=120)
        self.store = store
        self.fair_round = fair_round
        self.player_id = player_id
        self.player_cards = player_cards
        self.dealer_cards = dealer_cards
        self.deck_cursor = deck_cursor
        self.amount = amount
        self.message_ref = message_ref
        self.finished = False

    def _draw_card(self):
        """Pulls the next card from the deterministic fair sequence."""
        sequence = self.fair_round.roll_card_sequence(self.deck_cursor + 1)
        card = sequence[self.deck_cursor]
        self.deck_cursor += 1
        return card

    def _base_embed(self, title, color=NEUTRAL_COLOR):
        embed = discord.Embed(title=title, description=SERVER_NAME, color=color)
        embed.add_field(name=f"Your Hand ({hand_total(self.player_cards)})", value=format_hand(self.player_cards), inline=False)
        embed.add_field(name=f"Dealer Hand ({hand_total(self.dealer_cards)})", value=format_hand(self.dealer_cards), inline=False)
        embed.add_field(name="Bet", value=f"{fmt_gems(self.amount)} 💎", inline=True)
        embed.set_footer(text="GemCasino | Blackjack (Dealer)")
        return embed

    async def _finish(self, interaction: discord.Interaction, result: str):
        """result: 'win', 'lose', 'push', 'blackjack'"""
        self.finished = True
        for child in self.children:
            child.disabled = True

        player_bal = self.store.get_balance(self.player_id)

        if result == "blackjack":
            winnings = int(self.amount * BLACKJACK_NATURAL_MULTIPLIER)
            self.store.add_balance(self.player_id, winnings)
            self.store.record_result(self.player_id, True, self.amount)
            embed = self._base_embed("🃏 Blackjack! You won!", WIN_COLOR)
            embed.add_field(name="Winnings", value=f"{fmt_gems(winnings)} 💎 (natural blackjack bonus)", inline=False)
        elif result == "win":
            winnings = int(self.amount * BLACKJACK_PAYOUT_MULTIPLIER)
            self.store.add_balance(self.player_id, winnings)
            self.store.record_result(self.player_id, True, self.amount)
            embed = self._base_embed("🃏 You won!", WIN_COLOR)
            embed.add_field(name="Winnings", value=f"{fmt_gems(winnings)} 💎", inline=False)
        elif result == "push":
            self.store.add_balance(self.player_id, self.amount)
            embed = self._base_embed("🃏 Push — bet returned", NEUTRAL_COLOR)
        else:
            self.store.record_result(self.player_id, False, self.amount)
            embed = self._base_embed("🃏 You lost!", LOSS_COLOR)
            embed.add_field(name="Lost", value=f"{fmt_gems(self.amount)} 💎", inline=False)

        fairness_view = FairnessView(self.fair_round, game="blackjack", result=result)
        await interaction.response.edit_message(embed=embed, view=fairness_view)

    async def _dealer_play(self, interaction: discord.Interaction):
        while hand_total(self.dealer_cards) < 17:
            self.dealer_cards.append(self._draw_card())

        player_total = hand_total(self.player_cards)
        dealer_total = hand_total(self.dealer_cards)

        if dealer_total > 21 or player_total > dealer_total:
            await self._finish(interaction, "win")
        elif player_total < dealer_total:
            await self._finish(interaction, "lose")
        else:
            await self._finish(interaction, "push")

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary, emoji="➕")
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.player_id or self.finished:
            await interaction.response.send_message("This isn't your game.", ephemeral=True)
            return

        self.player_cards.append(self._draw_card())
        total = hand_total(self.player_cards)

        if total > 21:
            await self._finish(interaction, "lose")
            return

        embed = self._base_embed("🃏 Blackjack — Your Turn")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary, emoji="✋")
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.player_id or self.finished:
            await interaction.response.send_message("This isn't your game.", ephemeral=True)
            return
        await self._dealer_play(interaction)


# ==============================================================================
# MINES LOGIC
# ==============================================================================

GRID_SIZE = 25


def generate_mine_positions(fair_round: FairRound, mine_count: int):
    sequence = fair_round.roll_card_sequence(GRID_SIZE)
    indexed = sorted(range(GRID_SIZE), key=lambda i: sequence[i])
    return set(indexed[:mine_count])


def mines_multiplier(mines: int, safe_revealed: int) -> float:
    safe_total = GRID_SIZE - mines
    if safe_revealed == 0:
        return 1.0
    multiplier = 1.0
    remaining_safe = safe_total
    remaining_total = GRID_SIZE
    for _ in range(safe_revealed):
        multiplier *= (remaining_total / remaining_safe) * 0.97
        remaining_total -= 1
        remaining_safe -= 1
    return multiplier


class MinesView(discord.ui.View):
    """5x5 grid of buttons. Player reveals tiles, cashes out anytime before hitting a mine."""

    def __init__(self, store: DataStore, fair_round: FairRound, player_id: int, amount: int, mine_count: int):
        super().__init__(timeout=180)
        self.store = store
        self.fair_round = fair_round
        self.player_id = player_id
        self.amount = amount
        self.mine_count = mine_count
        self.mine_positions = generate_mine_positions(fair_round, mine_count)
        self.revealed = set()
        self.finished = False

        for i in range(GRID_SIZE):
            row, col = divmod(i, 5)
            btn = discord.ui.Button(label="?", style=discord.ButtonStyle.secondary, row=row)
            btn.callback = self._make_callback(i)
            self.add_item(btn)

        self.cashout_btn = discord.ui.Button(label="Cash Out", style=discord.ButtonStyle.success, emoji="💰", row=4)
        self.cashout_btn.callback = self._cashout
        self.remove_item(self.cashout_btn)
        self.add_item(self.cashout_btn)

    def _make_callback(self, index):
        async def callback(interaction: discord.Interaction):
            await self._reveal(interaction, index)
        return callback

    def _current_multiplier(self):
        return mines_multiplier(self.mine_count, len(self.revealed))

    async def _reveal(self, interaction: discord.Interaction, index: int):
        if interaction.user.id != self.player_id or self.finished:
            await interaction.response.send_message("This isn't your game.", ephemeral=True)
            return
        if index in self.revealed:
            await interaction.response.defer()
            return

        self.revealed.add(index)

        if index in self.mine_positions:
            await self._end_game(interaction, hit_mine=True)
            return

        self._refresh_buttons()
        multiplier = self._current_multiplier()
        embed = discord.Embed(
            title="💣 Mines",
            description=f"{SERVER_NAME}\nSafe tiles revealed: **{len(self.revealed)}** / {GRID_SIZE - self.mine_count}",
            color=NEUTRAL_COLOR,
        )
        embed.add_field(name="Bet", value=f"{fmt_gems(self.amount)} 💎", inline=True)
        embed.add_field(name="Mines", value=str(self.mine_count), inline=True)
        embed.add_field(name="Current Multiplier", value=f"{multiplier:.2f}x", inline=True)
        embed.add_field(name="Cash Out Value", value=f"{fmt_gems(int(self.amount * multiplier))} 💎", inline=False)
        embed.set_footer(text="GemCasino | Mines")

        await interaction.response.edit_message(embed=embed, view=self)

    def _refresh_buttons(self):
        tile_buttons = [c for c in self.children if isinstance(c, discord.ui.Button) and c is not self.cashout_btn]
        for i, btn in enumerate(tile_buttons):
            if i in self.revealed:
                btn.label = "💎"
                btn.style = discord.ButtonStyle.success
                btn.disabled = True

    async def _end_game(self, interaction: discord.Interaction, hit_mine: bool):
        self.finished = True
        tile_buttons = [c for c in self.children if isinstance(c, discord.ui.Button) and c is not self.cashout_btn]
        for idx, child in enumerate(tile_buttons):
            child.disabled = True
            if idx in self.mine_positions:
                child.label = "💣"
                child.style = discord.ButtonStyle.danger
            elif idx in self.revealed:
                child.label = "💎"
                child.style = discord.ButtonStyle.success
        self.cashout_btn.disabled = True

        if hit_mine:
            self.store.record_result(self.player_id, False, self.amount)
            embed = discord.Embed(title="💥 You hit a mine!", description=SERVER_NAME, color=LOSS_COLOR)
            embed.add_field(name="Lost", value=f"{fmt_gems(self.amount)} 💎", inline=False)
        else:
            multiplier = self._current_multiplier()
            winnings = int(self.amount * multiplier)
            self.store.add_balance(self.player_id, winnings)
            self.store.record_result(self.player_id, True, self.amount)
            embed = discord.Embed(title="💰 Cashed Out!", description=SERVER_NAME, color=WIN_COLOR)
            embed.add_field(name="Multiplier", value=f"{multiplier:.2f}x", inline=True)
            embed.add_field(name="Winnings", value=f"{fmt_gems(winnings)} 💎", inline=True)

        embed.add_field(name="Mines", value=str(self.mine_count), inline=False)
        embed.set_footer(text="GemCasino | Mines")

        fairness_view = FairnessView(self.fair_round, game="mines", result=str(hit_mine))
        await interaction.response.edit_message(embed=embed, view=fairness_view)

    async def _cashout(self, interaction: discord.Interaction):
        if interaction.user.id != self.player_id or self.finished:
            await interaction.response.send_message("This isn't your game.", ephemeral=True)
            return
        if len(self.revealed) == 0:
            await interaction.response.send_message("Reveal at least one tile before cashing out.", ephemeral=True)
            return
        await self._end_game(interaction, hit_mine=False)


# ==============================================================================
# DISCORD BOT SETUP
# ==============================================================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Sync failed: {e}")


async def banned_guard(interaction: discord.Interaction) -> bool:
    if store.is_banned(interaction.user.id):
        await interaction.response.send_message("🚫 You are banned from using this bot.", ephemeral=True)
        return False
    reason = store.blacklist_reason(interaction.user.id)
    if reason:
        await interaction.response.send_message(f"🔒 You are blacklisted: {reason}", ephemeral=True)
        return False
    return True


# ------------------------------------------------------------------------------
# /balance
# ------------------------------------------------------------------------------

@bot.tree.command(name="balance", description="Check your gem balance")
async def balance_cmd(interaction: discord.Interaction):
    if not await banned_guard(interaction):
        return
    bal = store.get_balance(interaction.user.id)
    embed = discord.Embed(title="💎 Balance", description=f"{SERVER_NAME}", color=NEUTRAL_COLOR)
    embed.add_field(name=interaction.user.display_name, value=f"{fmt_gems(bal)} gems")
    await interaction.response.send_message(embed=embed)


# ------------------------------------------------------------------------------
# /profile
# ------------------------------------------------------------------------------

@bot.tree.command(name="profile", description="View your full profile and stats")
async def profile_cmd(interaction: discord.Interaction):
    if not await banned_guard(interaction):
        return
    user_id = interaction.user.id
    bal = store.get_balance(user_id)
    stats = store.get_stats(user_id)
    total_games = stats["wins"] + stats["losses"]
    win_rate = (stats["wins"] / total_games * 100) if total_games else 0
    rank = get_rank(stats["wagered"])

    embed = discord.Embed(title=f"👤 {interaction.user.display_name}'s Profile", description=SERVER_NAME, color=GOLD_COLOR)
    embed.add_field(name="Balance", value=f"{fmt_gems(bal)} 💎", inline=True)
    embed.add_field(name="Rank", value=rank, inline=True)
    embed.add_field(name="Wins", value=str(stats["wins"]), inline=True)
    embed.add_field(name="Losses", value=str(stats["losses"]), inline=True)
    embed.add_field(name="Win Rate", value=f"{win_rate:.1f}%", inline=True)
    embed.add_field(name="Total Wagered", value=f"{fmt_gems(stats['wagered'])} 💎", inline=True)
    await interaction.response.send_message(embed=embed)


# ------------------------------------------------------------------------------
# /daily
# ------------------------------------------------------------------------------

@bot.tree.command(name="daily", description="Claim 10M free gems every 24 hours")
async def daily_cmd(interaction: discord.Interaction):
    if not await banned_guard(interaction):
        return
    can_claim, remaining = store.can_claim_daily(interaction.user.id)
    if not can_claim:
        hours = int(remaining // 3600)
        minutes = int((remaining % 3600) // 60)
        await interaction.response.send_message(
            f"⏳ You already claimed today. Try again in {hours}h {minutes}m.", ephemeral=True
        )
        return
    store.claim_daily(interaction.user.id)
    embed = discord.Embed(title="🎁 Daily Claim", description=SERVER_NAME, color=WIN_COLOR)
    embed.add_field(name="Claimed", value=f"{fmt_gems(DAILY_AMOUNT)} 💎")
    await interaction.response.send_message(embed=embed)


# ------------------------------------------------------------------------------
# /redeem
# ------------------------------------------------------------------------------

@bot.tree.command(name="redeem", description="Redeem a promo code for gems")
@app_commands.describe(code="The promo code to redeem")
async def redeem_cmd(interaction: discord.Interaction, code: str):
    if not await banned_guard(interaction):
        return
    gems, error = store.redeem_promo(interaction.user.id, code)
    if error:
        await interaction.response.send_message(f"❌ {error}", ephemeral=True)
        return
    embed = discord.Embed(title="🎟️ Code Redeemed!", description=SERVER_NAME, color=WIN_COLOR)
    embed.add_field(name="Code", value=code.upper(), inline=True)
    embed.add_field(name="Gems Received", value=f"{fmt_gems(gems)} 💎", inline=True)
    await interaction.response.send_message(embed=embed)


# ------------------------------------------------------------------------------
# /gift
# ------------------------------------------------------------------------------

@bot.tree.command(name="gift", description="Send gems to another player")
@app_commands.describe(user="Who to gift gems to", amount="How many gems to gift")
async def gift_cmd(interaction: discord.Interaction, user: discord.Member, amount: int):
    if not await banned_guard(interaction):
        return
    if store.is_command_locked("gift"):
        await interaction.response.send_message("🔒 Gifting is currently locked.", ephemeral=True)
        return
    if user.id == interaction.user.id:
        await interaction.response.send_message("You can't gift yourself.", ephemeral=True)
        return
    if amount <= 0:
        await interaction.response.send_message("Amount must be greater than zero.", ephemeral=True)
        return

    sender_bal = store.get_balance(interaction.user.id)
    if sender_bal < amount:
        await interaction.response.send_message("You don't have enough gems.", ephemeral=True)
        return

    tax = int(amount * GIFT_TAX_RATE)
    net = amount - tax

    store.set_balance(interaction.user.id, sender_bal - amount)
    store.add_balance(user.id, net)
    store.add_gift_tax(tax)

    embed = discord.Embed(title="🎁 Gift Sent!", description=SERVER_NAME, color=WIN_COLOR)
    embed.add_field(name="To", value=user.display_name, inline=True)
    embed.add_field(name="Amount Sent", value=f"{fmt_gems(net)} 💎", inline=True)
    embed.add_field(name="Gift Tax (2%)", value=f"{fmt_gems(tax)} 💎", inline=True)
    await interaction.response.send_message(embed=embed)


# ------------------------------------------------------------------------------
# /coinflip pvp and /coinflip computer
# ------------------------------------------------------------------------------

coinflip_group = app_commands.Group(name="coinflip", description="Coinflip game modes")


@coinflip_group.command(name="pvp", description="Challenge another player to a PvP coinflip")
@app_commands.describe(opponent="The player you want to challenge", amount="Gem amount to wager", pick="Heads or Tails")
@app_commands.choices(pick=[
    app_commands.Choice(name="Heads", value="heads"),
    app_commands.Choice(name="Tails", value="tails"),
])
async def coinflip_pvp(interaction: discord.Interaction, opponent: discord.Member, amount: int, pick: app_commands.Choice[str]):
    if not await banned_guard(interaction):
        return
    if store.is_game_locked("coinflip"):
        await interaction.response.send_message("🔒 Coinflip is currently locked by an admin.", ephemeral=True)
        return

    challenger = interaction.user
    if opponent.id == challenger.id:
        await interaction.response.send_message("You can't challenge yourself.", ephemeral=True)
        return
    if amount <= 0:
        await interaction.response.send_message("Bet amount must be greater than zero.", ephemeral=True)
        return

    challenger_bal = store.get_balance(challenger.id)
    opponent_bal = store.get_balance(opponent.id)
    if challenger_bal < amount:
        await interaction.response.send_message("You don't have enough gems for that bet.", ephemeral=True)
        return
    if opponent_bal < amount:
        await interaction.response.send_message(f"{opponent.display_name} doesn't have enough gems.", ephemeral=True)
        return

    fair_round = FairRound(nonce=store.next_nonce(challenger.id))

    starting_embed = discord.Embed(title="🪙 Coinflip — PvP", description=SERVER_NAME, color=NEUTRAL_COLOR)
    starting_embed.add_field(name="Bet", value=f"{fmt_gems(amount)} 💎", inline=True)
    starting_embed.add_field(name="Challenger", value=challenger.display_name, inline=True)
    starting_embed.add_field(name="Pick", value=pick.name, inline=True)
    starting_embed.add_field(name="Server Seed Hash", value=f"`{fair_round.server_seed_hash[:24]}...`", inline=False)
    starting_embed.set_footer(text="GemCasino | Coinflip PvP")

    await interaction.response.send_message(embed=starting_embed)
    message = await interaction.original_response()

    outcome = fair_round.roll_coinflip()
    challenger_wins = (pick.value == outcome)
    tax = int(amount * PVP_TAX_RATE)
    winnings = (amount * 2) - tax

    gif_path = f"/tmp/coinflip_{interaction.id}.gif"
    await asyncio.to_thread(generate_coinflip_gif, result=outcome, output_path=gif_path)

    spinning_embed = discord.Embed(title="🪙 Coinflip — PvP", description=SERVER_NAME, color=NEUTRAL_COLOR)
    spinning_embed.set_image(url="attachment://coinflip.gif")
    spinning_embed.add_field(name="Bet", value=f"{fmt_gems(amount)} 💎", inline=True)
    spinning_embed.add_field(name="Pick", value=pick.name, inline=True)
    spinning_embed.set_footer(text="GemCasino | Coinflip PvP")
    await message.edit(embed=spinning_embed, attachments=[discord.File(gif_path, filename="coinflip.gif")])
    await asyncio.sleep(3.2)

    if challenger_wins:
        store.set_balance(challenger.id, challenger_bal - amount + winnings)
        store.set_balance(opponent.id, opponent_bal - amount)
        winner, loser = challenger, opponent
    else:
        store.set_balance(opponent.id, opponent_bal - amount + winnings)
        store.set_balance(challenger.id, challenger_bal - amount)
        winner, loser = opponent, challenger

    store.record_result(winner.id, True, amount)
    store.record_result(loser.id, False, amount)
    store.add_pvp_tax(tax)

    result_embed = discord.Embed(color=WIN_COLOR)
    result_embed.description = f"**{winner.display_name} won!**\nResult: **{outcome.upper()}**"
    result_embed.add_field(name="Bet", value=f"{fmt_gems(amount)} 💎", inline=True)
    result_embed.add_field(name="Winnings", value=f"{fmt_gems(winnings)} 💎", inline=True)
    result_embed.add_field(name="Tax (6%)", value=f"{fmt_gems(tax)} 💎", inline=True)
    result_embed.set_image(url="attachment://coinflip.gif")
    result_embed.set_footer(text=f"GemCasino | Coinflip PvP • Lost: {loser.display_name}")

    view = FairnessView(fair_round, game="coinflip", result=outcome)
    await message.edit(embed=result_embed, attachments=[discord.File(gif_path, filename="coinflip.gif")], view=view)


@coinflip_group.command(name="computer", description="Play coinflip solo against the house")
@app_commands.describe(amount="Gem amount to wager", pick="Heads or Tails")
@app_commands.choices(pick=[
    app_commands.Choice(name="Heads", value="heads"),
    app_commands.Choice(name="Tails", value="tails"),
])
async def coinflip_computer(interaction: discord.Interaction, amount: int, pick: app_commands.Choice[str]):
    if not await banned_guard(interaction):
        return
    if store.is_game_locked("coinflip"):
        await interaction.response.send_message("🔒 Coinflip is currently locked by an admin.", ephemeral=True)
        return

    player = interaction.user
    if amount <= 0:
        await interaction.response.send_message("Bet amount must be greater than zero.", ephemeral=True)
        return

    player_bal = store.get_balance(player.id)
    if player_bal < amount:
        await interaction.response.send_message("You don't have enough gems for that bet.", ephemeral=True)
        return

    # Bet is deducted up front. If the player loses, the bot pays nothing further.
    store.set_balance(player.id, player_bal - amount)

    fair_round = FairRound(nonce=store.next_nonce(player.id))

    starting_embed = discord.Embed(title="🪙 Coinflip — vs Computer", description=SERVER_NAME, color=NEUTRAL_COLOR)
    starting_embed.add_field(name="Bet", value=f"{fmt_gems(amount)} 💎", inline=True)
    starting_embed.add_field(name="Pick", value=pick.name, inline=True)
    starting_embed.add_field(name="Payout if win", value=f"{COMPUTER_COINFLIP_MULTIPLIER}x", inline=True)
    starting_embed.add_field(name="Server Seed Hash", value=f"`{fair_round.server_seed_hash[:24]}...`", inline=False)
    starting_embed.set_footer(text="GemCasino | Coinflip vs Computer")

    await interaction.response.send_message(embed=starting_embed)
    message = await interaction.original_response()

    outcome = fair_round.roll_coinflip()
    player_wins = (pick.value == outcome)
    winnings = int(amount * COMPUTER_COINFLIP_MULTIPLIER)

    gif_path = f"/tmp/coinflip_cpu_{interaction.id}.gif"
    await asyncio.to_thread(generate_coinflip_gif, result=outcome, output_path=gif_path)

    spinning_embed = discord.Embed(title="🪙 Coinflip — vs Computer", description=SERVER_NAME, color=NEUTRAL_COLOR)
    spinning_embed.set_image(url="attachment://coinflip.gif")
    spinning_embed.add_field(name="Bet", value=f"{fmt_gems(amount)} 💎", inline=True)
    spinning_embed.add_field(name="Pick", value=pick.name, inline=True)
    spinning_embed.set_footer(text="GemCasino | Coinflip vs Computer")
    await message.edit(embed=spinning_embed, attachments=[discord.File(gif_path, filename="coinflip.gif")])
    await asyncio.sleep(3.2)

    if player_wins:
        store.add_balance(player.id, winnings)
        store.record_result(player.id, True, amount)
        result_embed = discord.Embed(color=WIN_COLOR)
        result_embed.description = f"**You won!**\nResult: **{outcome.upper()}**"
        result_embed.add_field(name="Bet", value=f"{fmt_gems(amount)} 💎", inline=True)
        result_embed.add_field(name="Winnings", value=f"{fmt_gems(winnings)} 💎", inline=True)
        result_embed.add_field(name="Payout", value=f"{COMPUTER_COINFLIP_MULTIPLIER}x", inline=True)
    else:
        store.record_result(player.id, False, amount)
        result_embed = discord.Embed(color=LOSS_COLOR)
        result_embed.description = f"**You lost!**\nResult: **{outcome.upper()}**"
        result_embed.add_field(name="Bet", value=f"{fmt_gems(amount)} 💎", inline=True)
        result_embed.add_field(name="Lost", value=f"{fmt_gems(amount)} 💎", inline=True)

    result_embed.set_image(url="attachment://coinflip.gif")
    result_embed.set_footer(text="GemCasino | Coinflip vs Computer")

    view = FairnessView(fair_round, game="coinflip", result=outcome)
    await message.edit(embed=result_embed, attachments=[discord.File(gif_path, filename="coinflip.gif")], view=view)


bot.tree.add_command(coinflip_group)


# ------------------------------------------------------------------------------
# /dice  (PvP — highest roll wins)
# ------------------------------------------------------------------------------

@bot.tree.command(name="dice", description="Roll dice against another player, highest roll wins")
@app_commands.describe(opponent="The player you want to challenge", amount="Gem amount to wager")
async def dice_cmd(interaction: discord.Interaction, opponent: discord.Member, amount: int):
    if not await banned_guard(interaction):
        return
    if store.is_game_locked("dice"):
        await interaction.response.send_message("🔒 Dice is currently locked by an admin.", ephemeral=True)
        return

    challenger = interaction.user
    if opponent.id == challenger.id:
        await interaction.response.send_message("You can't challenge yourself.", ephemeral=True)
        return
    if amount <= 0:
        await interaction.response.send_message("Bet amount must be greater than zero.", ephemeral=True)
        return

    challenger_bal = store.get_balance(challenger.id)
    opponent_bal = store.get_balance(opponent.id)
    if challenger_bal < amount or opponent_bal < amount:
        await interaction.response.send_message("One of you doesn't have enough gems.", ephemeral=True)
        return

    fair_round = FairRound(nonce=store.next_nonce(challenger.id))

    embed = discord.Embed(title="🎲 Dice — PvP", description=SERVER_NAME, color=NEUTRAL_COLOR)
    embed.add_field(name="Bet", value=f"{fmt_gems(amount)} 💎", inline=True)
    embed.add_field(name="Players", value=f"{challenger.display_name} vs {opponent.display_name}", inline=True)
    await interaction.response.send_message(embed=embed)
    message = await interaction.original_response()

    challenger_roll = fair_round.roll_dice()
    fair_round_2 = FairRound(client_seed=fair_round.client_seed, nonce=fair_round.nonce + 100000)
    fair_round_2.server_seed = fair_round.server_seed
    fair_round_2.server_seed_hash = fair_round.server_seed_hash
    opponent_roll = fair_round_2.roll_dice()

    tax = int(amount * PVP_TAX_RATE)
    winnings = (amount * 2) - tax

    gif_path = f"/tmp/dice_{interaction.id}.gif"
    higher_roll = max(challenger_roll, opponent_roll)
    await asyncio.to_thread(generate_dice_gif, result=higher_roll, output_path=gif_path)

    await asyncio.sleep(1.5)

    if challenger_roll == opponent_roll:
        result_embed = discord.Embed(title="🎲 It's a tie! Bets returned.", color=NEUTRAL_COLOR)
        result_embed.add_field(name=f"{challenger.display_name}", value=f"Rolled {challenger_roll}", inline=True)
        result_embed.add_field(name=f"{opponent.display_name}", value=f"Rolled {opponent_roll}", inline=True)
        await message.edit(embed=result_embed, attachments=[discord.File(gif_path, filename="dice.gif")])
        return

    if challenger_roll > opponent_roll:
        winner, loser, w_roll, l_roll = challenger, opponent, challenger_roll, opponent_roll
        store.set_balance(challenger.id, challenger_bal - amount + winnings)
        store.set_balance(opponent.id, opponent_bal - amount)
    else:
        winner, loser, w_roll, l_roll = opponent, challenger, opponent_roll, challenger_roll
        store.set_balance(opponent.id, opponent_bal - amount + winnings)
        store.set_balance(challenger.id, challenger_bal - amount)

    store.record_result(winner.id, True, amount)
    store.record_result(loser.id, False, amount)
    store.add_pvp_tax(tax)

    result_embed = discord.Embed(title=f"🎲 {winner.display_name} won!", color=WIN_COLOR)
    result_embed.add_field(name=f"{winner.display_name}", value=f"Rolled {w_roll}", inline=True)
    result_embed.add_field(name=f"{loser.display_name}", value=f"Rolled {l_roll}", inline=True)
    result_embed.add_field(name="Winnings", value=f"{fmt_gems(winnings)} 💎 (6% tax: {fmt_gems(tax)})", inline=False)
    result_embed.set_image(url="attachment://dice.gif")
    result_embed.set_footer(text="GemCasino | Dice PvP")

    view = FairnessView(fair_round, game="dice", result=str(challenger_roll))
    await message.edit(embed=result_embed, attachments=[discord.File(gif_path, filename="dice.gif")], view=view)


# ------------------------------------------------------------------------------
# /blackjack  (Dealer vs Player only)
# ------------------------------------------------------------------------------

@bot.tree.command(name="blackjack", description="Play blackjack against the dealer")
@app_commands.describe(amount="Gem amount to wager")
async def blackjack_cmd(interaction: discord.Interaction, amount: int):
    if not await banned_guard(interaction):
        return
    if store.is_game_locked("blackjack"):
        await interaction.response.send_message("🔒 Blackjack is currently locked by an admin.", ephemeral=True)
        return

    player = interaction.user
    if amount <= 0:
        await interaction.response.send_message("Bet amount must be greater than zero.", ephemeral=True)
        return

    player_bal = store.get_balance(player.id)
    if player_bal < amount:
        await interaction.response.send_message("You don't have enough gems for that bet.", ephemeral=True)
        return

    store.set_balance(player.id, player_bal - amount)

    fair_round = FairRound(nonce=store.next_nonce(player.id))
    full_sequence = fair_round.roll_card_sequence(4)
    player_cards = [full_sequence[0], full_sequence[1]]
    dealer_cards = [full_sequence[2], full_sequence[3]]
    deck_cursor = 4

    player_total = hand_total(player_cards)
    dealer_total = hand_total(dealer_cards)

    if player_total == 21:
        store.add_balance(player.id, int(amount * BLACKJACK_NATURAL_MULTIPLIER))
        store.record_result(player.id, True, amount)
        embed = discord.Embed(title="🃏 Blackjack! You won!", description=SERVER_NAME, color=WIN_COLOR)
        embed.add_field(name=f"Your Hand ({player_total})", value=format_hand(player_cards), inline=False)
        embed.add_field(name=f"Dealer Hand ({dealer_total})", value=format_hand(dealer_cards), inline=False)
        embed.add_field(name="Winnings", value=f"{fmt_gems(int(amount * BLACKJACK_NATURAL_MULTIPLIER))} 💎", inline=False)
        view = FairnessView(fair_round, game="blackjack", result="blackjack")
        await interaction.response.send_message(embed=embed, view=view)
        return

    embed = discord.Embed(title="🃏 Blackjack — Your Turn", description=SERVER_NAME, color=NEUTRAL_COLOR)
    embed.add_field(name=f"Your Hand ({player_total})", value=format_hand(player_cards), inline=False)
    embed.add_field(name=f"Dealer Hand ({dealer_total})", value=format_hand(dealer_cards), inline=False)
    embed.add_field(name="Bet", value=f"{fmt_gems(amount)} 💎", inline=True)
    embed.set_footer(text="GemCasino | Blackjack (Dealer)")

    await interaction.response.send_message(embed=embed)
    message = await interaction.original_response()

    view = BlackjackView(store, fair_round, player.id, player_cards, dealer_cards, deck_cursor, amount, message)
    await message.edit(view=view)


# ------------------------------------------------------------------------------
# /mines computer  and  /mines pvp
# ------------------------------------------------------------------------------

mines_group = app_commands.Group(name="mines", description="Mines game modes")


@mines_group.command(name="computer", description="Play mines vs the computer")
@app_commands.describe(amount="Gem amount to wager", mine_count="Number of mines (minimum 3)")
async def mines_computer(interaction: discord.Interaction, amount: int, mine_count: int):
    if not await banned_guard(interaction):
        return
    if store.is_game_locked("mines"):
        await interaction.response.send_message("🔒 Mines is currently locked by an admin.", ephemeral=True)
        return
    if mine_count < 3:
        await interaction.response.send_message("Minimum 3 mines required.", ephemeral=True)
        return
    if mine_count >= GRID_SIZE:
        await interaction.response.send_message("Too many mines for the grid size.", ephemeral=True)
        return
    if amount <= 0:
        await interaction.response.send_message("Bet amount must be greater than zero.", ephemeral=True)
        return

    player_bal = store.get_balance(interaction.user.id)
    if player_bal < amount:
        await interaction.response.send_message("You don't have enough gems for that bet.", ephemeral=True)
        return

    store.set_balance(interaction.user.id, player_bal - amount)

    fair_round = FairRound(nonce=store.next_nonce(interaction.user.id))

    embed = discord.Embed(
        title="💣 Mines",
        description=f"{SERVER_NAME}\nPick a tile to reveal. Cash out anytime before hitting a mine!",
        color=NEUTRAL_COLOR,
    )
    embed.add_field(name="Bet", value=f"{fmt_gems(amount)} 💎", inline=True)
    embed.add_field(name="Mines", value=str(mine_count), inline=True)
    embed.set_footer(text="GemCasino | Mines")

    view = MinesView(store, fair_round, interaction.user.id, amount, mine_count)
    await interaction.response.send_message(embed=embed, view=view)


@mines_group.command(name="pvp", description="Play mines against another player")
@app_commands.describe(opponent="The player you want to challenge", amount="Gem amount to wager")
async def mines_pvp(interaction: discord.Interaction, opponent: discord.Member, amount: int):
    if not await banned_guard(interaction):
        return
    if store.is_game_locked("mines"):
        await interaction.response.send_message("🔒 Mines is currently locked by an admin.", ephemeral=True)
        return
    if opponent.id == interaction.user.id:
        await interaction.response.send_message("You can't challenge yourself.", ephemeral=True)
        return
    if amount <= 0:
        await interaction.response.send_message("Bet amount must be greater than zero.", ephemeral=True)
        return

    challenger_bal = store.get_balance(interaction.user.id)
    opponent_bal = store.get_balance(opponent.id)
    if challenger_bal < amount or opponent_bal < amount:
        await interaction.response.send_message("One of you doesn't have enough gems.", ephemeral=True)
        return

    fair_round = FairRound(nonce=store.next_nonce(interaction.user.id))
    mine_count = 5
    positions = generate_mine_positions(fair_round, mine_count)

    challenger_safe = sum(1 for i in range(0, 12) if i not in positions)
    opponent_safe = sum(1 for i in range(12, 24) if i not in positions)

    tax = int(amount * PVP_TAX_RATE)
    winnings = (amount * 2) - tax

    if challenger_safe == opponent_safe:
        embed = discord.Embed(title="💣 Mines PvP — Tie! Bets returned.", color=NEUTRAL_COLOR)
    elif challenger_safe > opponent_safe:
        store.set_balance(interaction.user.id, challenger_bal - amount + winnings)
        store.set_balance(opponent.id, opponent_bal - amount)
        store.record_result(interaction.user.id, True, amount)
        store.record_result(opponent.id, False, amount)
        store.add_pvp_tax(tax)
        embed = discord.Embed(title=f"💣 {interaction.user.display_name} won!", color=WIN_COLOR)
        embed.add_field(name="Winnings", value=f"{fmt_gems(winnings)} 💎 (6% tax: {fmt_gems(tax)})")
    else:
        store.set_balance(opponent.id, opponent_bal - amount + winnings)
        store.set_balance(interaction.user.id, challenger_bal - amount)
        store.record_result(opponent.id, True, amount)
        store.record_result(interaction.user.id, False, amount)
        store.add_pvp_tax(tax)
        embed = discord.Embed(title=f"💣 {opponent.display_name} won!", color=WIN_COLOR)
        embed.add_field(name="Winnings", value=f"{fmt_gems(winnings)} 💎 (6% tax: {fmt_gems(tax)})")

    embed.set_footer(text="GemCasino | Mines PvP")
    view = FairnessView(fair_round, game="mines_pvp", result=f"{challenger_safe}-{opponent_safe}")
    await interaction.response.send_message(embed=embed, view=view)


bot.tree.add_command(mines_group)


# ------------------------------------------------------------------------------
# /rps  (Rock Paper Scissors PvP)
# ------------------------------------------------------------------------------

class RPSView(discord.ui.View):
    def __init__(self, challenger_id, opponent_id, amount):
        super().__init__(timeout=60)
        self.challenger_id = challenger_id
        self.opponent_id = opponent_id
        self.amount = amount
        self.choices = {}

    async def _handle_choice(self, interaction: discord.Interaction, choice: str):
        if interaction.user.id not in (self.challenger_id, self.opponent_id):
            await interaction.response.send_message("This isn't your game.", ephemeral=True)
            return
        if interaction.user.id in self.choices:
            await interaction.response.send_message("You already chose.", ephemeral=True)
            return

        self.choices[interaction.user.id] = choice
        await interaction.response.send_message(f"You picked **{choice}**.", ephemeral=True)

        if len(self.choices) == 2:
            await self._resolve(interaction)

    async def _resolve(self, interaction: discord.Interaction):
        c_choice = self.choices[self.challenger_id]
        o_choice = self.choices[self.opponent_id]

        beats = {"rock": "scissors", "paper": "rock", "scissors": "paper"}

        challenger_bal = store.get_balance(self.challenger_id)
        opponent_bal = store.get_balance(self.opponent_id)
        tax = int(self.amount * PVP_TAX_RATE)
        winnings = (self.amount * 2) - tax

        if c_choice == o_choice:
            embed = discord.Embed(title="✊ RPS — Tie! Bets returned.", color=NEUTRAL_COLOR)
        elif beats[c_choice] == o_choice:
            store.set_balance(self.challenger_id, challenger_bal - self.amount + winnings)
            store.set_balance(self.opponent_id, opponent_bal - self.amount)
            store.record_result(self.challenger_id, True, self.amount)
            store.record_result(self.opponent_id, False, self.amount)
            store.add_pvp_tax(tax)
            embed = discord.Embed(title="✊ Challenger won!", color=WIN_COLOR)
        else:
            store.set_balance(self.opponent_id, opponent_bal - self.amount + winnings)
            store.set_balance(self.challenger_id, challenger_bal - self.amount)
            store.record_result(self.opponent_id, True, self.amount)
            store.record_result(self.challenger_id, False, self.amount)
            store.add_pvp_tax(tax)
            embed = discord.Embed(title="✊ Opponent won!", color=WIN_COLOR)

        embed.add_field(name="Challenger picked", value=c_choice.capitalize(), inline=True)
        embed.add_field(name="Opponent picked", value=o_choice.capitalize(), inline=True)
        embed.set_footer(text="GemCasino | Rock Paper Scissors")

        for child in self.children:
            child.disabled = True

        await interaction.followup.send(embed=embed)

    @discord.ui.button(label="Rock", emoji="🪨", style=discord.ButtonStyle.secondary)
    async def rock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_choice(interaction, "rock")

    @discord.ui.button(label="Paper", emoji="📄", style=discord.ButtonStyle.secondary)
    async def paper(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_choice(interaction, "paper")

    @discord.ui.button(label="Scissors", emoji="✂️", style=discord.ButtonStyle.secondary)
    async def scissors(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_choice(interaction, "scissors")


@bot.tree.command(name="rps", description="Rock paper scissors PvP")
@app_commands.describe(opponent="The player you want to challenge", amount="Gem amount to wager")
async def rps_cmd(interaction: discord.Interaction, opponent: discord.Member, amount: int):
    if not await banned_guard(interaction):
        return
    if store.is_game_locked("rps"):
        await interaction.response.send_message("🔒 RPS is currently locked by an admin.", ephemeral=True)
        return
    if opponent.id == interaction.user.id:
        await interaction.response.send_message("You can't challenge yourself.", ephemeral=True)
        return
    if amount <= 0:
        await interaction.response.send_message("Bet amount must be greater than zero.", ephemeral=True)
        return

    challenger_bal = store.get_balance(interaction.user.id)
    opponent_bal = store.get_balance(opponent.id)
    if challenger_bal < amount or opponent_bal < amount:
        await interaction.response.send_message("One of you doesn't have enough gems.", ephemeral=True)
        return

    embed = discord.Embed(
        title="✊ Rock Paper Scissors",
        description=f"{interaction.user.display_name} vs {opponent.display_name}\nBoth players pick using the buttons below.",
        color=NEUTRAL_COLOR,
    )
    embed.add_field(name="Bet", value=f"{fmt_gems(amount)} 💎")
    view = RPSView(interaction.user.id, opponent.id, amount)
    await interaction.response.send_message(embed=embed, view=view)


# ------------------------------------------------------------------------------
# /race
# ------------------------------------------------------------------------------

@bot.tree.command(name="race", description="View the weekly leaderboard with prizes")
async def race_cmd(interaction: discord.Interaction):
    top = store.get_race(10)
    embed = discord.Embed(title="🏆 Weekly Race", description=SERVER_NAME, color=GOLD_COLOR)
    if not top:
        embed.description += "\n\nNo wagers placed yet this week."
    medals = ["🥇", "🥈", "🥉"]
    prizes = [500_000_000, 250_000_000, 100_000_000]
    for i, (user_id, wagered) in enumerate(top):
        medal = medals[i] if i < 3 else f"#{i+1}"
        prize_text = f" — Prize: {fmt_gems(prizes[i])} 💎" if i < 3 else ""
        embed.add_field(name=f"{medal} <@{user_id}>", value=f"Wagered: {fmt_gems(wagered)}{prize_text}", inline=False)
    await interaction.response.send_message(embed=embed)


# ==============================================================================
# OWNER / ADMIN COMMANDS
# ==============================================================================

@bot.tree.command(name="addgems", description="[Admin] Add gems to a player")
@app_commands.describe(user="Player to give gems to", amount="Amount to add")
async def addgems_cmd(interaction: discord.Interaction, user: discord.Member, amount: int):
    if not is_owner_or_admin(interaction.user.id):
        await interaction.response.send_message("🚫 No permission.", ephemeral=True)
        return
    store.add_balance(user.id, amount)
    await interaction.response.send_message(f"✅ Added {fmt_gems(amount)} gems to {user.display_name}.")


@bot.tree.command(name="removegems", description="[Admin] Remove gems from a player")
@app_commands.describe(user="Player to remove gems from", amount="Amount to remove")
async def removegems_cmd(interaction: discord.Interaction, user: discord.Member, amount: int):
    if not is_owner_or_admin(interaction.user.id):
        await interaction.response.send_message("🚫 No permission.", ephemeral=True)
        return
    current = store.get_balance(user.id)
    store.set_balance(user.id, current - amount)
    await interaction.response.send_message(f"✅ Removed {fmt_gems(amount)} gems from {user.display_name}.")


promo_group = app_commands.Group(name="promo", description="Manage promo codes")


@promo_group.command(name="add", description="[Admin] Add a new promo code")
@app_commands.describe(code="The code text", gems="Gems awarded on redemption")
async def promo_add(interaction: discord.Interaction, code: str, gems: int):
    if not is_owner_or_admin(interaction.user.id):
        await interaction.response.send_message("🚫 No permission.", ephemeral=True)
        return
    store.add_promo(code, gems)
    await interaction.response.send_message(f"✅ Promo `{code.upper()}` added — {fmt_gems(gems)} gems.")


@promo_group.command(name="remove", description="[Admin] Remove a promo code")
@app_commands.describe(code="The code to remove")
async def promo_remove(interaction: discord.Interaction, code: str):
    if not is_owner_or_admin(interaction.user.id):
        await interaction.response.send_message("🚫 No permission.", ephemeral=True)
        return
    store.remove_promo(code)
    await interaction.response.send_message(f"✅ Promo `{code.upper()}` removed.")


@promo_group.command(name="list", description="[Admin] List all promo codes")
async def promo_list(interaction: discord.Interaction):
    if not is_owner_or_admin(interaction.user.id):
        await interaction.response.send_message("🚫 No permission.", ephemeral=True)
        return
    promos = store.data["promos"]
    if not promos:
        await interaction.response.send_message("No promo codes exist yet.", ephemeral=True)
        return
    embed = discord.Embed(title="🎟️ Promo Codes", color=NEUTRAL_COLOR)
    for code, info in promos.items():
        status = "Active" if info["active"] else "Disabled"
        embed.add_field(name=code, value=f"{fmt_gems(info['gems'])} gems — {status}", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@promo_group.command(name="toggle", description="[Admin] Enable or disable a promo code")
@app_commands.describe(code="The code to toggle")
async def promo_toggle(interaction: discord.Interaction, code: str):
    if not is_owner_or_admin(interaction.user.id):
        await interaction.response.send_message("🚫 No permission.", ephemeral=True)
        return
    promo = store.toggle_promo(code)
    if not promo:
        await interaction.response.send_message("Code not found.", ephemeral=True)
        return
    status = "enabled" if promo["active"] else "disabled"
    await interaction.response.send_message(f"✅ Promo `{code.upper()}` {status}.")


bot.tree.add_command(promo_group)


@bot.tree.command(name="resetrace", description="[Admin] Reset the weekly race leaderboard")
async def resetrace_cmd(interaction: discord.Interaction):
    if not is_owner_or_admin(interaction.user.id):
        await interaction.response.send_message("🚫 No permission.", ephemeral=True)
        return
    store.reset_race()
    await interaction.response.send_message("✅ Weekly race leaderboard has been reset.")


@bot.tree.command(name="ban", description="[Admin] Ban a player from using the bot")
@app_commands.describe(user="Player to ban")
async def ban_cmd(interaction: discord.Interaction, user: discord.Member):
    if not is_owner_or_admin(interaction.user.id):
        await interaction.response.send_message("🚫 No permission.", ephemeral=True)
        return
    store.ban(user.id)
    await interaction.response.send_message(f"🚫 {user.display_name} has been banned.")


@bot.tree.command(name="unban", description="[Admin] Unban a player")
@app_commands.describe(user="Player to unban")
async def unban_cmd(interaction: discord.Interaction, user: discord.Member):
    if not is_owner_or_admin(interaction.user.id):
        await interaction.response.send_message("🚫 No permission.", ephemeral=True)
        return
    store.unban(user.id)
    await interaction.response.send_message(f"✅ {user.display_name} has been unbanned.")


@bot.tree.command(name="blacklist", description="[Admin] Blacklist a player with a reason")
@app_commands.describe(user="Player to blacklist", reason="Reason shown to the player")
async def blacklist_cmd(interaction: discord.Interaction, user: discord.Member, reason: str):
    if not is_owner_or_admin(interaction.user.id):
        await interaction.response.send_message("🚫 No permission.", ephemeral=True)
        return
    store.blacklist(user.id, reason)
    await interaction.response.send_message(f"🔒 {user.display_name} blacklisted: {reason}")


@bot.tree.command(name="unblacklist", description="[Admin] Remove a player from the blacklist")
@app_commands.describe(user="Player to remove from blacklist")
async def unblacklist_cmd(interaction: discord.Interaction, user: discord.Member):
    if not is_owner_or_admin(interaction.user.id):
        await interaction.response.send_message("🚫 No permission.", ephemeral=True)
        return
    store.unblacklist(user.id)
    await interaction.response.send_message(f"✅ {user.display_name} removed from blacklist.")


@bot.tree.command(name="lockcommand", description="[Admin] Lock a command so players can't use it")
@app_commands.describe(command="Command name to lock")
async def lockcommand_cmd(interaction: discord.Interaction, command: str):
    if not is_owner_or_admin(interaction.user.id):
        await interaction.response.send_message("🚫 No permission.", ephemeral=True)
        return
    store.lock_command(command)
    await interaction.response.send_message(f"🔒 Command `{command}` is now locked.")


@bot.tree.command(name="unlockcommand", description="[Admin] Unlock a command")
@app_commands.describe(command="Command name to unlock")
async def unlockcommand_cmd(interaction: discord.Interaction, command: str):
    if not is_owner_or_admin(interaction.user.id):
        await interaction.response.send_message("🚫 No permission.", ephemeral=True)
        return
    store.unlock_command(command)
    await interaction.response.send_message(f"✅ Command `{command}` is now unlocked.")


@bot.tree.command(name="lockgame", description="[Admin] Lock a specific game at any time")
@app_commands.describe(game="Game name to lock (coinflip, dice, blackjack, mines, rps)")
async def lockgame_cmd(interaction: discord.Interaction, game: str):
    if not is_owner_or_admin(interaction.user.id):
        await interaction.response.send_message("🚫 No permission.", ephemeral=True)
        return
    store.lock_game(game.lower())
    await interaction.response.send_message(f"🔒 Game `{game.lower()}` is now locked. Players cannot start new rounds.")


@bot.tree.command(name="unlockgame", description="[Admin] Unlock a specific game")
@app_commands.describe(game="Game name to unlock")
async def unlockgame_cmd(interaction: discord.Interaction, game: str):
    if not is_owner_or_admin(interaction.user.id):
        await interaction.response.send_message("🚫 No permission.", ephemeral=True)
        return
    store.unlock_game(game.lower())
    await interaction.response.send_message(f"✅ Game `{game.lower()}` is now unlocked.")


@bot.tree.command(name="announce", description="[Admin] Post an announcement embed")
@app_commands.describe(message="The announcement text")
async def announce_cmd(interaction: discord.Interaction, message: str):
    if not is_owner_or_admin(interaction.user.id):
        await interaction.response.send_message("🚫 No permission.", ephemeral=True)
        return
    embed = discord.Embed(title="📢 Announcement", description=message, color=GOLD_COLOR)
    embed.set_footer(text="GemCasino")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="seeprofit", description="[Admin] See profit/loss from PvP and gift taxes")
async def seeprofit_cmd(interaction: discord.Interaction):
    if not is_owner_or_admin(interaction.user.id):
        await interaction.response.send_message("🚫 No permission.", ephemeral=True)
        return
    profit = store.get_profit()
    total = profit["pvp_tax"] + profit["gift_tax"]
    embed = discord.Embed(title="📊 Profit / Loss", color=GOLD_COLOR)
    embed.add_field(name="PvP Tax (6%)", value=f"{fmt_gems(profit['pvp_tax'])} 💎", inline=True)
    embed.add_field(name="Gift Tax (2%)", value=f"{fmt_gems(profit['gift_tax'])} 💎", inline=True)
    embed.add_field(name="Total Profit", value=f"{fmt_gems(total)} 💎", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ==============================================================================
# RUN
# ==============================================================================

if __name__ == "__main__":
    if BOT_TOKEN == "MTUxOTE0NDUzNDkxNzc3OTQ2Ng.GYg-2L.VTF3BPHA9sHNHsQ98Uf2V0acF_zQ8OtZwBJ040":
        print("ERROR: Set your bot token in BOT_TOKEN or the DISCORD_BOT_TOKEN env var.")
    else:
        bot.run(BOT_TOKEN)
