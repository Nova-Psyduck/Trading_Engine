"""
Discord Webhook Alert Module
=============================
Sends formatted alert messages to a Discord channel via webhook.
"""

import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1572671505312129205/8ugvduf_hxB5DgzagcQXzBybbMQ4oNpQAZZwO5wp7G6Ow6WovntDGHiLpu3X8pgzKJO_"


def send_discord_message(content: str, username: str = "Trading Engine 🤖") -> bool:
    """
    Send a plain text message to Discord via webhook.

    Parameters
    ----------
    content : str
        The message text to send.
    username : str
        Display name for the webhook bot.

    Returns
    -------
    bool
        True if sent successfully, False otherwise.
    """
    payload = {
        "username": username,
        "content": content,
    }

    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if response.status_code in (200, 204):
            return True
        else:
            print(f"❌ Discord Webhook Error ({response.status_code}): {response.text}")
            return False
    except Exception as e:
        print(f"❌ Discord Webhook Exception: {e}")
        return False


def send_discord_embed(
    title: str,
    description: str,
    color: int = 0x00FF00,
    fields: list[dict] | None = None,
    username: str = "Trading Engine 🤖",
    footer: str | None = None,
) -> bool:
    """
    Send a rich embed message to Discord via webhook.

    Parameters
    ----------
    title : str
        Embed title.
    description : str
        Embed description text.
    color : int
        Embed sidebar color (hex). Green=0x00FF00, Red=0xFF0000, Gold=0xFFD700.
    fields : list[dict], optional
        List of {"name": ..., "value": ..., "inline": bool} field dicts.
    username : str
        Display name for the webhook bot.
    footer : str, optional
        Footer text for the embed.

    Returns
    -------
    bool
        True if sent successfully, False otherwise.
    """
    embed = {
        "title": title,
        "description": description,
        "color": color,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if fields:
        embed["fields"] = fields

    if footer:
        embed["footer"] = {"text": footer}

    payload = {
        "username": username,
        "embeds": [embed],
    }

    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if response.status_code in (200, 204):
            return True
        else:
            print(f"❌ Discord Webhook Error ({response.status_code}): {response.text}")
            return False
    except Exception as e:
        print(f"❌ Discord Webhook Exception: {e}")
        return False


def send_price_cross_alert(
    ticker: str,
    current_price: float,
    target_price: float,
    direction: str,
    timeframe: str = "5m",
) -> bool:
    """
    Send a formatted price-cross alert embed to Discord.

    Parameters
    ----------
    ticker : str
        The stock ticker symbol.
    current_price : float
        The current price that triggered the alert.
    target_price : float
        The threshold price that was crossed.
    direction : str
        "ABOVE" or "BELOW" — which way the price crossed.
    timeframe : str
        Candle timeframe being monitored.
    """
    now_utc = datetime.now(timezone.utc)

    ts = now_utc.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST")

    if direction == "ABOVE":
        color = 0x00FF00  # Green
        emoji = "🟢📈"
        action_text = f"**CROSSED ABOVE** ${target_price:.2f}"
    else:
        color = 0xFF0000  # Red
        emoji = "🔴📉"
        action_text = f"**CROSSED BELOW** ${target_price:.2f}"

    title = f"{emoji} PRICE ALERT — {ticker}"
    description = f"**{ticker}** has {action_text}"

    fields = [
        {"name": "💰 Current Price", "value": f"${current_price:.4f}", "inline": True},
        {"name": "🎯 Target Price", "value": f"${target_price:.2f}", "inline": True},
        {"name": "📊 Direction", "value": direction, "inline": True},
        {"name": "⏱️ Timeframe", "value": timeframe, "inline": True},
        {"name": "🕐 Timestamp", "value": ts, "inline": False},
    ]

    footer = "Trading Engine • Price Alert Monitor"

    return send_discord_embed(
        title=title,
        description=description,
        color=color,
        fields=fields,
        footer=footer,
    )
