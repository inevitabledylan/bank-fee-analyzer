"""Generate synthetic account analysis statements for Heartland Fabrication Co.

Run from the project root:
    python -m src.generate_data

What it creates (everything is fictional):
  reference/benchmark_prices.csv       illustrative price ranges per AFP code
  data/raw/price_schedule_<bank>.csv   each bank's contracted price list, including tiers
  data/raw/statements_<bank>.csv       one row per account x month x service line
  data/raw/balances.csv                average ledger and collected balance per account x month
  data/raw/bank_terms.csv              ECR, reserve haircut, day basis, carryover per bank x month
  data/raw/_answer_key.csv             the planted anomalies A1-A8 (tests only - the app never reads it)
  data/raw/_mapping_key.csv            the true AFP code for every bank description (tests only)

Every random draw comes from one generator seeded with config.RANDOM_SEED,
so running this twice produces identical files.
"""

import calendar

import numpy as np
import pandas as pd

from src import config

# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------
MONTHS = list(pd.date_range(config.PERIOD_START, config.PERIOD_END, freq="MS"))
FIRST_PAYDAY = pd.Timestamp("2025-10-03")  # Heartland pays employees every other Friday
PAYROLL_EMPLOYEES = 1950                   # direct-deposit employees paid through Prairie
VOLUME_PEAK_MONTH = 6                      # April 2026 (index 6) = busiest month of the year

STATEMENT_COLUMNS = [
    "bank", "account_id", "account_name", "statement_month", "bank_service_code",
    "bank_description", "afp_code", "volume", "unit_price", "total_charge",
    "ecr_eligible", "price_tier",
]

# ---------------------------------------------------------------------------
# Illustrative benchmark prices: (p25, median, p75) per AFP code.
# These are made-up ranges for demonstration - NOT market survey data.
# ---------------------------------------------------------------------------
BENCHMARK_LABEL = "Illustrative only - not market data"
BENCHMARKS = {
    "000230": (0.06, 0.09, 0.12),     # FDIC assessment, per $1,000 of average balance
    "010000": (15.00, 25.00, 35.00),
    "010020": (35.00, 50.00, 65.00),
    "010021": (15.00, 25.00, 35.00),
    "010300": (5.00, 10.00, 15.00),
    "010411": (10.00, 20.00, 30.00),
    "050000": (75.00, 125.00, 175.00),
    "050100": (0.25, 0.35, 0.45),
    "050300": (0.04, 0.06, 0.08),
    "100000": (10.00, 20.00, 30.00),
    "100010": (0.08, 0.12, 0.16),
    "100220": (0.06, 0.10, 0.14),
    "100224": (0.08, 0.12, 0.16),
    "150120": (0.12, 0.18, 0.24),
    "150410": (18.00, 25.00, 32.00),
    "151351": (0.02, 0.04, 0.06),
    "200010": (50.00, 75.00, 100.00),
    "200310": (0.03, 0.05, 0.07),
    "250000": (25.00, 40.00, 55.00),
    "250100": (0.06, 0.10, 0.14),
    "250101": (0.06, 0.10, 0.14),
    "250120": (0.01, 0.02, 0.03),
    "250201": (0.06, 0.10, 0.14),
    "250302": (2.50, 4.00, 5.50),
    "250501": (5.00, 10.00, 15.00),
    "251050": (15.00, 25.00, 35.00),
    "350000": (25.00, 40.00, 55.00),
    "350100": (10.00, 15.00, 20.00),
    "350300": (8.00, 12.00, 16.00),
    "350400": (25.00, 35.00, 45.00),
    "400000": (50.00, 100.00, 150.00),
    "400011": (0.05, 0.08, 0.11),
    "400200": (50.00, 75.00, 100.00),
    "450020": (75.00, 100.00, 150.00),
}

# ---------------------------------------------------------------------------
# Banks, their compensation terms, and their accounts
# ---------------------------------------------------------------------------
BANKS = {
    "prairie": {
        "name": "Prairie National Bank", "code_prefix": "PN", "code_start": 1010, "code_step": 15,
        "reserve_pct": 0.0, "day_basis": 365, "ecr_carryover": False, "float_pct": 0.030,
        "ecr_schedule": [("2025-10-01", 0.0275)],
    },
    "summit": {
        "name": "Summit Commercial Bank", "code_prefix": "SC", "code_start": 40100, "code_step": 113,
        "reserve_pct": 0.10, "day_basis": 360, "ecr_carryover": False, "float_pct": 0.050,
        "ecr_schedule": [("2025-10-01", 0.0250)],
    },
    "riverbend": {
        "name": "Riverbend Trust Company", "code_prefix": "RB-", "code_start": 200, "code_step": 7,
        "reserve_pct": 0.0, "day_basis": 365, "ecr_carryover": False, "float_pct": 0.025,
        "ecr_schedule": [("2025-10-01", 0.0225), ("2026-04-01", 0.0175)],  # A7: ECR cut in April
    },
}

