"""Matplotlib configuration and chart utilities."""

import os

import discord
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

from cogs.utils.constants import DATA_DIR

matplotlib.use("Agg")

# Font setup
_FONT_PATH = os.path.join(DATA_DIR, "fonts", "NotoSansKR-Bold.ttf")


def setup_font() -> str | None:
    """Load Korean font. Returns font name or None."""
    if os.path.exists(_FONT_PATH):
        fm.fontManager.addfont(_FONT_PATH)
        font_name = fm.FontProperties(fname=_FONT_PATH).get_name()
        plt.rcParams["font.family"] = font_name
        return font_name
    for name in ["Malgun Gothic", "NanumGothic", "AppleGothic"]:
        if name in {f.name for f in fm.fontManager.ttflist}:
            plt.rcParams["font.family"] = name
            return name
    return None


FONT_NAME = setup_font()
plt.rcParams["axes.unicode_minus"] = False


class ChartPeriod(discord.Enum):
    one_month = "1mo"
    three_months = "3mo"
    six_months = "6mo"
    one_year = "1y"
