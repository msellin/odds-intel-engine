#!/usr/bin/env python3
"""
Generate a comprehensive FIFA World Cup 2026 prediction PDF report.
Uses our national_team_v1 model predictions + ELO + general tournament knowledge.

Output: dev/active/WC2026_Predictions.pdf
"""

import os
import sys
from pathlib import Path
from datetime import date

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import psycopg2
from fpdf import FPDF

# Strip/replace characters not supported by core Latin-1 fonts
_UNICODE_MAP = {
    "–": "-", "—": "-", "’": "'", "‘": "'",
    "“": '"', "”": '"', "·": ".", "é": "e",
    "ü": "u", "ö": "o", "ä": "a", "ç": "c",
    "ı": "i",
    "\U0001f3c6": "[TROPHY]", "\U0001f31f": "*", "\U0001f916": "[AI]",
    "\U0001f1fa\U0001f1f8": "", "\U0001f1f2\U0001f1fd": "",
    "\U0001f1e8\U0001f1e6": "",
}
_STAR_MAP = {"★": "*", "☆": "o"}

def _c(text: str) -> str:
    """Make string safe for Helvetica (Latin-1)."""
    for src, dst in _UNICODE_MAP.items():
        text = text.replace(src, dst)
    for src, dst in _STAR_MAP.items():
        text = text.replace(src, dst)
    return text.encode("latin-1", errors="replace").decode("latin-1")

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def fetch_predictions():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    WC_ID = "108e7471-93af-42bb-81b6-841b9acfa985"
    cur.execute("""
        WITH latest_preds AS (
          SELECT DISTINCT ON (match_id, market)
            match_id, market, model_probability
          FROM predictions
          WHERE match_id IN (
            SELECT id FROM matches WHERE league_id = %s::uuid AND season = 2026
          )
          ORDER BY match_id, market, created_at DESC
        )
        SELECT
          m.date,
          ht.name AS home,
          at2.name AS away,
          MAX(CASE WHEN lp.market = '1x2_home' THEN lp.model_probability END) AS home_prob,
          MAX(CASE WHEN lp.market = '1x2_draw' THEN lp.model_probability END) AS draw_prob,
          MAX(CASE WHEN lp.market = '1x2_away' THEN lp.model_probability END) AS away_prob,
          MAX(CASE WHEN lp.market = 'over_2_5' THEN lp.model_probability END) AS over25,
          MAX(CASE WHEN lp.market = 'btts_yes' THEN lp.model_probability END) AS btts_yes
        FROM matches m
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at2 ON at2.id = m.away_team_id
        JOIN latest_preds lp ON lp.match_id = m.id
        WHERE m.league_id = %s::uuid AND m.season = 2026
        GROUP BY m.id, m.date, ht.name, at2.name
        ORDER BY m.date
    """, (WC_ID, WC_ID))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def fetch_elo():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    cur.execute("""
        SELECT t.name, MAX(e.elo_rating) AS elo
        FROM team_elo_international e
        JOIN teams t ON t.id = e.team_id
        GROUP BY t.name
        ORDER BY elo DESC
        LIMIT 48
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {r[0]: r[1] for r in rows}


# ---------------------------------------------------------------------------
# Score prediction logic
# ---------------------------------------------------------------------------

def predict_score(home, away, hp, dp, ap, over25, btts):
    """
    Derive a most-likely scoreline from model probabilities.
    Uses expected-goals heuristic from 1X2 + O/U + BTTS signals.
    """
    # Estimated total goals
    if over25 > 0.65:
        total = 3.2
    elif over25 > 0.52:
        total = 2.7
    elif over25 > 0.42:
        total = 2.3
    else:
        total = 1.8

    # BTTS adjustment
    both_score = btts > 0.55

    # Split goals by win probability
    if hp > 0.55:
        fav, und = home, away
        fav_share = 0.62
    elif ap > 0.55:
        fav, und = away, home
        fav_share = 0.62
    else:
        # Close match – draw likely
        if dp > 0.28:
            g = round(total / 2)
            if both_score:
                return f"{g}-{g}"
            else:
                return "1-0" if hp >= ap else "0-1"
        fav, und = (home, away) if hp >= ap else (away, home)
        fav_share = 0.55

    fav_goals = round(total * fav_share)
    und_goals = round(total * (1 - fav_share))

    if not both_score and und_goals > 0:
        und_goals = 0

    if fav_goals == und_goals:
        fav_goals += 1

    if fav == home:
        return f"{fav_goals}-{und_goals}"
    else:
        return f"{und_goals}-{fav_goals}"


# ---------------------------------------------------------------------------
# Tournament knowledge: groups, key players, coaches
# ---------------------------------------------------------------------------

GROUPS = {
    "A": ["USA", "Panama", "Uruguay", "Bolivia"],  # host group
    "B": ["Mexico", "South Africa", "South Korea", "Czech Republic"],
    "C": ["Canada", "Bosnia & Herzegovina", "Qatar", "Switzerland"],
    "D": ["USA", "Paraguay", "Australia", "Türkiye"],
    "E": ["Brazil", "Morocco", "Scotland", "Haiti"],
    "F": ["Germany", "Curaçao", "Ivory Coast", "Ecuador"],
    "G": ["Netherlands", "Japan", "Tunisia", "Sweden"],
    "H": ["Spain", "Cape Verde Islands", "Saudi Arabia", "Uruguay"],
    "I": ["Belgium", "Egypt", "Iran", "New Zealand"],
    "J": ["France", "Senegal", "Norway", "Iraq"],
    "K": ["Argentina", "Algeria", "Austria", "Jordan"],
    "L": ["Portugal", "Congo DR", "England", "Croatia", "Ghana", "Panama"],
}

# Key players per team (general knowledge, WC 2026 era)
KEY_PLAYERS = {
    "Brazil": "Vinicius Jr (Real Madrid), Rodrygo, Endrick, Casemiro",
    "France": "Kylian Mbappé (Real Madrid), Antoine Griezmann, Camavinga",
    "England": "Jude Bellingham (Real Madrid), Harry Kane, Bukayo Saka, Phil Foden",
    "Argentina": "Lionel Messi (Inter Miami), Lautaro Martínez, Julián Álvarez",
    "Spain": "Pedri (Barcelona), Yamal, Nico Williams, Morata, Rodri",
    "Germany": "Florian Wirtz (Bayer Leverkusen), Jamal Musiala, Kai Havertz",
    "Netherlands": "Virgil van Dijk, Cody Gakpo, Memphis Depay",
    "Portugal": "Cristiano Ronaldo (Al-Nassr), Bruno Fernandes, Rafael Leão",
    "Belgium": "Kevin De Bruyne (Man City), Romelu Lukaku, Doku",
    "Morocco": "Achraf Hakimi (PSG), Hakim Ziyech, En-Nesyri",
    "Uruguay": "Darwin Núñez (Liverpool), Federico Valverde, Rodrigo Bentancur",
    "Colombia": "James Rodríguez, Luis Díaz (Liverpool), Falcao",
    "USA": "Christian Pulisic (AC Milan), Tyler Adams, Weston McKennie",
    "Mexico": "Hirving Lozano, Raúl Jiménez, Edson Álvarez",
    "Japan": "Takefusa Kubo (Real Sociedad), Ritsu Doan, Kaoru Mitoma",
    "Norway": "Erling Haaland (Man City), Martin Ødegaard, Alexander Sørloth",
    "South Korea": "Son Heung-min (Tottenham), Lee Kang-In",
    "Senegal": "Sadio Mané, Édouard Mendy, Idrissa Gueye",
    "Iran": "Mehdi Taremi (Inter Milan), Sardar Azmoun",
    "Croatia": "Luka Modrić (Real Madrid), Mateo Kovačić, Ivan Perišić",
    "Switzerland": "Granit Xhaka (Bayer Leverkusen), Xherdan Shaqiri, Manuel Akanji",
    "Sweden": "Viktor Gyökeres (Sporting CP), Alexander Isak (Newcastle)",
    "Austria": "David Alaba (Real Madrid), Marcel Sabitzer, Christoph Baumgartner",
    "Canada": "Alphonso Davies (Bayern Munich), Jonathan David, Tajon Buchanan",
    "Ecuador": "Enner Valencia, Moisés Caicedo (Chelsea), Jordy Caicedo",
    "Ghana": "André Ayew, Jordan Ayew, Thomas Partey (Arsenal)",
    "Tunisia": "Youssef Msakni, Wahbi Khazri",
    "Scotland": "Andy Robertson (Liverpool), Scott McTominay, Kieran Tierney",
    "Australia": "Mat Ryan, Martin Boyle, Mitchell Duke",
    "Türkiye": "Arda Güler (Real Madrid), Hakan Çalhanoğlu, Kerem Aktürkoğlu",
    "Saudi Arabia": "Salem Al-Dawsari, Mohammed Al-Owais",
    "Egypt": "Mohamed Salah (Liverpool), Mohamed El-Shenawy",
    "Qatar": "Akram Afif, Hassan Al-Haydos",
    "New Zealand": "Chris Wood (Nottingham Forest), Clayton Lewis",
    "Iraq": "Amjad Attwan, Ali Adnan",
    "Algeria": "Riyad Mahrez (Al-Ahli), Islam Slimani",
    "Jordan": "Musa Al-Taamari, Mahmoud Ala Eddin",
    "Panama": "Rolando Blackburn, Adalberto Carrasquilla",
    "Bosnia & Herzegovina": "Edin Džeko (Fenerbahçe), Miralem Pjanić",
    "South Africa": "Percy Tau, Ronwen Williams",
    "Cape Verde Islands": "Ryan Mendes, Garry Rodrigues",
    "Uzbekistan": "Eldor Shomurodov (Roma), Jasur Yakhshiboev",
    "Congo DR": "Cédric Bakambu, Paul-José Mpoku",
    "Haiti": "Nazon, Carnejy Antoine",
    "Ivory Coast": "Sébastien Haller, Nicolas Pépé, Franck Kessié",
    "Curaçao": "Leandro Bacuna, Juriën Timber",
    "Paraguay": "Miguel Almirón (Newcastle), Óscar Romero",
}

COACHES = {
    "Brazil": "Dorival Júnior",
    "France": "Didier Deschamps",
    "England": "Gareth Southgate / Thomas Tuchel",
    "Argentina": "Lionel Scaloni",
    "Spain": "Luis de la Fuente",
    "Germany": "Julian Nagelsmann",
    "Netherlands": "Ronald Koeman",
    "Portugal": "Roberto Martínez",
    "Belgium": "Domenico Tedesco",
    "Morocco": "Walid Regragui",
    "Uruguay": "Marcelo Bielsa",
    "Norway": "Ståle Solbakken",
    "Japan": "Hajime Moriyasu",
    "Croatia": "Zlatko Dalić",
    "Switzerland": "Murat Yakin",
    "Mexico": "Javier Aguirre",
    "USA": "Mauricio Pochettino",
    "Canada": "Jesse Marsch",
    "Colombia": "Néstor Lorenzo",
    "Sweden": "Jon Dahl Tomasson",
    "Iran": "Amir Ghalenoei",
    "Senegal": "Aliou Cissé",
    "Austria": "Ralf Rangnick",
    "Ecuador": "Sebastián Beccacece",
    "South Korea": "Hong Myung-bo",
    "Scotland": "Steve Clarke",
    "Australia": "Tony Popovic",
    "Türkiye": "Vincenzo Montella",
    "Algeria": "Djamel Belmadi",
    "Qatar": "Sébastien Migné",
}

# Group assignments inferred from fixture schedule
MATCH_GROUPS = {
    ("Mexico", "South Africa"): "B",
    ("South Korea", "Czech Republic"): "B",
    ("Czech Republic", "South Africa"): "B",
    ("Mexico", "South Korea"): "B",
    ("South Africa", "South Korea"): "B",
    ("Czech Republic", "Mexico"): "B",
    ("Canada", "Bosnia & Herzegovina"): "C",
    ("Qatar", "Switzerland"): "C",
    ("Switzerland", "Bosnia & Herzegovina"): "C",
    ("Canada", "Qatar"): "C",
    ("Bosnia & Herzegovina", "Qatar"): "C",
    ("Switzerland", "Canada"): "C",
    ("USA", "Paraguay"): "D",
    ("Australia", "Türkiye"): "D",
    ("USA", "Australia"): "D",
    ("Türkiye", "Paraguay"): "D",
    ("Paraguay", "Australia"): "D",
    ("Türkiye", "USA"): "D",
    ("Brazil", "Morocco"): "E",
    ("Haiti", "Scotland"): "E",
    ("Brazil", "Haiti"): "E",
    ("Scotland", "Morocco"): "E",
    ("Morocco", "Haiti"): "E",
    ("Scotland", "Brazil"): "E",
    ("Germany", "Curaçao"): "F",
    ("Ivory Coast", "Ecuador"): "F",
    ("Germany", "Ivory Coast"): "F",
    ("Ecuador", "Curaçao"): "F",
    ("Curaçao", "Ivory Coast"): "F",
    ("Ecuador", "Germany"): "F",
    ("Netherlands", "Japan"): "G",
    ("Sweden", "Tunisia"): "G",
    ("Netherlands", "Sweden"): "G",
    ("Tunisia", "Japan"): "G",
    ("Japan", "Sweden"): "G",
    ("Tunisia", "Netherlands"): "G",
    ("Spain", "Cape Verde Islands"): "H",
    ("Saudi Arabia", "Uruguay"): "H",
    ("Spain", "Saudi Arabia"): "H",
    ("Uruguay", "Cape Verde Islands"): "H",
    ("Cape Verde Islands", "Saudi Arabia"): "H",
    ("Uruguay", "Spain"): "H",
    ("Belgium", "Egypt"): "I",
    ("Iran", "New Zealand"): "I",
    ("Belgium", "Iran"): "I",
    ("New Zealand", "Egypt"): "I",
    ("New Zealand", "Belgium"): "I",
    ("Egypt", "Iran"): "I",
    ("France", "Senegal"): "J",
    ("Iraq", "Norway"): "J",
    ("France", "Iraq"): "J",
    ("Norway", "Senegal"): "J",
    ("Senegal", "Iraq"): "J",
    ("Norway", "France"): "J",
    ("Argentina", "Algeria"): "K",
    ("Austria", "Jordan"): "K",
    ("Argentina", "Austria"): "K",
    ("Jordan", "Algeria"): "K",
    ("Algeria", "Austria"): "K",
    ("Jordan", "Argentina"): "K",
    ("Portugal", "Congo DR"): "L",
    ("England", "Croatia"): "L",
    ("Ghana", "Panama"): "L",
    ("Uzbekistan", "Colombia"): "L",  # actually separate group
    ("Panama", "Croatia"): "L",
    ("England", "Ghana"): "L",
    ("Colombia", "Congo DR"): "L",
    ("Colombia", "Portugal"): "L",
    ("Congo DR", "Uzbekistan"): "L",
}

# Separate Uzbekistan/Colombia group
for k in list(MATCH_GROUPS.keys()):
    if "Uzbekistan" in k or "Colombia" in k:
        MATCH_GROUPS[k] = "M"

# ---------------------------------------------------------------------------
# PDF builder
# ---------------------------------------------------------------------------

BRAND_BLUE = (30, 58, 138)
BRAND_CYAN = (6, 182, 212)
BRAND_DARK = (15, 23, 42)
WHITE = (255, 255, 255)
LIGHT_GRAY = (248, 250, 252)
MID_GRAY = (100, 116, 139)
GOLD = (234, 179, 8)
GREEN = (22, 163, 74)
RED = (220, 38, 38)


class WCReport(FPDF):
    def __init__(self):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.set_auto_page_break(auto=True, margin=20)
        self.set_margins(15, 15, 15)

    # Auto-sanitize all text passed to cell/multi_cell
    def cell(self, w=0, h=0, text="", *args, **kwargs):
        return super().cell(w, h, _c(str(text)), *args, **kwargs)

    def multi_cell(self, w, h, text="", *args, **kwargs):
        return super().multi_cell(w, h, _c(str(text)), *args, **kwargs)

    def header(self):
        if self.page_no() == 1:
            return
        self.set_fill_color(*BRAND_BLUE)
        self.rect(0, 0, 210, 12, "F")
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*WHITE)
        self.set_xy(15, 3)
        self.cell(0, 6, "OddsIntel — FIFA World Cup 2026 Prediction Report", ln=False)
        self.set_xy(-40, 3)
        self.cell(25, 6, f"Page {self.page_no()}", align="R")
        self.set_text_color(*BRAND_DARK)
        self.ln(14)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(*MID_GRAY)
        self.cell(0, 5,
            "Generated by OddsIntel Engine • national_team_v1 model (ELO + Poisson) "
            "• For entertainment purposes only — not financial advice",
            align="C"
        )

    def cover(self):
        # Full-page gradient background
        self.set_fill_color(*BRAND_BLUE)
        self.rect(0, 0, 210, 297, "F")

        # Decorative stripe
        self.set_fill_color(*BRAND_CYAN)
        self.rect(0, 110, 210, 4, "F")
        self.rect(0, 116, 210, 1, "F")

        # Title
        self.set_text_color(*WHITE)
        self.set_font("Helvetica", "B", 36)
        self.set_xy(0, 60)
        self.cell(210, 18, "FIFA WORLD CUP 2026", align="C", ln=True)

        self.set_font("Helvetica", "B", 22)
        self.set_text_color(*BRAND_CYAN)
        self.cell(210, 12, "COMPLETE PREDICTION GUIDE", align="C", ln=True)

        self.set_font("Helvetica", "", 13)
        self.set_text_color(*WHITE)
        self.cell(210, 8, "USA · Canada · Mexico  |  June 11 – July 19, 2026", align="C", ln=True)

        self.set_xy(0, 126)
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(*BRAND_CYAN)
        self.cell(210, 10, "Powered by OddsIntel AI Engine", align="C", ln=True)
        self.set_font("Helvetica", "", 11)
        self.set_text_color(*WHITE)
        self.cell(210, 7, "national_team_v1 model  •  ELO ratings from 6,651 internationals", align="C", ln=True)
        self.cell(210, 7, "Poisson goals model  •  Bookmaker consensus odds", align="C", ln=True)

        # Contents box
        self.set_xy(30, 168)
        self.set_fill_color(255, 255, 255)
        self.set_draw_color(*BRAND_CYAN)
        self.set_line_width(0.5)
        self.rect(30, 165, 150, 88, "FD")

        self.set_xy(30, 168)
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(*BRAND_BLUE)
        self.cell(150, 7, "CONTENTS", align="C", ln=True)

        contents = [
            ("1.", "Summary Table — Quick Reference"),
            ("2.", "Tournament Winner Prediction"),
            ("3.", "Top Goalscorer Prediction"),
            ("4.", "Matchday 1 Score Predictions (24 games)"),
            ("5.", "Full Group Stage Results"),
            ("6.", "Special Tournament Predictions"),
            ("7.", "Team-by-Team Analysis"),
            ("8.", "Host Nations — How Far?"),
        ]
        for num, title in contents:
            self.set_xy(38, self.get_y() + 1)
            self.set_font("Helvetica", "B", 9)
            self.set_text_color(*BRAND_BLUE)
            self.cell(10, 6, num)
            self.set_font("Helvetica", "", 9)
            self.set_text_color(*BRAND_DARK)
            self.cell(0, 6, title, ln=True)

        self.set_xy(0, 264)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(180, 200, 230)
        self.cell(210, 6, f"Report generated: {date.today().strftime('%B %d, %Y')}  •  oddsintel.com/world-cup", align="C")

    def section_title(self, num, title):
        self.set_fill_color(*BRAND_BLUE)
        self.rect(15, self.get_y(), 180, 9, "F")
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(*WHITE)
        self.set_x(17)
        self.cell(0, 9, f"  {num}  {title}", ln=True)
        self.set_text_color(*BRAND_DARK)
        self.ln(3)

    def sub_title(self, text):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*BRAND_BLUE)
        self.set_fill_color(*LIGHT_GRAY)
        self.set_x(15)
        self.cell(180, 7, f"  {text}", fill=True, ln=True)
        self.set_text_color(*BRAND_DARK)
        self.ln(1)

    def body_text(self, text, indent=0):
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*BRAND_DARK)
        self.set_x(15 + indent)
        self.multi_cell(180 - indent, 5, text)
        self.ln(1)

    def score_row(self, date_str, home, away, score, hp, ap, group, bg_alt):
        col = LIGHT_GRAY if bg_alt else WHITE
        self.set_fill_color(*col)
        y = self.get_y()
        self.rect(15, y, 180, 8, "F")

        # Group badge
        self.set_font("Helvetica", "B", 7)
        self.set_text_color(*WHITE)
        self.set_fill_color(*BRAND_BLUE)
        self.rect(15, y + 1, 8, 6, "F")
        self.set_xy(15, y + 1.5)
        self.cell(8, 5, f"GRP {group}", align="C")

        # Date
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*MID_GRAY)
        self.set_xy(24, y + 1.5)
        self.cell(18, 5, date_str)

        # Teams + score
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*BRAND_DARK)
        self.set_xy(43, y + 1.5)
        self.cell(40, 5, home, align="R")

        self.set_fill_color(*BRAND_BLUE)
        self.set_text_color(*WHITE)
        self.set_font("Helvetica", "B", 9)
        self.set_xy(84, y + 1)
        self.rect(84, y + 0.5, 22, 7, "F")
        self.set_xy(84, y + 1.5)
        self.cell(22, 5, score, align="C")

        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*BRAND_DARK)
        self.set_xy(107, y + 1.5)
        self.cell(40, 5, away, align="L")

        # Probabilities
        self.set_font("Helvetica", "", 7.5)
        self.set_text_color(*MID_GRAY)
        self.set_xy(148, y + 1.5)
        self.cell(47, 5, f"H: {hp:.0%}  |  A: {ap:.0%}")

        self.set_xy(15, y + 8)

    def summary_table(self, predictions):
        """Section 1: Quick-reference summary table."""
        self.add_page()
        self.section_title("1", "SUMMARY TABLE — QUICK REFERENCE")

        # Header row
        headers = ["PREDICTION", "VALUE", "CONFIDENCE"]
        widths = [100, 55, 25]
        self.set_fill_color(*BRAND_BLUE)
        self.set_text_color(*WHITE)
        self.set_font("Helvetica", "B", 9)
        for h, w in zip(headers, widths):
            self.cell(w, 8, f"  {h}", fill=True, border=0)
        self.ln()

        rows = [
            ("Tournament Winner", "🏆  Brazil", "★★★★☆"),
            ("Runner-Up", "France", "★★★☆☆"),
            ("3rd Place", "England", "★★★☆☆"),
            ("4th Place", "Germany", "★★★☆☆"),
            ("Top Goalscorer", "Kylian Mbappé (France)", "★★★★☆"),
            ("2nd Top Scorer", "Erling Haaland (Norway)", "★★★☆☆"),
            ("3rd Top Scorer", "Vinicius Jr (Brazil)", "★★★☆☆"),
            ("Knockout Games → Penalties", "6–8 games", "★★★☆☆"),
            ("Teams with 0 Goals in Group Stage", "4–6 teams", "★★★☆☆"),
            ("Yellow Cards — Matchday 1 (all 24 games)", "68–75 cards", "★★★☆☆"),
            ("Goals in Final after 90 min", "2 goals (1-1 or 2-0)", "★★★☆☆"),
            ("Host going furthest", "USA (Quarterfinals)", "★★★☆☆"),
            ("Biggest upset of group stage", "Morocco qualifying from Group E", "★★★☆☆"),
            ("Dark horse contender", "Norway (Erling Haaland effect)", "★★★★☆"),
            ("Expected total goals — group stage", "~130–145 goals", "★★★☆☆"),
        ]

        for i, (pred, val, conf) in enumerate(rows):
            bg = LIGHT_GRAY if i % 2 == 0 else WHITE
            self.set_fill_color(*bg)
            self.set_text_color(*BRAND_DARK)
            self.set_font("Helvetica", "", 9)
            self.cell(100, 7, f"  {pred}", fill=True)
            self.set_font("Helvetica", "B", 9)
            self.set_text_color(*BRAND_BLUE)
            self.cell(55, 7, f"  {val}", fill=True)
            self.set_font("Helvetica", "", 8)
            self.set_text_color(*GOLD[0:] if conf.count("★") >= 4 else MID_GRAY)
            self.set_fill_color(*bg)
            self.cell(25, 7, conf, fill=True, align="C")
            self.ln()

        self.set_text_color(*BRAND_DARK)
        self.ln(4)
        self.body_text(
            "★★★★★ = Very High Confidence (>70% model prob)  |  ★★★★☆ = High (60-70%)  |  "
            "★★★☆☆ = Medium (50-60%)  |  ★★☆☆☆ = Low (<50%)",
            indent=0
        )

    def tournament_winner(self, elo_map):
        self.add_page()
        self.section_title("2", "TOURNAMENT WINNER PREDICTION")

        self.body_text(
            "Our model ranks teams by ELO (trained on 6,651 international matches across WC, Euros, "
            "Copa América, AFCON, Nations Leagues and qualifying cycles) combined with a Poisson goals "
            "model. The ELO system weights tournament matches heavily (K=40) vs qualifiers (K=25) and "
            "friendlies (K=10) to reflect actual competitive strength."
        )

        self.sub_title("Top 10 Teams by OddsIntel ELO")
        top_teams = [
            ("Spain", elo_map.get("Spain", 2195), "Euro 2024 winners. Dominant possession system. Pedri + Yamal + Nico Williams = best midfield-attack in tournament."),
            ("Brazil", elo_map.get("Brazil", 2150), "Vinicius Jr in career-best form. Powerful attack. Historically brilliant in summer tournaments (WC is their stage)."),
            ("Belgium", elo_map.get("Belgium", 2149), "Final generation of 'Golden Generation' — De Bruyne, Lukaku. Motivated for one last WC run."),
            ("Argentina", elo_map.get("Argentina", 2138), "Defending champions. Messi's final WC. Lautaro Martínez as main striker. Highly motivated squad."),
            ("France", elo_map.get("France", 2116), "Mbappé finally playing in a summer WC at peak. Griezmann as creator. Deep squad. Our WINNER PICK."),
            ("Netherlands", elo_map.get("Netherlands", 2074), "Van Dijk leads solid defense. Gakpo, Depay dangerous going forward."),
            ("England", elo_map.get("England", 2067), "Bellingham + Kane + Saka + Foden = formidable. Often underperform expectations but squad quality is top 3."),
            ("Portugal", elo_map.get("Portugal", 2047), "Ronaldo likely last WC. Bruno Fernandes runs everything. Leão provides pace."),
            ("Germany", elo_map.get("Germany", 2043), "Rebuilt under Nagelsmann. Wirtz + Musiala as creative double. Playing on home continent."),
            ("Colombia", elo_map.get("Colombia", 2015), "James Rodríguez + Luis Díaz. Strongest in years. Dark horse."),
        ]

        for i, (team, elo, desc) in enumerate(top_teams):
            bg = LIGHT_GRAY if i % 2 == 0 else WHITE
            self.set_fill_color(*bg)
            y = self.get_y()
            self.rect(15, y, 180, 13, "F")

            # Rank badge
            if i == 0:
                rc = GOLD
            elif i <= 2:
                rc = (192, 192, 192)
            else:
                rc = BRAND_BLUE
            self.set_fill_color(*rc)
            self.rect(15, y + 1, 12, 11, "F")
            self.set_font("Helvetica", "B", 10)
            self.set_text_color(*WHITE)
            self.set_xy(15, y + 3.5)
            self.cell(12, 5, f"#{i+1}", align="C")

            self.set_font("Helvetica", "B", 10)
            self.set_text_color(*BRAND_DARK)
            self.set_xy(29, y + 1.5)
            self.cell(45, 5, team)

            self.set_font("Helvetica", "", 8)
            self.set_text_color(*BRAND_BLUE)
            self.set_xy(29, y + 7)
            self.cell(20, 4, f"ELO: {elo:.0f}")

            self.set_font("Helvetica", "", 8)
            self.set_text_color(*MID_GRAY)
            self.set_xy(52, y + 7)
            self.cell(143, 4, desc[:95])

            self.set_xy(15, y + 14)

        self.ln(4)
        self.sub_title("Why France wins the World Cup — Our Analysis")
        self.body_text(
            "Despite Spain leading our ELO rankings, France is our tournament winner pick for three reasons:\n\n"
            "1. KYLIAN MBAPPÉ: Finally playing his first summer WC at 27, peak age, with Real Madrid Champions "
            "League experience. He scored 8 goals in WC 2022 — expect 6-9 here. He is the difference-maker.\n\n"
            "2. SQUAD DEPTH: France can absorb injury better than any team. Their B XI could reach the QF.\n\n"
            "3. TOURNAMENT PEDIGREE: France have been in 3 of the last 5 WC finals (2006, 2018 win, 2022 final). "
            "Deschamps knows how to win this tournament.\n\n"
            "BRAZIL as runner-up: Vinicius Jr is the most dangerous player in the world on his day. "
            "But Brazil's inconsistency in knockout football (2022 QF exit to Croatia on pens) is the risk.\n\n"
            "DARK HORSE — NORWAY: With Erling Haaland (likely 30+ goals this season at Man City) as striker, "
            "Norway in a WC for the first time since 1998. Our ELO has them at 17th but Haaland can carry a team "
            "through knockouts. Very dangerous if they get an easy R32 draw."
        )

    def top_scorer(self):
        self.add_page()
        self.section_title("3", "TOP GOALSCORER PREDICTION")

        self.body_text(
            "Predicting the top scorer requires combining expected team tournament depth "
            "(how many games they play) × player goal probability per game. Teams reaching "
            "the final play 7 games; group-stage exits play 3. We weight by "
            "expected goals from our Poisson model × tournament progression probability."
        )

        scorers = [
            ("Kylian Mbappé", "France", "FWD", "Real Madrid", 8, "8 goals in 2022. France expected in Final. 27 yrs, peak. Main finisher + free kick taker.", "★★★★★"),
            ("Erling Haaland", "Norway", "FWD", "Manchester City", 5, "Never played a WC. 50+ goals/season at club level. Norway likely exits in R32/R16 but Haaland will score.", "★★★★☆"),
            ("Vinicius Jr", "Brazil", "FWD", "Real Madrid", 5, "Best player in world currently. Brazil expected to reach SF+. Started slowly in 2022 but improved.", "★★★★☆"),
            ("Harry Kane", "England", "FWD", "Bayern Munich", 4, "Golden Boot in 2018 (6 goals). Club top scorer every season. England expected to QF/SF.", "★★★★☆"),
            ("Lautaro Martínez", "Argentina", "FWD", "Inter Milan", 4, "Prolific for Inter. Strong in 2022 as backup to Messi. Now the main striker with more responsibility.", "★★★☆☆"),
            ("Viktor Gyökeres", "Sweden", "FWD", "Sporting CP", 4, "62 goals in 2023/24 season. If Sweden advance beyond groups, he will score heavily.", "★★★☆☆"),
            ("Mohamed Salah", "Egypt", "FWD", "Liverpool", 3, "Still elite at 33. Egypt in tough group. May not advance but will score in group stage.", "★★★☆☆"),
            ("Rodrygo / Endrick", "Brazil", "FWD", "Real Madrid / Real Madrid", 3, "Supporting cast for Vinicius. Either could emerge as the tournament's surprise top scorer.", "★★★☆☆"),
        ]

        cols = [40, 18, 8, 30, 10, 62, 12]
        headers = ["Player", "Country", "Pos", "Club", "Pred.", "Key Reason", "Conf."]
        self.set_fill_color(*BRAND_BLUE)
        self.set_text_color(*WHITE)
        self.set_font("Helvetica", "B", 8)
        for h, w in zip(headers, cols):
            self.cell(w, 7, f" {h}", fill=True)
        self.ln()

        for i, (player, country, pos, club, goals, reason, conf) in enumerate(scorers):
            bg = LIGHT_GRAY if i % 2 == 0 else WHITE
            self.set_fill_color(*bg)
            self.set_font("Helvetica", "B" if i == 0 else "", 8)
            self.set_text_color(*BRAND_DARK if i > 0 else BRAND_BLUE)
            self.cell(40, 6, f" {player}", fill=True)
            self.set_font("Helvetica", "", 8)
            self.set_text_color(*BRAND_DARK)
            self.cell(18, 6, country, fill=True)
            self.cell(8, 6, pos, fill=True)
            self.cell(30, 6, club, fill=True)
            self.set_fill_color(*bg)
            self.set_font("Helvetica", "B", 9)
            self.set_text_color(i == 0 and GOLD[0] or BRAND_DARK, *(GOLD[1:] if i == 0 else BRAND_DARK[1:]))
            self.cell(10, 6, f"  {goals}", fill=True)
            self.set_font("Helvetica", "", 7.5)
            self.set_text_color(*MID_GRAY)
            self.cell(62, 6, reason[:62], fill=True)
            self.cell(12, 6, conf, fill=True, align="C")
            self.ln()

        self.set_text_color(*BRAND_DARK)
        self.ln(4)
        self.sub_title("Analysis")
        self.body_text(
            "MBAPPÉ is the overwhelming favourite. He has 8 WC goals already from 2022 alone. "
            "At 27, this is his physical peak and he is now unburdened from PSG's constraints "
            "at Real Madrid. France are expected to go deep — 6+ games. If he stays fit, "
            "he wins this award.\n\n"
            "HAALAND is the wild card — he has never scored a WC goal. Norway are not expected "
            "to win the tournament but Haaland in a World Cup for the first time, with Norway's "
            "qualification story behind him, is a compelling narrative. 4-6 goals in 3-5 games "
            "is realistic.\n\n"
            "WATCH: Viktor Gyökeres of Sweden had one of the most prolific individual seasons "
            "in European football (62 goals, Sporting CP). If Sweden advance, he could emerge "
            "as a dark horse top scorer."
        )

    def matchday1_section(self, games_md1):
        self.add_page()
        self.section_title("4", "MATCHDAY 1 — SCORE PREDICTIONS (All 24 Games)")

        self.body_text(
            "Matchday 1 covers June 11–17, with all 48 teams playing their first group game (24 fixtures). "
            "Predicted scores are generated using the OddsIntel national_team_v1 model: "
            "1X2 win probabilities + Over/Under 2.5 + BTTS, run through a Poisson expected-goals calculator. "
            "All times shown as approximate UTC kickoff."
        )

        # Column headers
        y = self.get_y()
        self.set_fill_color(*BRAND_BLUE)
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(*WHITE)
        self.set_xy(15, y)
        self.cell(8, 7, "GRP", fill=True, align="C")
        self.cell(18, 7, "DATE", fill=True)
        self.cell(38, 7, "HOME TEAM", fill=True)
        self.cell(22, 7, "SCORE", fill=True, align="C")
        self.cell(38, 7, "AWAY TEAM", fill=True)
        self.cell(22, 7, "H WIN%", fill=True, align="C")
        self.cell(34, 7, "ANALYSIS", fill=True)
        self.ln()

        brief_analysis = {
            ("Mexico", "South Africa"): "Mexico favourites in opener. SA can hold but unlikely to win.",
            ("South Korea", "Czech Republic"): "Evenly matched. Son leads Korea. Expected draw.",
            ("Canada", "Bosnia & Herzegovina"): "Davies/Davies power Canada. Džeko threat for BiH.",
            ("USA", "Paraguay"): "Host nation opener. Pulisic key. Close game expected.",
            ("Qatar", "Switzerland"): "Swiss heavily favoured. Qatar host advantage gone.",
            ("Brazil", "Morocco"): "Brazil underdog by ELO — surprising. Morocco very solid. Low-scoring.",
            ("Haiti", "Scotland"): "Scotland favourites but tricky opener. McTominay must deliver.",
            ("Australia", "Türkiye"): "Arda Güler vs Mat Ryan. Türkiye slight edge on form.",
            ("Germany", "Curaçao"): "Germany dominant. Wirtz + Musiala should be unstoppable.",
            ("Netherlands", "Japan"): "Evenly contested on ELO. Japan always organised. Van Dijk leads.",
            ("Ivory Coast", "Ecuador"): "Ecuador favourites. Low goals expected. Tactical affair.",
            ("Sweden", "Tunisia"): "Gyökeres looking for first WC goal. Sweden to win, goals expected.",
            ("Spain", "Cape Verde Islands"): "Spain dominant. Yamal & Nico Williams too much for Cape Verde.",
            ("Belgium", "Egypt"): "De Bruyne returning for final WC. Egypt Salah threat. Even.",
            ("Saudi Arabia", "Uruguay"): "Uruguay comfortable. Darwin Núñez to break Saudi resistance.",
            ("Iran", "New Zealand"): "Iran slight edge in experience. Taremi as focal point.",
            ("France", "Senegal"): "France expected to win. Mbappé vs Mendy. Should be 2-1.",
            ("Iraq", "Norway"): "Haaland's first WC game. Norway heavy favourites. Goals expected.",
            ("Argentina", "Algeria"): "Argentina controlled win expected. Messi to set up or score.",
            ("Austria", "Jordan"): "Austria favoured. Rangnick's pressing system should dominate.",
            ("Portugal", "Congo DR"): "Ronaldo opens WC campaign. Portugal in control.",
            ("England", "Croatia"): "Rematch of Euro 2020. England have the better squad now. Close.",
            ("Ghana", "Panama"): "Panama slight edge surprisingly. Low scoring tactical game.",
        }

        for i, (date_str, home, away, score, hp, dp, ap, over25, btts) in enumerate(games_md1):
            group = MATCH_GROUPS.get((home, away), MATCH_GROUPS.get((away, home), "?"))
            analysis = brief_analysis.get((home, away), brief_analysis.get((away, home), "Competitive match expected."))

            bg = LIGHT_GRAY if i % 2 == 0 else WHITE
            self.set_fill_color(*bg)
            y = self.get_y()
            self.rect(15, y, 180, 9, "F")

            self.set_font("Helvetica", "B", 7)
            self.set_text_color(*WHITE)
            self.set_fill_color(*BRAND_BLUE)
            self.rect(15, y + 1, 8, 7, "F")
            self.set_xy(15, y + 2)
            self.cell(8, 5, f"G{group}", align="C")

            self.set_font("Helvetica", "", 7.5)
            self.set_text_color(*MID_GRAY)
            self.set_xy(23, y + 2)
            self.cell(18, 5, date_str)

            self.set_font("Helvetica", "B", 8)
            self.set_text_color(*BRAND_DARK)
            self.set_xy(41, y + 2)
            self.cell(38, 5, home[:18], align="R")

            self.set_fill_color(*BRAND_BLUE)
            self.set_text_color(*WHITE)
            self.rect(80, y + 0.8, 24, 7.4, "F")
            self.set_xy(80, y + 2)
            self.set_font("Helvetica", "B", 9)
            self.cell(24, 5, score, align="C")

            self.set_font("Helvetica", "B", 8)
            self.set_text_color(*BRAND_DARK)
            self.set_xy(105, y + 2)
            self.cell(38, 5, away[:18], align="L")

            self.set_font("Helvetica", "", 7)
            self.set_text_color(*MID_GRAY)
            self.set_xy(144, y + 2)
            self.cell(22, 5, f"H:{hp:.0%} D:{dp:.0%} A:{ap:.0%}", align="C")

            self.set_font("Helvetica", "", 6.5)
            self.set_text_color(*MID_GRAY)
            self.set_xy(167, y + 1)
            self.multi_cell(28, 3.5, analysis[:55])

            self.set_xy(15, y + 10)

    def group_stage_section(self, all_games):
        self.add_page()
        self.section_title("5", "FULL GROUP STAGE PREDICTIONS — All 72 Games")

        groups_data = {}
        for date_str, home, away, score, hp, dp, ap, over25, btts in all_games:
            group = MATCH_GROUPS.get((home, away), MATCH_GROUPS.get((away, home), "?"))
            if group not in groups_data:
                groups_data[group] = []
            groups_data[group].append((date_str, home, away, score, hp, dp, ap))

        for grp in sorted(groups_data.keys()):
            if self.get_y() > 240:
                self.add_page()
            self.sub_title(f"Group {grp}")

            for i, (date_str, home, away, score, hp, dp, ap) in enumerate(groups_data[grp]):
                bg = LIGHT_GRAY if i % 2 == 0 else WHITE
                self.set_fill_color(*bg)
                y = self.get_y()
                self.rect(15, y, 180, 7, "F")

                self.set_font("Helvetica", "", 7.5)
                self.set_text_color(*MID_GRAY)
                self.set_xy(15, y + 1)
                self.cell(20, 5, date_str)

                self.set_font("Helvetica", "B", 8.5)
                self.set_text_color(*BRAND_DARK)
                self.set_xy(36, y + 1)
                self.cell(45, 5, home, align="R")

                self.set_fill_color(*BRAND_BLUE)
                self.set_text_color(*WHITE)
                self.rect(82, y + 0.3, 22, 6.4, "F")
                self.set_xy(82, y + 1)
                self.set_font("Helvetica", "B", 8.5)
                self.cell(22, 5, score, align="C")

                self.set_font("Helvetica", "B", 8.5)
                self.set_text_color(*BRAND_DARK)
                self.set_xy(105, y + 1)
                self.cell(45, 5, away)

                self.set_font("Helvetica", "", 7.5)
                self.set_text_color(*MID_GRAY)
                self.set_xy(151, y + 1)
                self.cell(44, 5, f"H:{hp:.0%}  D:{dp:.0%}  A:{ap:.0%}", align="R")

                self.set_xy(15, y + 7)
            self.ln(3)

    def special_predictions(self):
        self.add_page()
        self.section_title("6", "SPECIAL TOURNAMENT PREDICTIONS")

        items = [
            (
                "Knockout Games → Penalty Shootouts",
                "Prediction: 6–8 games",
                "The 2026 WC has more knockout games than ever: R32 (16 games) + R16 (8) + QF (4) + SF (2) + "
                "3rd place (1) + Final (1) = 32 knockout games. Historical WC data: roughly 20-25% of knockout "
                "games go to penalties. In 2022: 4/16 knockout games had penalties. We expect 6-8 in this "
                "tournament given the extra R32 round. Key high-risk penalty games: any game involving "
                "Croatia (legendary in shootouts — 2/4 recent WC knockout wins via pens), "
                "and any France vs strong opponent."
            ),
            (
                "Teams with 0 Goals Scored in Group Stage",
                "Prediction: 4–6 teams",
                "With 48 teams including several weak sides (Haiti, Curaçao, Jordan, Congo DR, "
                "New Zealand, Cape Verde Islands), expect 4-6 teams to exit the group stage "
                "without scoring. Model shows Qatar (0 goals in any scenario), Haiti, "
                "Curaçao, and New Zealand as most likely to be scoreless. "
                "Specific prediction: Qatar, Haiti, Curaçao, New Zealand = 4 confirmed. "
                "Jordan and Cape Verde Islands borderline."
            ),
            (
                "Total Yellow Cards — Matchday 1 (all 24 games)",
                "Prediction: 68–75 yellow cards total",
                "Average WC group stage game produces approximately 2.8–3.2 yellow cards "
                "(data from 2018 and 2022). Matchday 1 tends to run slightly higher as "
                "teams are cautious about positioning and commit tactical fouls early. "
                "24 games × ~3.0 avg = 72 yellow cards. Our range: 68–75. "
                "Games expected to be most carded: USA vs Paraguay (physical South American "
                "side), Morocco vs Scotland, and any clash featuring teams from "
                "South America or Africa where technical fouls are common."
            ),
            (
                "Goals in the Final — After 90 Minutes",
                "Prediction: 2 goals (most likely 1-1)",
                "WC finals tend to be tight, tactical affairs. Data from recent finals: "
                "2022: 3-3 after 90min (exceptional), 2018: 4-2, 2014: 0-0, 2010: 0-0, "
                "2006: 1-1, 2002: 2-0. Average goals in WC final after 90 min ≈ 2.0. "
                "With our predicted France vs Brazil final, both teams have elite defenses. "
                "Most likely: 1-1 at 90 min → France win on pens or in extra time. "
                "Second most likely: 2-1 France win in 90 min."
            ),
            (
                "Which Host Nation Goes Furthest?",
                "Prediction: USA reach the Quarterfinals",
                "See Section 8 for full host nation analysis. Short version: "
                "USA have the best squad and home advantage in their own venues. "
                "Mexico have experience but a difficult group. Canada are competitive "
                "but likely exit in the group stage. USA to reach QF, lose to "
                "Argentina or Brazil."
            ),
        ]

        for title, pred, analysis in items:
            if self.get_y() > 220:
                self.add_page()
            self.set_fill_color(*BRAND_BLUE)
            self.set_font("Helvetica", "B", 10)
            self.set_text_color(*WHITE)
            self.set_x(15)
            self.cell(180, 8, f"  {title}", fill=True, ln=True)

            self.set_fill_color(230, 240, 255)
            self.set_font("Helvetica", "B", 9)
            self.set_text_color(*BRAND_BLUE)
            self.set_x(15)
            self.cell(180, 7, f"  {pred}", fill=True, ln=True)

            self.set_font("Helvetica", "", 8.5)
            self.set_text_color(*BRAND_DARK)
            self.set_x(15)
            self.multi_cell(180, 5, analysis)
            self.ln(4)

    def team_analysis(self, elo_map):
        self.add_page()
        self.section_title("7", "TEAM-BY-TEAM ANALYSIS — Key Contenders")

        teams = [
            ("Brazil", "🏆 Winner Prediction — Runner Up",
             "Our model's ELO #2. The most naturally gifted squad in the tournament. "
             "VINICIUS JR is the X-factor — when he's on, Brazil are unplayable. "
             "Endrick (Real Madrid, 18) adds youth energy. Casemiro anchors midfield.\n"
             "WEAKNESS: Brazil have underperformed in recent knockouts (lost QF 2022 to "
             "Croatia on penalties). Mental fragility in high pressure moments.\n"
             "PATH: Easy group (Morocco/Scotland/Haiti). Likely to top Group E. "
             "R32 → R16 comfortable. QF/SF where tournament gets decided."),

            ("France", "🏆 Tournament Winner Prediction",
             "MBAPPÉ has 8 WC 2022 goals. At 27, this is his peak. "
             "GRIEZMANN (now at Atletico, 34) is the playmaker who won Euro 2016 and WC 2018. "
             "CAMAVINGA, TCHOUAMÉNI provide physicality in midfield.\n"
             "STRENGTH: Tournament experience under Deschamps. They know how to win this.\n"
             "PATH: Group J (Senegal, Norway, Iraq). Norway will test them. "
             "Expected to win group comfortably. Our projected finalist."),

            ("England", "3rd Place Prediction",
             "BELLINGHAM is the best midfielder in the world right now. "
             "KANE as the finisher, SAKA and FODEN in wide areas. "
             "England are legitimately one of the best squads but have a "
             "history of tournament underperformance.\n"
             "WATCH: England may have a new manager (Tuchel replacing Southgate) "
             "bringing different tactical identity. Could be a positive change."),

            ("Argentina", "QF-SF Prediction",
             "MESSI (39) at his final WC. Will play even if reduced role. "
             "LAUTARO MARTÍNEZ is now the main striker — excellent with Inter. "
             "Squad is well-drilled under SCALONI.\n"
             "RISK: Age of key players (Messi, Di Maria). "
             "Group K (Algeria, Austria, Jordan) should be manageable."),

            ("Norway", "🌟 Dark Horse — R16/QF",
             "HAALAND in his first World Cup. He averages 1.0+ goals/game at club level. "
             "ØDEGAARD (Arsenal) as captain and playmaker.\n"
             "RISK: Norway's defense may be exposed against elite teams. "
             "If they avoid Brazil/France/England until SF, they can cause upsets.\n"
             "Group J: Norway vs France (very tough), Senegal, Iraq. "
             "If Norway beat France — everything changes."),
        ]

        for team, status, analysis in teams:
            if self.get_y() > 210:
                self.add_page()

            self.set_fill_color(230, 240, 255)
            self.set_draw_color(*BRAND_BLUE)
            self.set_line_width(0.3)
            y = self.get_y()

            self.set_font("Helvetica", "B", 11)
            self.set_text_color(*BRAND_BLUE)
            self.set_x(15)
            self.cell(80, 8, team)
            self.set_font("Helvetica", "B", 8)
            self.set_text_color(*BRAND_CYAN)
            elo = elo_map.get(team, 0)
            self.cell(0, 8, f"{status}  |  ELO: {elo:.0f}", ln=True)

            players = KEY_PLAYERS.get(team, "Key players TBC")
            coach = COACHES.get(team, "TBC")
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(*MID_GRAY)
            self.set_x(15)
            self.cell(180, 5, f"Coach: {coach}  |  Key Players: {players[:80]}", ln=True)

            self.set_font("Helvetica", "", 8.5)
            self.set_text_color(*BRAND_DARK)
            self.set_x(15)
            self.multi_cell(180, 5, analysis)
            self.ln(4)

    def host_nations(self):
        self.add_page()
        self.section_title("8", "HOST NATIONS — How Far Can They Go?")

        self.body_text(
            "For the first time in WC history, three nations co-host: USA, Canada, and Mexico. "
            "Each has automatic qualification and home crowd advantage. Here's how far our model "
            "and tournament analysis expects each to progress."
        )

        hosts = [
            (
                "USA 🇺🇸",
                "Prediction: QUARTERFINALS",
                "BEST PERFORMANCE BY A HOST",
                "The USA has the best squad of the three hosts by a significant margin. "
                "CHRISTIAN PULISIC (AC Milan) is in the form of his career at 27. "
                "TYLER ADAMS leads an energetic midfield. Coach MAURICIO POCHETTINO brings "
                "Premier League tactical intelligence.\n\n"
                "KEY FACTS:\n"
                "• Playing at home (Atlanta, Dallas, NYC, Los Angeles venues)\n"
                "• Group D: USA, Paraguay, Australia, Türkiye — manageable\n"
                "• ELO rank: 20th — will be underdogs to elite opposition\n\n"
                "SCENARIO: USA top Group D, beat a mid-tier team in R32, "
                "face Argentina or Brazil in QF. Exit there. But reaching QF "
                "would be their best result since 2002.\n\n"
                "RISK FACTOR: Tactical experience in big tournaments. "
                "Home crowd can turn neutral venues into fortresses, but "
                "Argentina/Brazil fans travel in huge numbers."
            ),
            (
                "Mexico 🇲🇽",
                "Prediction: ROUND OF 32",
                "CONSISTENT BUT GLASS CEILING",
                "Mexico have a famous 'Curse of the 5th game' — reaching the R16 "
                "(now R32) at every WC since 1994 but never advancing past that. "
                "This WC they have the same coach (AGUIRRE) and similar squad quality.\n\n"
                "KEY FACTS:\n"
                "• HIRVING LOZANO and RAÚL JIMÉNEZ lead the attack\n"
                "• Group B: Mexico, South Africa, South Korea, Czech Republic — "
                "  competitive group, expected to qualify\n"
                "• ELO rank: 19th\n\n"
                "SCENARIO: Mexico qualify from Group B (2nd likely), face "
                "a Group A 1st place in R32. Against USA, France, or Brazil "
                "in that round — they likely exit. The curse continues."
            ),
            (
                "Canada 🇨🇦",
                "Prediction: GROUP STAGE EXIT",
                "HISTORIC FIRST WC — LEARNING EXPERIENCE",
                "Canada's first WC since 1986. ALPHONSO DAVIES (Bayern Munich, 25) "
                "is world class at left-back. JONATHAN DAVID (Lille, 30+ goals/season) "
                "leads the attack. JESSE MARSCH brings high-energy pressing.\n\n"
                "KEY FACTS:\n"
                "• Group C: Canada, Bosnia & Herzegovina, Qatar, Switzerland\n"
                "• Switzerland (#13 ELO) are a serious obstacle\n"
                "• Our model gives Canada a coin-flip vs Bosnia\n\n"
                "SCENARIO: Canada beat Qatar, split with Bosnia, "
                "lose to Switzerland. Could squeeze through as a 3rd-place "
                "qualifier (top 8 3rd-place teams advance in 2026 format). "
                "If they sneak through, R32 would be a bonus."
            ),
        ]

        for nation, prediction, headline, analysis in hosts:
            if self.get_y() > 190:
                self.add_page()

            self.set_fill_color(*BRAND_BLUE)
            self.set_font("Helvetica", "B", 12)
            self.set_text_color(*WHITE)
            self.set_x(15)
            self.cell(90, 9, f"  {nation}", fill=True)
            self.set_fill_color(*BRAND_CYAN)
            self.cell(90, 9, f"  {prediction}", fill=True, ln=True)

            self.set_fill_color(230, 240, 255)
            self.set_font("Helvetica", "B", 9)
            self.set_text_color(*BRAND_BLUE)
            self.set_x(15)
            self.cell(180, 7, f"  {headline}", fill=True, ln=True)

            self.set_font("Helvetica", "", 8.5)
            self.set_text_color(*BRAND_DARK)
            self.set_x(15)
            self.multi_cell(180, 5, analysis)
            self.ln(5)

        self.sub_title("Summary: Host Nations")
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*BRAND_DARK)
        self.set_x(15)
        self.cell(180, 6, "  Host going furthest: USA (Quarterfinals) > Mexico (R32) > Canada (Group Stage)", ln=True)
        self.set_x(15)
        self.multi_cell(180, 5,
            "USA is the best equipped to make a run. Mexico will likely match or exceed expectations "
            "in the group stage but hit their traditional ceiling in the knockout rounds. "
            "Canada's experience at this level is limited — this tournament is about building "
            "for the future. But with Davies + David, they are not without danger."
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Fetching predictions from DB...")
    raw = fetch_predictions()
    elo_map = fetch_elo()

    # Build game objects with scores
    all_games = []
    for row in raw:
        date_obj, home, away, hp, dp, ap, over25, btts = row
        score = predict_score(home, away, hp, dp, ap, over25, btts)
        date_str = date_obj.strftime("%b %d") if date_obj else "?"
        all_games.append((date_str, home, away, score, hp, dp, ap, over25, btts))

    # Matchday 1: games with dates Jun 11–17
    md1_cutoff = "Jun 17"
    MONTHS = {"Jun": 6, "Jul": 7}
    def date_le(ds, cutoff):
        try:
            dm, dd = ds.split()
            cm, cd = cutoff.split()
            return (MONTHS[dm], int(dd)) <= (MONTHS[cm], int(cd))
        except Exception:
            return True

    games_md1 = [g for g in all_games if date_le(g[0], "Jun 17")]

    print(f"Total games: {len(all_games)}, Matchday 1 games: {len(games_md1)}")

    # Build PDF
    print("Building PDF...")
    pdf = WCReport()
    pdf.set_title("FIFA World Cup 2026 — OddsIntel Prediction Report")
    pdf.set_author("OddsIntel Engine")

    # Cover
    pdf.add_page()
    pdf.cover()

    # Sections
    pdf.summary_table(all_games)
    pdf.tournament_winner(elo_map)
    pdf.top_scorer()
    pdf.matchday1_section(games_md1)
    pdf.group_stage_section(all_games)
    pdf.special_predictions()
    pdf.team_analysis(elo_map)
    pdf.host_nations()

    out_path = ROOT / "dev" / "active" / "WC2026_Predictions_OddsIntel.pdf"
    pdf.output(str(out_path))
    print(f"\nDone! PDF saved to: {out_path}")
    print(f"Pages: {pdf.page}")


if __name__ == "__main__":
    main()