# (account_id, account_name, share of the bank's balances, is a ZBA account)
ACCOUNTS = {
    "prairie": [
        ("PNB-0001", "Operating - ZBA Master", 0.40, True),
        ("PNB-0002", "ZBA Sub - AP Checks", 0.0, True),
        ("PNB-0003", "ZBA Sub - AP ACH and Wires", 0.0, True),
        ("PNB-0004", "ZBA Sub - Plant 1 Disbursements", 0.0, True),
        ("PNB-0005", "ZBA Sub - Plant 2 Disbursements", 0.0, True),
        ("PNB-0006", "ZBA Sub - Plant 3 Disbursements", 0.0, True),
        ("PNB-0007", "ZBA Sub - Plant 4 Disbursements", 0.0, True),
        ("PNB-0008", "ZBA Sub - Freight and Logistics", 0.0, True),
        ("PNB-0009", "ZBA Sub - Benefits and Insurance", 0.0, True),
        ("PNB-0010", "ZBA Sub - Tax Payments", 0.0, True),
        ("PNB-0011", "Payroll", 0.15, False),
        ("PNB-0012", "Concentration", 0.45, False),
    ],
    "summit": [
        ("SCB-0001", "Lockbox - East Region Receipts", 0.02, False),
        ("SCB-0002", "Lockbox - Central Region Receipts", 0.02, False),
        ("SCB-0003", "Lockbox - West Region Receipts", 0.02, False),
        ("SCB-0004", "Receivables Concentration", 0.22, False),
        ("SCB-0005", "Customer ACH Collections", 0.04, False),
        ("SCB-0006", "Customer Refunds and Rebates", 0.01, False),
        ("SCB-0007", "Card Settlement", 0.02, False),
        ("SCB-0008", "Operating Reserve", 0.65, False),
    ],
    "riverbend": [
        ("RTC-0001", "Operating - Backup", 0.55, False),
        ("RTC-0002", "Payroll - Backup", 0.12, False),
        ("RTC-0003", "Credit Facility - Loan Payments", 0.25, False),
        ("RTC-0004", "Escrow - Insurance Collateral", 0.07, False),
        ("RTC-0005", "Disbursement - Legacy AP", 0.01, False),
    ],
}

# An account that stops all activity partway through the year (but keeps being billed).
DORMANT_FROM = {"PNB-0007": pd.Timestamp("2026-03-01")}  # Plant 4 closed in March 2026


def account_ids(slug):
    """All account IDs at a bank."""
    return [a[0] for a in ACCOUNTS[slug]]


def each(accounts, volume=1):
    """Same monthly volume on every listed account (used for flat monthly fees)."""
    return {a: volume for a in accounts}


# ---------------------------------------------------------------------------
# Services each bank bills, and on which accounts.
#   kind "fixed"   -> flat monthly count (maintenance fees); value = count
#   kind "item"    -> transaction volume; value = typical monthly volume,
#                     or "payroll" / "payroll_files" for payroll-driven volume
#   kind "same_as" -> same volume as another code on the same account (e.g. check images)
#   kind "bai"     -> BAI prior-day detail items, derived from posted transactions
#   kind "fdic"    -> FDIC assessment, volume = bank's average ledger balance / $1,000
# ---------------------------------------------------------------------------
PNB_ALL, SCB_ALL, RTC_ALL = account_ids("prairie"), account_ids("summit"), account_ids("riverbend")
PNB_SUBS = PNB_ALL[1:10]

