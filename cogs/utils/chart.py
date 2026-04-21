"""Matplotlib configuration and chart utilities."""

import os

import discord
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

matplotlib.use("Agg")

# Font setup — assets/ is repo-static, not shadowed by Railway volume mount on data/
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_FONT_PATH = os.path.join(_PROJECT_ROOT, "assets", "fonts", "NotoSansKR-Bold.ttf")


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