SERVICES = {
    "prairie": [
        ("010020", "fixed", {"PNB-0001": 1}),
        ("010021", "fixed", each(PNB_SUBS)),
        ("010000", "fixed", each(["PNB-0011", "PNB-0012"])),
        ("010300", "fixed", each(PNB_ALL)),
        ("010411", "fixed", {"PNB-0012": 1}),
        ("000230", "fdic", "PNB-0012"),
        ("100220", "item", {"PNB-0012": 120}),
        ("100224", "item", {"PNB-0012": 850}),
        ("150120", "item", {"PNB-0002": 3300, "PNB-0004": 160, "PNB-0005": 140,
                            "PNB-0006": 150, "PNB-0007": 120, "PNB-0008": 90}),
        ("150410", "item", {"PNB-0002": 3}),
        ("151351", "same_as", "150120"),
        ("200310", "same_as", "150120"),
        ("200010", "fixed", each(["PNB-0002", "PNB-0004", "PNB-0005", "PNB-0006", "PNB-0007", "PNB-0008"])),
        ("250000", "fixed", each(["PNB-0003", "PNB-0009", "PNB-0010", "PNB-0011", "PNB-0012"])),
        ("250101", "item", {"PNB-0003": 1800, "PNB-0009": 40, "PNB-0011": "payroll"}),
        ("250120", "item", {"PNB-0003": 1750}),
        ("250201", "item", {"PNB-0012": 3000, "PNB-0009": 30, "PNB-0010": 20}),
        ("250302", "item", {"PNB-0011": 6, "PNB-0003": 4}),
        ("251050", "fixed", each(["PNB-0001", "PNB-0012"])),
        ("250501", "item", {"PNB-0003": 22, "PNB-0009": 4, "PNB-0011": "payroll_files"}),
        ("350000", "fixed", {"PNB-0001": 1}),
        ("350100", "item", {"PNB-0001": 35, "PNB-0003": 160, "PNB-0010": 12}),
        ("350300", "item", {"PNB-0012": 90}),
        ("350400", "item", {"PNB-0003": 20}),
        ("400000", "fixed", {"PNB-0012": 1}),
        ("400011", "bai", None),
        ("400200", "fixed", {"PNB-0001": 1}),
    ],
    "summit": [
        ("010000", "fixed", each(SCB_ALL)),
        ("010300", "fixed", each(SCB_ALL)),
        ("010411", "fixed", {"SCB-0004": 1}),
        ("000230", "fdic", "SCB-0004"),
        ("050000", "fixed", each(["SCB-0001", "SCB-0002", "SCB-0003"])),
        ("050100", "item", {"SCB-0001": 1500, "SCB-0002": 1200, "SCB-0003": 900}),
        ("050300", "same_as", "050100"),
        ("100220", "item", {"SCB-0004": 40}),
        ("100224", "item", {"SCB-0004": 180}),
        ("250000", "fixed", each(["SCB-0005", "SCB-0006"])),
        ("250101", "item", {"SCB-0006": 45}),
        ("250201", "item", {"SCB-0005": 1400, "SCB-0007": 28}),
        ("250302", "item", {"SCB-0005": 5}),
        ("250501", "item", {"SCB-0006": 6}),
        ("350100", "item", {"SCB-0004": 18}),
        ("350300", "item", {"SCB-0004": 45}),
        ("400000", "fixed", {"SCB-0004": 1}),
        ("400011", "bai", None),
    ],
    "riverbend": [
        ("010000", "fixed", each(RTC_ALL)),
        ("010300", "fixed", each(RTC_ALL)),
        ("010411", "fixed", {"RTC-0001": 1}),
        ("000230", "fdic", "RTC-0001"),
        ("100224", "item", {"RTC-0001": 45}),
        ("150120", "item", {"RTC-0001": 380}),
        ("151351", "same_as", "150120"),
        ("200010", "fixed", each(["RTC-0001", "RTC-0005"])),   # RTC-0005 is A3 (no checks)
        ("250000", "fixed", each(["RTC-0002", "RTC-0005"])),   # RTC-0005 is A3 (no ACH)
        ("250101", "item", {"RTC-0002": 65}),
        ("250501", "item", {"RTC-0002": "payroll_files"}),
        ("350100", "item", {"RTC-0001": 22, "RTC-0003": 3}),
        ("350300", "item", {"RTC-0001": 6, "RTC-0003": 3}),
        ("400000", "fixed", {"RTC-0001": 1}),
        ("400011", "bai", None),
        ("400200", "fixed", each(["RTC-0001", "RTC-0004"])),   # RTC-0004 is A3 (nothing reported)
    ],
}

# ---------------------------------------------------------------------------
# How each bank describes its services (SPEC.md 2.4).
# Prairie: clean wording. Riverbend: abbreviations. Summit: product names.
# ---------------------------------------------------------------------------
DESCRIPTIONS = {
    "prairie": {
        "000230": "FDIC ASSESSMENT FEE", "010000": "ACCOUNT MAINTENANCE",
        "010020": "ZBA MASTER ACCOUNT MAINTENANCE", "010021": "ZBA SUB ACCOUNT MAINTENANCE",
        "010300": "ELECTRONIC STATEMENT", "010411": "RELATIONSHIP ANALYSIS STATEMENT",
        "100220": "DEPOSITED ITEM ON-US", "100224": "DEPOSITED ITEM TRANSIT",
        "150120": "CHECKS PAID", "150410": "STOP PAYMENT", "151351": "CHECK IMAGE CAPTURE",
        "200010": "POSITIVE PAY MAINTENANCE", "200310": "ARP PAID AND OUTSTANDING ITEM",
        "250000": "ACH MODULE MAINTENANCE", "250101": "ACH ORIGINATED CREDIT ITEM",
        "250120": "ACH ORIGINATED ADDENDA", "250201": "ACH RECEIVED ITEM",
        "250302": "ACH RETURN ITEM", "250501": "ACH FILE TRANSMISSION", "251050": "ACH DEBIT BLOCK MAINTENANCE",
        "350000": "WIRE MODULE MAINTENANCE", "350100": "OUTGOING DOMESTIC WIRE - ONLINE",
        "350300": "INCOMING DOMESTIC WIRE", "350400": "OUTGOING INTERNATIONAL WIRE",
        "400000": "ONLINE BANKING MONTHLY BASE", "400011": "BAI PRIOR DAY DETAIL ITEM",
        "400200": "INTRADAY REPORTING MODULE",
    },
    "riverbend": {
        "000230": "FDIC ASSMT", "010000": "ACCT MAINT", "010300": "ELEC STMT",
        "010411": "REL ANLYS STMT", "100224": "DEP ITM TRNST", "150120": "CHKS PD",
        "151351": "CHK IMG CAPT", "200010": "POS PAY MAINT", "250000": "ACH MOD MAINT",
        "250101": "ACH ORIG CR ITM", "250501": "ACH FILE TRANS", "350100": "OUT DOM WIRE ONL",
        "350300": "INC DOM WIRE", "400000": "OLB MO BASE", "400011": "BAI PD DTL ITM",
        "400200": "INTRADAY RPT MOD",
    },
    "summit": {
        "000230": "FDIC Insurance Recovery Fee", "010000": "Business Analyzed Checking - Monthly",
        "010300": "eStatement Delivery", "010411": "Relationship Summary Statement",
        "050000": "LockPoint Wholesale Box - Monthly", "050100": "LockPoint Remittance Item",
        "050300": "SmartVault Item Capture", "100220": "Deposit Item - Summit Drawn",
        "100224": "Deposit Item - Other Bank Drawn", "250000": "PayStream Service Base",
        "250101": "PayStream Outbound Credit Txn", "250201": "PayStream Inbound Item",
        "250302": "PayStream Return Handling", "250501": "PayStream File Upload",
        "350100": "SwiftLine Outgoing Domestic - Web", "350300": "SwiftLine Incoming Domestic",
        "400000": "InfoLink Portal Access - Monthly", "400011": "InfoLink Prior-Day Rpt Acct",
    },
}

# Summit leaves the AFP code blank on most lines - these are the ones it does provide.
SUMMIT_PROVIDED_CODES = {"000230", "010000", "010411", "250302", "250501", "350300"}

# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------
# Where each bank's normal prices fall inside the benchmark p25-p75 range
# (0.0 = p25, 0.5 = roughly median, 1.0 = p75). The primary bank prices best.
PRICE_BANDS = {"prairie": (0.1, 0.55), "summit": (0.5, 0.95), "riverbend": (0.15, 0.9)}

# A2: services deliberately priced above the benchmark p75.
PRICE_OVERRIDES = {
    "prairie": {"250501": 19.00, "150410": 36.00},
    "summit": {"350100": 30.00, "050100": 0.580, "050300": 0.100, "400000": 195.00, "250201": 0.180},
    "riverbend": {"010000": 45.00},
}

# Tiered services: price depends on the bank-wide monthly volume for that service.
# (tier label, minimum volume, maximum volume or None, unit price)
TIERS = {
    "prairie": {
        "250101": [("Tier 1", 0, 5000, 0.110), ("Tier 2", 5001, None, 0.075)],
        "150120": [("Tier 1", 0, 2500, 0.190), ("Tier 2", 2501, None, 0.150)],
    },
}

# ---------------------------------------------------------------------------
# Planted anomalies (SPEC.md 2.5)
# ---------------------------------------------------------------------------
A1_START = pd.Timestamp("2026-07-01")
A1_CODES = ["350100", "350300", "200010", "400011", "010021", "250201"]  # Prairie price increase
A1_RAISE_RANGE = (0.13, 0.19)  # drawn inside 12-20% so rounding cannot push it outside
A4_DUPLICATES = [("PNB-0003", "350100", "2025-11-01"), ("PNB-0011", "250501", "2026-05-01")]
A5_CODE, A5_MONTHS = "250101", ["2025-12-01", "2026-01-01", "2026-02-01"]
A6_ERRORS = [  # (account, code, month, how the billed total is wrong)
    ("SCB-0001", "050100", "2026-02-01", "plus_100"),
    ("SCB-0004", "350100", "2026-06-01", "one_extra_item"),
]

# How balances are set (DECISIONS.md: compensation is analyzed at the bank level).
#   "coverage": balances sized to cover normal fees by a small cushion (no idle cash).
#   "fixed":    a set balance level regardless of fees (Summit - large idle cash, A8).
BALANCE_TARGETS = {
    "prairie": {"method": "coverage", "coverage": 1.08, "season_amp": 0.03, "noise": 0.008},
    # Riverbend balances were sized for the original 2.25% ECR; the April cut (A7) leaves a shortfall.
    "riverbend": {"method": "coverage", "coverage": 1.04, "season_amp": 0.015, "noise": 0.005,
                  "calibration_ecr": 0.0225},
    "summit": {"method": "fixed", "collected": 33_000_000, "season_amp": 0.08, "noise": 0.015},
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def round_price(price):
    """Banks quote sub-dollar prices to 3 decimals and larger prices to cents."""
    return round(price, 3) if price < 1 else round(price, 2)


def days_in_month(month):
    return calendar.monthrange(month.year, month.month)[1]


def seasonal_factor(month_index, amplitude, peak_index=VOLUME_PEAK_MONTH):
    """Smooth yearly cycle: 1 + amplitude at the peak month, 1 - amplitude six months later."""
    return 1 + amplitude * np.cos(2 * np.pi * (month_index - peak_index) / 12)


def paydays_in_month(month):
    """Number of biweekly paydays that fall in a month (2 in most months, 3 in some)."""
    paydays = pd.date_range(FIRST_PAYDAY, MONTHS[-1] + pd.offsets.MonthEnd(0), freq="14D")
    return int(((paydays.year == month.year) & (paydays.month == month.month)).sum())


def ecr_for(slug, month):
    """ECR in effect for a bank in a given month."""
    rate = None
    for start, ecr in BANKS[slug]["ecr_schedule"]:
        if month >= pd.Timestamp(start):
            rate = ecr
    return rate


def earnings_per_dollar(slug, month, ecr):
    """Earnings credit per $1 of collected balance for one month (after the reserve haircut)."""
    bank = BANKS[slug]
    return ecr * days_in_month(month) / bank["day_basis"] * (1 - bank["reserve_pct"])


def is_ecr_eligible(slug, afp_code, afp_ref):
    """ECR eligibility from the AFP reference, except FDIC is a hard charge at Riverbend."""
    if afp_code == "000230":
        return slug != "riverbend"
    return bool(afp_ref.loc[afp_code, "typical_ecr_eligible"])


def load_afp_reference():
    """The provided AFP code list, indexed by code."""
    ref = pd.read_csv(config.REFERENCE_DIR / "afp_codes_sample.csv", dtype={"afp_code": str, "family_code": str})
    ref["typical_ecr_eligible"] = ref["typical_ecr_eligible"].astype(str).str.upper() == "TRUE"
    return ref.set_index("afp_code")


# ---------------------------------------------------------------------------
# Step 1: benchmarks and price lists
# ---------------------------------------------------------------------------
def build_benchmarks(afp_ref):
    """reference/benchmark_prices.csv - one illustrative price range per AFP code."""
    rows = []
    for code, (p25, median, p75) in BENCHMARKS.items():
        rows.append({
            "afp_code": code, "afp_description": afp_ref.loc[code, "afp_description"],
            "unit_basis": afp_ref.loc[code, "unit_basis"],
            "p25": p25, "median": median, "p75": p75, "label": BENCHMARK_LABEL,
        })
    return pd.DataFrame(rows)


def draw_unit_price(rng, slug, afp_code):
    """A bank's normal price for a service: a random point in its usual benchmark band."""
    if afp_code in PRICE_OVERRIDES.get(slug, {}):
        return PRICE_OVERRIDES[slug][afp_code]
    p25, _, p75 = BENCHMARKS[afp_code]
    low_share, high_share = PRICE_BANDS[slug]
    low, high = p25 + low_share * (p75 - p25), p25 + high_share * (p75 - p25)
    return round_price(rng.uniform(low, high))


def build_price_book(rng, slug, afp_ref):
    """Each bank's contracted price list, keyed by true AFP code."""
    bank = BANKS[slug]
    book = {}
    for i, (afp_code, _, _) in enumerate(SERVICES[slug]):
        tiers = TIERS.get(slug, {}).get(afp_code)
        if tiers is None:
            tiers = [("", None, None, draw_unit_price(rng, slug, afp_code))]
        shown_code = afp_code if (slug != "summit" or afp_code in SUMMIT_PROVIDED_CODES) else ""
        book[afp_code] = {
            "bank_service_code": f"{bank['code_prefix']}{bank['code_start'] + bank['code_step'] * i}",
            "bank_description": DESCRIPTIONS[slug][afp_code],
            "afp_code_shown": shown_code,
            "unit_basis": afp_ref.loc[afp_code, "unit_basis"],
            "ecr_eligible": is_ecr_eligible(slug, afp_code, afp_ref),
            "tiers": tiers,
        }
    return book


def price_schedule_table(slug, book):
    """The price book as a CSV-ready table (one row per service per tier)."""
    rows = []
    for item in book.values():
        for label, low, high, price in item["tiers"]:
            rows.append({
                "bank": BANKS[slug]["name"], "bank_service_code": item["bank_service_code"],
                "bank_description": item["bank_description"], "afp_code": item["afp_code_shown"],
                "unit_basis": item["unit_basis"], "price_tier": label,
                "tier_min_volume": low, "tier_max_volume": high,
                "unit_price": price, "ecr_eligible": item["ecr_eligible"],
                "effective_from": MONTHS[0].strftime("%Y-%m-%d"),
            })
    return pd.DataFrame(rows)


def draw_a1_increases(rng, book):
    """A1: new (higher) Prairie prices that take effect in July 2026 without notice."""
    new_prices = {}
    for code in A1_CODES:
        old = book[code]["tiers"][0][3]
        new_prices[code] = round_price(old * (1 + rng.uniform(*A1_RAISE_RANGE)))
    return new_prices


# ---------------------------------------------------------------------------
# Step 2: monthly volumes
# ---------------------------------------------------------------------------
def item_volume(rng, base, month_index, month):
    """Transaction count for one account-month: seasonal, with random noise."""
    if base == "payroll":
        return PAYROLL_EMPLOYEES * paydays_in_month(month)
    if base == "payroll_files":
        return paydays_in_month(month)
    expected = base * seasonal_factor(month_index, 0.05) * rng.normal(1, 0.04)
    return int(rng.poisson(max(expected, 0)))


# Codes that each post as a separate entry on the BAI prior-day report.
BAI_POSTING_CODES = {"150120", "250201", "250302", "250501", "350100", "350300", "350400"}
DEPOSIT_CODES = {"100220", "100224", "050100"}  # deposits post as roughly one entry per business day
BUSINESS_DAYS = 21


def bai_items(account_lines, is_zba):
    """BAI prior-day detail items for one account-month, based on what posted to it."""
    posted = sum(v for code, v in account_lines.items() if code in BAI_POSTING_CODES)
    if any(account_lines.get(code, 0) > 0 for code in DEPOSIT_CODES):
        posted += BUSINESS_DAYS
    if is_zba and posted > 0:
        posted += BUSINESS_DAYS  # daily ZBA funding transfer
    return posted


def build_volumes(rng, slug):
    """One row per account x month x service with its volume (before prices)."""
    zba = {a[0]: a[3] for a in ACCOUNTS[slug]}
    rows = []
    for i, month in enumerate(MONTHS):
        month_lines = {acct: {} for acct in account_ids(slug)}
        # Pass 1: flat monthly fees and transaction volumes.
        for afp_code, kind, spec in SERVICES[slug]:
            if kind not in ("fixed", "item"):
                continue
            for acct, base in spec.items():
                vol = base if kind == "fixed" else item_volume(rng, base, i, month)
                if kind == "item" and acct in DORMANT_FROM and month >= DORMANT_FROM[acct]:
                    vol = 0
                if vol > 0:
                    month_lines[acct][afp_code] = vol
        # Pass 2: services that mirror another service's volume (e.g. check images).
        for afp_code, kind, source in SERVICES[slug]:
            if kind == "same_as":
                for acct, lines in month_lines.items():
                    if lines.get(source, 0) > 0:
                        lines[afp_code] = lines[source]
        # Pass 3: BAI reporting items, derived from everything that posted.
        if any(kind == "bai" for _, kind, _ in SERVICES[slug]):
            for acct, lines in month_lines.items():
                items = bai_items(lines, zba[acct])
                if items > 0:
                    lines["400011"] = items
        for acct, lines in month_lines.items():
            for afp_code, vol in lines.items():
                rows.append({"account_id": acct, "statement_month": month, "afp_code_true": afp_code,
                             "volume": float(vol)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Step 3: prices on each line (contracted prices, before anomalies)
# ---------------------------------------------------------------------------
def pick_tier(tiers, bank_volume):
    """The tier whose volume range contains the bank-wide monthly volume."""
    for label, low, high, price in tiers:
        if low is None or (bank_volume >= low and (high is None or bank_volume <= high)):
            return label, price
    raise ValueError(f"No tier for volume {bank_volume}")


def apply_contract_prices(volumes, slug, book):
    """Attach the bank's description, codes, and correct contracted price to every line."""
    df = volumes.copy()
    bank_volume = df.groupby(["afp_code_true", "statement_month"])["volume"].transform("sum")
    tiers = [pick_tier(book[c]["tiers"], v) for c, v in zip(df["afp_code_true"], bank_volume)]
    df["price_tier"] = [t[0] for t in tiers]
    df["unit_price"] = [t[1] for t in tiers]
    df["bank"] = BANKS[slug]["name"]
    names = {a[0]: a[1] for a in ACCOUNTS[slug]}
    df["account_name"] = df["account_id"].map(names)
    for field in ("bank_service_code", "bank_description", "ecr_eligible"):
        df[field] = df["afp_code_true"].map(lambda c, f=field: book[c][f])
    df["afp_code"] = df["afp_code_true"].map(lambda c: book[c]["afp_code_shown"])
    return df


def eligible_fees_by_month(lines):
    """Sum of ECR-eligible charges per month (volume x price, full precision)."""
    eligible = lines[lines["ecr_eligible"]]
    return (eligible["volume"] * eligible["unit_price"]).groupby(eligible["statement_month"]).sum()


# ---------------------------------------------------------------------------
# Step 4: balances
# ---------------------------------------------------------------------------
def bank_balances(rng, slug, clean_fees, fdic_price, fdic_eligible):
    """Bank-level average collected balance and float % for each month."""
    target, bank = BALANCE_TARGETS[slug], BANKS[slug]
    out = {}
    for i, month in enumerate(MONTHS):
        float_pct = float(np.clip(bank["float_pct"] + rng.normal(0, 0.004), 0.02, 0.06))
        wiggle = seasonal_factor(i, target["season_amp"]) + rng.normal(0, target["noise"])
        if target["method"] == "fixed":
            collected = target["collected"] * wiggle
        else:
            coverage = target["coverage"] * wiggle
            ecr = target.get("calibration_ecr") or ecr_for(slug, month)
            per_dollar = earnings_per_dollar(slug, month, ecr)
            fees = clean_fees.get(month, 0.0)
            collected = coverage * fees / per_dollar
            # The FDIC fee depends on the balance, and (where it is ECR-eligible) the balance
            # target depends on fees, so repeat a few times until the two settle down.
            for _ in range(10):
                fdic_fee = fdic_price * collected / (1 - float_pct) / 1000 if fdic_eligible else 0.0
                collected = coverage * (fees + fdic_fee) / per_dollar
        out[month] = (collected, float_pct)
    return out


def account_balances(slug, bank_level):
    """Split each month's bank balance across accounts by their share."""
    rows = []
    for month, (collected, float_pct) in bank_level.items():
        for acct_id, name, share, _ in ACCOUNTS[slug]:
            acct_collected = collected * share
            rows.append({
                "bank": BANKS[slug]["name"], "account_id": acct_id, "account_name": name,
                "statement_month": month,
                "avg_ledger_balance": round(acct_collected / (1 - float_pct), 2),
                "avg_collected_balance": round(acct_collected, 2),
            })
    return pd.DataFrame(rows)


def fdic_lines(slug, book, balances):
    """FDIC assessment lines: billed on the bank's total average ledger balance per $1,000."""
    account = next(spec for code, kind, spec in SERVICES[slug] if kind == "fdic")
    ledger = balances.groupby("statement_month")["avg_ledger_balance"].sum()
    item = book["000230"]
    rows = []
    for month, total in ledger.items():
        rows.append({
            "account_id": account, "statement_month": month, "afp_code_true": "000230",
            "volume": round(total / 1000, 2), "price_tier": "", "unit_price": item["tiers"][0][3],
            "bank": BANKS[slug]["name"],
            "account_name": {a[0]: a[1] for a in ACCOUNTS[slug]}[account],
            "bank_service_code": item["bank_service_code"], "bank_description": item["bank_description"],
            "ecr_eligible": item["ecr_eligible"], "afp_code": item["afp_code_shown"],
        })
    return pd.DataFrame(rows)


def bank_terms_table():
    """data/raw/bank_terms.csv - compensation terms per bank per month."""
    rows = []
    for slug, bank in BANKS.items():
        for month in MONTHS:
            rows.append({
                "bank": bank["name"], "statement_month": month, "ecr": ecr_for(slug, month),
                "reserve_pct": bank["reserve_pct"], "day_basis": bank["day_basis"],
                "ecr_carryover": bank["ecr_carryover"],
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Step 5: plant the anomalies, recording each one in the answer key
# ---------------------------------------------------------------------------
def key_row(anomaly_id, name, slug, account, book, afp_code, months, detail):
    """One answer-key entry."""
    return {
        "anomaly_id": anomaly_id, "anomaly": name, "bank": BANKS[slug]["name"], "account_id": account,
        "bank_service_code": book[afp_code]["bank_service_code"] if afp_code else "",
        "afp_code": afp_code or "",
        "months": ";".join(pd.Timestamp(m).strftime("%Y-%m-%d") for m in months), "detail": detail,
    }


def plant_a1_price_increase(lines, book, new_prices, key):
    """A1: six Prairie services silently repriced from July 2026."""
    months = [m for m in MONTHS if m >= A1_START]
    for code, new in new_prices.items():
        old = book[code]["tiers"][0][3]
        mask = (lines["afp_code_true"] == code) & (lines["statement_month"] >= A1_START)
        lines.loc[mask, "unit_price"] = new
        key.append(key_row("A1", "Unannounced price increase", "prairie", "ALL", book, code, months,
                           f"Unit price raised from {old} to {new} ({new / old - 1:.1%})"))


def plant_a2_above_benchmark(key, books):
    """A2: record the services each bank prices above the benchmark p75 (set in PRICE_OVERRIDES)."""
    for slug, overrides in PRICE_OVERRIDES.items():
        for code, price in overrides.items():
            p75, median = BENCHMARKS[code][2], BENCHMARKS[code][1]
            key.append(key_row("A2", "Above-benchmark pricing", slug, "ALL", books[slug], code, MONTHS,
                               f"Unit price {price} vs illustrative p75 {p75} (median {median}, "
                               f"{price / median:.1f}x median)"))


def plant_a3_unused_services(lines, books, key):
    """A3: record monthly module fees billed on accounts with no related activity for 6+ months."""
    for slug, slug_lines in lines.items():
        for module, related in config.RELATED_ACTIVITY.items():
            fees = slug_lines[slug_lines["afp_code_true"] == module]
            for acct in fees["account_id"].unique():
                acct_lines = slug_lines[slug_lines["account_id"] == acct]
                active = set(acct_lines.loc[acct_lines["afp_code_true"].isin(related), "statement_month"])
                idle = [m for m in MONTHS if m not in active]
                if len(idle) >= 6:
                    key.append(key_row("A3", "Billing for unused service", slug, acct, books[slug], module,
                                       idle, f"Module fee billed with no related activity "
                                             f"({', '.join(related)}) for {len(idle)} months"))


def plant_a4_duplicates(lines, book, key):
    """A4: the same Prairie line billed twice in two months."""
    extra = []
    for acct, code, month in A4_DUPLICATES:
        month = pd.Timestamp(month)
        mask = (lines["account_id"] == acct) & (lines["afp_code_true"] == code) & (lines["statement_month"] == month)
        row = lines[mask]
        extra.append(row)
        charge = float(row["volume"].iloc[0] * row["unit_price"].iloc[0])
        key.append(key_row("A4", "Duplicate charge", "prairie", acct, book, code, [month],
                           f"Line billed twice; duplicate amount ${charge:,.2f}"))
    return pd.concat([lines] + extra, ignore_index=True)


def plant_a5_tier_misapplied(lines, book, key):
    """A5: Prairie bills ACH credits at the Tier 1 price although volume qualified for Tier 2."""
    tier1, tier2 = book[A5_CODE]["tiers"][0], book[A5_CODE]["tiers"][1]
    months = [pd.Timestamp(m) for m in A5_MONTHS]
    mask = (lines["afp_code_true"] == A5_CODE) & (lines["statement_month"].isin(months))
    lines.loc[mask, "unit_price"] = tier1[3]
    lines.loc[mask, "price_tier"] = tier1[0]
    key.append(key_row("A5", "Tier pricing misapplied", "prairie", "ALL", book, A5_CODE, months,
                       f"Bank-wide volume above {tier2[1]:,} qualifies for {tier2[0]} at {tier2[3]} "
                       f"but billed at {tier1[0]} price {tier1[3]}"))


def plant_a6_math_errors(lines, book, key):
    """A6: two Summit lines where the billed total does not equal volume x price."""
    for acct, code, month, how in A6_ERRORS:
        month = pd.Timestamp(month)
        mask = (lines["account_id"] == acct) & (lines["afp_code_true"] == code) & (lines["statement_month"] == month)
        vol, price = lines.loc[mask, "volume"].iloc[0], lines.loc[mask, "unit_price"].iloc[0]
        correct = round(vol * price, 2)
        billed = correct + 100.00 if how == "plus_100" else round((vol + 1) * price, 2)
        lines.loc[mask, "total_charge"] = billed
        key.append(key_row("A6", "Math error", "summit", acct, book, code, [month],
                           f"{vol:,.0f} x {price} = ${correct:,.2f} but billed ${billed:,.2f} "
                           f"(overbilled ${billed - correct:,.2f})"))


def plant_a7_a8_records(books, key):
    """A7 and A8 come from how balances and ECRs were set; record them in the key."""
    cut = [m for m in MONTHS if m >= pd.Timestamp("2026-04-01")]
    key.append(key_row("A7", "ECR cut creates net service charges", "riverbend", "ALL", books["riverbend"],
                       None, cut, "ECR cut from 2.25% to 1.75% in April 2026; balances sized for the old "
                                  "rate no longer cover analyzed fees"))
    key.append(key_row("A8", "Excess idle balances", "summit", "ALL", books["summit"], None, MONTHS,
                       "Collected balances far above the balance required to offset fees"))


# ---------------------------------------------------------------------------
# Putting it all together
# ---------------------------------------------------------------------------
def build_dataset():
    """Build every output table in memory. Returns a dict of DataFrames."""
    rng = np.random.default_rng(config.RANDOM_SEED)
    afp_ref = load_afp_reference()
    benchmarks = build_benchmarks(afp_ref)

    books = {slug: build_price_book(rng, slug, afp_ref) for slug in BANKS}
    a1_new_prices = draw_a1_increases(rng, books["prairie"])

    lines, balances = {}, []
    for slug in BANKS:
        priced = apply_contract_prices(build_volumes(rng, slug), slug, books[slug])
        fdic_price = books[slug]["000230"]["tiers"][0][3]
        bank_level = bank_balances(rng, slug, eligible_fees_by_month(priced), fdic_price,
                                   books[slug]["000230"]["ecr_eligible"])
        acct_bal = account_balances(slug, bank_level)
        balances.append(acct_bal)
        lines[slug] = pd.concat([priced, fdic_lines(slug, books[slug], acct_bal)], ignore_index=True)

    key = []
    plant_a1_price_increase(lines["prairie"], books["prairie"], a1_new_prices, key)
    plant_a2_above_benchmark(key, books)
    plant_a3_unused_services(lines, books, key)
    plant_a5_tier_misapplied(lines["prairie"], books["prairie"], key)
    for slug in lines:  # totals are calculated once prices are final
        lines[slug]["total_charge"] = (lines[slug]["volume"] * lines[slug]["unit_price"]).round(2)
    plant_a6_math_errors(lines["summit"], books["summit"], key)
    lines["prairie"] = plant_a4_duplicates(lines["prairie"], books["prairie"], key)
    plant_a7_a8_records(books, key)

    mapping_key = pd.DataFrame([
        {"bank": BANKS[slug]["name"], "bank_service_code": item["bank_service_code"],
         "bank_description": item["bank_description"], "afp_code_on_statement": item["afp_code_shown"],
         "true_afp_code": code}
        for slug, book in books.items() for code, item in book.items()
    ])
    statements = {
        slug: df.sort_values(["account_id", "statement_month", "bank_service_code"], kind="stable")
                .reset_index(drop=True)[STATEMENT_COLUMNS]
        for slug, df in lines.items()
    }
    return {
        "benchmarks": benchmarks,
        "price_schedules": {slug: price_schedule_table(slug, book) for slug, book in books.items()},
        "statements": statements,
        "balances": pd.concat(balances, ignore_index=True),
        "bank_terms": bank_terms_table(),
        "answer_key": pd.DataFrame(key),
        "mapping_key": mapping_key,
    }


def _csv(df, path):
    """Write a CSV with months as YYYY-MM-DD text."""
    out = df.copy()
    if "statement_month" in out:
        out["statement_month"] = out["statement_month"].dt.strftime("%Y-%m-%d")
    out.to_csv(path, index=False)


def write_dataset(data):
    """Write every table to disk."""
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    _csv(data["benchmarks"], config.REFERENCE_DIR / "benchmark_prices.csv")
    for slug, df in data["price_schedules"].items():
        _csv(df, config.RAW_DIR / f"price_schedule_{slug}.csv")
    for slug, df in data["statements"].items():
        _csv(df, config.RAW_DIR / f"statements_{slug}.csv")
    _csv(data["balances"], config.RAW_DIR / "balances.csv")
    _csv(data["bank_terms"], config.RAW_DIR / "bank_terms.csv")
    _csv(data["answer_key"], config.RAW_DIR / "_answer_key.csv")
    _csv(data["mapping_key"], config.RAW_DIR / "_mapping_key.csv")


def main():
    data = build_dataset()
    write_dataset(data)
    for slug, df in data["statements"].items():
        print(f"{BANKS[slug]['name']:<25} {len(df):>5} statement lines, ${df['total_charge'].sum():>12,.2f} total fees")
    print(f"Answer key: {len(data['answer_key'])} planted findings -> data/raw/_answer_key.csv")


if __name__ == "__main__":
    main()
