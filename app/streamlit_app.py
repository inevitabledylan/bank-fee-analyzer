"""Bank Fee Analyzer - Streamlit dashboard.

Run from the project root:
    streamlit run app/streamlit_app.py      (or: python run.py)

Reads only the processed outputs in data/processed/ and the cached AI answers in
data/cache/. It never calls an API on its own and never reads the answer key.
"""

import json
import re
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so "src" imports work
from src import analyze, config, memo  # noqa: E402

st.set_page_config(page_title="Bank Fee Analyzer", page_icon="🏦", layout="wide")

# ---------------------------------------------------------------------------
# Look and feel: one fixed color per bank (validated colorblind-safe set), quiet chrome
# ---------------------------------------------------------------------------
BANK_COLORS = {
    "Prairie National Bank": "#2a78d6",    # blue
    "Summit Commercial Bank": "#eb6834",   # orange
    "Riverbend Trust Company": "#1baf7a",  # aqua
}
BANK_ORDER = list(BANK_COLORS)
INK, INK_2, MUTED, GRID, AXIS, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"
BAND = "#e1e0d9"
FOOTER = "Synthetic data and illustrative benchmarks for demonstration purposes."

st.markdown("""
<style>
  .block-container {padding-top: 2rem; max-width: 1300px;}
  div[data-testid="stMetric"] {background: #ffffff; border: 1px solid rgba(11,11,11,0.10);
      border-radius: 8px; padding: 12px 16px;}
  div[data-testid="stMetricLabel"] p {color: #52514e; font-size: 0.85rem;}
  /* KPI values shrink with the screen instead of being cut off ("$233.…") on laptops */
  div[data-testid="stMetricValue"] {font-size: clamp(1.3rem, 1.9vw, 2.1rem);}
  div[data-testid="stMetricValue"] *, div[data-testid="stMetricLabel"] * {
      overflow: visible !important; text-overflow: clip !important; white-space: normal !important;}
  .what-this-shows {color: #52514e; font-size: 0.95rem; border-left: 3px solid #c3c2b7;
      padding: 2px 0 2px 12px; margin: 0 0 1.2rem 0;}
  .footer {color: #898781; font-size: 0.8rem; border-top: 1px solid #e1e0d9;
      margin-top: 2.5rem; padding-top: 0.6rem;}
</style>
""", unsafe_allow_html=True)


def money(x, cents=False, compact=False):
    """$1,234 below a million, $1.2M above (compact: $23.9K above $10K, for KPI cards).
    Negative values in parentheses."""
    if pd.isna(x):
        return "-"
    sign, x = ("(", ")") if x < 0 else ("", ""), abs(x)
    if x >= 1_000_000:
        text = f"${x / 1_000_000:,.1f}M"
    elif compact and x >= 10_000:
        text = f"${x / 1_000:,.1f}K"
    else:
        text = f"${x:,.2f}" if cents else f"${x:,.0f}"
    return f"{sign[0]}{text}{sign[1]}"


def panel_title(text):
    """Small-multiple titles: 'bank=Prairie National Bank' -> 'Prairie' (short, so panels never overlap)."""
    name = text.split("=")[-1]
    return name.split()[0] if name in BANK_COLORS else name


def style(fig, height=380, legend=True):
    """Shared chart chrome: light surface, hairline grid, muted axes, legend on top."""
    fig.update_layout(
        height=height, paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
        font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif", color=INK_2, size=13),
        margin=dict(l=10, r=10, t=40 if legend else 20, b=10), showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title_text="", font=dict(color=INK_2)),
        hoverlabel=dict(bgcolor="white", font_color=INK, bordercolor=AXIS),
    )
    fig.update_xaxes(gridcolor=GRID, linecolor=AXIS, zeroline=False, tickfont=dict(color=MUTED))
    fig.update_yaxes(gridcolor=GRID, linecolor=AXIS, zeroline=False, tickfont=dict(color=MUTED))
    fig.for_each_annotation(lambda a: a.update(text=panel_title(a.text), font=dict(color=INK, size=13)))
    return fig


def line_style_legend(fig, styles):
    """For charts where color = bank (named in each panel title) and line style = measure:
    hide the per-bank legend entries and show one neutral entry per line style instead."""
    fig.update_traces(showlegend=False)
    for name, dash in styles.items():
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines", name=name,
                                 line=dict(color=INK_2, dash=dash, width=2)))
    fig.update_layout(legend=dict(orientation="h", yanchor="bottom", y=1.08, xanchor="right", x=1),
                      margin=dict(t=70))
    return fig


def caption(text, where=st):
    """st.caption with dollar signs escaped (two "$" in one line would otherwise render as math)."""
    where.caption(text.replace("$", "\\$"))


def kpi(col, label, value, note=None, help=None):
    """A KPI card with an optional plain-text note underneath (no up/down arrow)."""
    col.metric(label, value, help=help)
    if note:
        caption(note, col)


def months_label(months):
    """'2026-07;2026-08;2026-09' -> 'Jul 2026 - Sep 2026 (3 months)'."""
    ms = [pd.Timestamp(m + "-01") for m in months.split(";")]
    if len(ms) == 1:
        return f"{ms[0]:%b %Y}"
    return f"{ms[0]:%b %Y} - {ms[-1]:%b %Y} ({len(ms)} months)"


def page_header(title, note):
    st.title(title)
    st.markdown(f'<div class="what-this-shows"><b>What this shows:</b> {note}</div>', unsafe_allow_html=True)


def footer():
    st.markdown(f'<div class="footer">{FOOTER}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
@st.cache_data
def load_data():
    p = config.PROCESSED_DIR
    codes = {"afp_code": str, "family_code": str, "mapped_afp_code": str, "afp_code_on_statement": str}
    read = lambda name: pd.read_csv(p / name, dtype=codes, keep_default_na=False, na_values=[""])
    data = {name: read(f"{name}.csv") for name in
            ("fee_summary", "compensation", "price_benchmark", "exceptions", "savings_summary")}
    data["mapping"] = read("code_mapping.csv")
    for name in ("fee_summary", "compensation"):
        data[name]["statement_month"] = pd.to_datetime(data[name]["statement_month"])
    data["exceptions"]["afp_code"] = data["exceptions"]["afp_code"].fillna("")  # A7/A8 have no AFP code
    data["accuracy"] = json.loads((p / "mapping_accuracy.json").read_text())
    ref = pd.read_csv(config.REFERENCE_DIR / "afp_codes_sample.csv", dtype=str)
    data["families"] = dict(zip(ref["family_code"], ref["family_name"]))
    return data


DATA = load_data()
MONTHS = sorted(DATA["compensation"]["statement_month"].unique())

# ---------------------------------------------------------------------------
# Sidebar: navigation and global filters
# ---------------------------------------------------------------------------
PAGES = ["Executive Summary", "Fee Drivers", "Price Benchmarking", "Exceptions",
         "Balances & ECR", "AI Mapping Review", "Negotiation Memo"]
with st.sidebar:
    st.markdown("### 🏦 Bank Fee Analyzer")
    caption("Heartland Fabrication Co. (fictional) · Oct 2025 - Sep 2026")
    page = st.radio("Page", PAGES, label_visibility="collapsed")
    st.divider()
    st.markdown("**Filters**")
    banks = st.multiselect("Bank", BANK_ORDER, default=BANK_ORDER)
    start, end = st.select_slider("Months", options=MONTHS, value=(MONTHS[0], MONTHS[-1]),
                                  format_func=lambda m: pd.Timestamp(m).strftime("%b %Y"))
    family_options = sorted(DATA["families"].items())
    families = st.multiselect("AFP family", [f for f, _ in family_options], placeholder="All families",
                              format_func=lambda f: f"{f} {DATA['families'][f]}")
    families = families or [f for f, _ in family_options]
    caption("Annualized findings are full-year figures and do not change with the month filter.")

banks = banks or BANK_ORDER
in_period = lambda df: df[(df["statement_month"] >= start) & (df["statement_month"] <= end)
                          & df["bank"].isin(banks)]
fees = in_period(DATA["fee_summary"])
fees = fees[fees["family_code"].isin(families)]
comp = in_period(DATA["compensation"])
exc = DATA["exceptions"][DATA["exceptions"]["bank"].isin(banks)]
exc = exc[(exc["afp_code"] == "") | exc["afp_code"].str[:2].isin(families)]  # A7/A8 have no code
month_strs = {pd.Timestamp(m).strftime("%Y-%m") for m in MONTHS if start <= m <= end}
exc = exc[exc["months_affected"].str.split(";").apply(lambda ms: bool(set(ms) & month_strs))]
bench = DATA["price_benchmark"]
bench = bench[bench["bank"].isin(banks) & bench["afp_code"].str[:2].isin(families)]
period_label = f"{pd.Timestamp(start):%b %Y} - {pd.Timestamp(end):%b %Y}"


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
def executive_summary():
    page_header("Executive Summary",
                "Total bank fees, how much of them the earnings credit on balances paid for, the cash the banks "
                "actually charged, and the savings this review identified. Findings are listed by dollar impact.")
    run_rate = exc.loc[exc["savings_type"] == analyze.RUN_RATE, "annualized_impact"].sum()
    one_time = exc["one_time_recovery"].sum()
    income = exc.loc[exc["savings_type"] == analyze.INCOME, "annualized_impact"].sum()
    excess = comp.assign(e=comp["excess_balance"].clip(lower=0)).groupby("statement_month")["e"].sum().mean()
    cols = st.columns(5)
    kpi(cols[0], "Bank fees", money(fees["total_charge"].sum(), compact=True), period_label,
        help="All service charges in the selected period")
    kpi(cols[1], "Earnings credit", money(comp["earnings_allowance"].sum(), compact=True), "non-cash, on balances",
        help="Credit the banks gave on balances: investable balance x ECR x days/basis")
    kpi(cols[2], "Cash charges", money(comp["net_service_charge"].sum(), compact=True), "fees paid in cash",
        help="Fees not covered by earnings credit, plus hard charges (e.g. FDIC at Riverbend)")
    kpi(cols[3], "Savings found", f"{money(run_rate, compact=True)}/yr", f"plus {money(one_time)} in refunds",
        help="Annual run-rate savings from pricing and unused services, plus one-time refunds of past errors")
    kpi(cols[4], "Excess cash", money(excess, compact=True), f"{money(income)}/yr income opportunity",
        help="Collected balance above what is needed to offset fees; income at the money market yield")

    st.subheader("Monthly analyzed fees vs. earnings allowance")
    long = comp.melt(id_vars=["bank", "statement_month"], value_vars=["analyzed_fees", "earnings_allowance"],
                     var_name="measure", value_name="amount")
    long["measure"] = long["measure"].map({"analyzed_fees": "Analyzed fees", "earnings_allowance": "Earnings allowance"})
    fig = px.line(long, x="statement_month", y="amount", color="bank", line_dash="measure", facet_col="bank",
                  color_discrete_map=BANK_COLORS, category_orders={"bank": [b for b in BANK_ORDER if b in banks]},
                  line_dash_map={"Analyzed fees": "solid", "Earnings allowance": "dot"}, markers=True,
                  labels={"statement_month": "", "amount": ""})
    fig.update_traces(line_width=2, marker_size=6, hovertemplate="%{x|%b %Y}: $%{y:,.0f}")
    fig.update_yaxes(matches=None, showticklabels=True, tickprefix="$", tickformat="~s")
    st.plotly_chart(line_style_legend(style(fig, 380), {"Analyzed fees": "solid", "Earnings allowance": "dot"}),
                    width="stretch")
    caption("Each bank has its own scale. Where the dotted line sits above the solid line, the earnings credit "
            "covered that month's fees; below it, the company paid cash. Unused credit expires each month.")

    st.subheader("Top 5 findings by dollar impact")
    top = exc.head(5)[["finding_id", "category", "bank", "service", "savings_type", "dollar_impact", "evidence"]]
    st.dataframe(top, hide_index=True, width="stretch", column_config={
        "dollar_impact": st.column_config.NumberColumn("Impact", format="dollar"),
        "finding_id": "ID", "category": "Category", "bank": "Bank", "service": "Service", "savings_type": "Type",
        "evidence": st.column_config.TextColumn("Evidence", width="large")})


def fee_drivers():
    page_header("Fee Drivers",
                "Where the fees come from: by AFP service family, the handful of services that make up most of the "
                "bill, and whether a service is expensive because of volume or because of price.")
    left, right = st.columns(2)
    with left:
        st.subheader("Fees by AFP family")
        fam = fees.groupby(["family_name", "bank"], as_index=False)["total_charge"].sum()
        order = fam.groupby("family_name")["total_charge"].sum().sort_values().index.tolist()
        fig = px.bar(fam, x="total_charge", y="family_name", color="bank", orientation="h",
                     color_discrete_map=BANK_COLORS, category_orders={"family_name": order, "bank": BANK_ORDER},
                     labels={"total_charge": "", "family_name": ""})
        fig.update_traces(marker_line_color=SURFACE, marker_line_width=2, hovertemplate="%{y}: $%{x:,.0f}")
        fig.update_xaxes(tickprefix="$", tickformat="~s")
        st.plotly_chart(style(fig, 420), width="stretch")
    with right:
        st.subheader("Top services (Pareto)")
        svc = fees.groupby(["afp_description", "bank"], as_index=False)["total_charge"].sum()
        totals = svc.groupby("afp_description")["total_charge"].sum().sort_values(ascending=False)
        top = totals.head(12)
        cum = top.cumsum() / totals.sum()
        n80 = int((totals.cumsum() / totals.sum() < 0.8).sum() + 1)
        fig = px.bar(svc[svc["afp_description"].isin(top.index)], x="total_charge", y="afp_description",
                     color="bank", orientation="h", color_discrete_map=BANK_COLORS,
                     category_orders={"afp_description": list(top.index), "bank": BANK_ORDER},
                     labels={"total_charge": "", "afp_description": ""})
        fig.update_traces(marker_line_color=SURFACE, marker_line_width=2, hovertemplate="%{y}: $%{x:,.0f}")
        for name, value in top.items():
            fig.add_annotation(x=value, y=name, text=f"{cum[name]:.0%}", showarrow=False, xanchor="left",
                               xshift=6, font=dict(color=MUTED, size=11))
        fig.update_xaxes(tickprefix="$", tickformat="~s", range=[0, top.max() * 1.15])
        st.plotly_chart(style(fig, 420), width="stretch")
        caption(f"Labels show the running share of all fees. {n80} services make up 80% of the bill.")

    st.subheader("Volume vs. unit price")
    pts = (fees.assign(cost=fees["total_charge"])
           .groupby(["bank", "afp_code", "afp_description"], as_index=False)
           .agg(volume=("volume", "sum"), cost=("cost", "sum")))
    # The FDIC fee's "volume" is dollars of balance (in $1,000s), not a count, so leave it out here.
    pts = pts[(pts["volume"] > 0) & (pts["afp_code"] != "000230")].assign(unit_price=lambda d: d["cost"] / d["volume"])
    fig = px.scatter(pts, x="volume", y="unit_price", size="cost", color="bank", log_x=True, log_y=True,
                     color_discrete_map=BANK_COLORS, category_orders={"bank": BANK_ORDER}, size_max=40,
                     hover_name="afp_description", labels={"volume": "Volume in period (log)",
                                                          "unit_price": "Average unit price (log)"},
                     hover_data={"cost": ":$,.0f", "volume": ":,.0f", "unit_price": ":$,.3f"})
    fig.update_traces(marker_line_color=SURFACE, marker_line_width=2)
    fig.update_yaxes(tickprefix="$", dtick=1)
    fig.update_xaxes(dtick=1)
    st.plotly_chart(style(fig, 440), width="stretch")
    caption("Bubble size = total cost. Top-right: high volume and high price - the best targets for negotiation. "
            "Top-left: low-volume monthly fees (maintenance, modules) - check they are still needed. "
            "The FDIC assessment is excluded because it is billed on balances, not items.")


def price_benchmarking():
    page_header("Price Benchmarking",
                "Each bank's current unit price compared with an illustrative benchmark range (p25 to p75) and "
                "with the other banks. Prices are shown relative to the benchmark median so services with very "
                "different prices fit on one scale.")
    b = bench.dropna(subset=["median"]).copy()
    b["ratio"] = b["unit_price"] / b["median"]
    codes = b.groupby(["afp_code", "afp_description"], as_index=False).agg(p25=("p25", "first"),
                                                                           median=("median", "first"),
                                                                           p75=("p75", "first"),
                                                                           worst=("ratio", "max"))
    codes = codes.sort_values("worst")
    codes["label"] = codes["afp_code"] + "  " + codes["afp_description"]
    fig = go.Figure()
    fig.add_trace(go.Bar(y=codes["label"], x=(codes["p75"] - codes["p25"]) / codes["median"],
                         base=codes["p25"] / codes["median"], orientation="h", marker_color=BAND,
                         name="Benchmark p25-p75", hoverinfo="skip", width=0.55))
    fig.add_vline(x=1, line_color=AXIS, line_width=1)
    for bank in [x for x in BANK_ORDER if x in banks]:
        d = b[b["bank"] == bank].merge(codes[["afp_code", "label"]], on="afp_code")
        fig.add_trace(go.Scatter(
            y=d["label"], x=d["ratio"], mode="markers", name=bank,
            marker=dict(color=BANK_COLORS[bank], size=11, line=dict(color=SURFACE, width=2)),
            customdata=d[["unit_price", "p25", "median", "p75"]],
            hovertemplate=bank + "<br>%{y}<br>Price $%{customdata[0]:,.3f} "
                          "(%{x:.2f}x median)<br>Benchmark p25 $%{customdata[1]:,.3f} · median "
                          "$%{customdata[2]:,.3f} · p75 $%{customdata[3]:,.3f}<extra></extra>"))
    fig.update_xaxes(title_text="Unit price ÷ illustrative median (1.0 = median)", ticksuffix="x")
    st.plotly_chart(style(fig, max(420, 26 * len(codes) + 80)), width="stretch")
    caption("Gray band = illustrative p25-p75 range. Dots to the right of the band are priced above p75.")

    st.subheader("Above-benchmark services")
    above = b[b["position"] == "Above p75"].sort_values("annual_cost_above_p75", ascending=False)
    st.dataframe(above[["bank", "afp_description", "unit_price", "p75", "median", "annual_volume",
                        "annual_cost_above_p75", "annual_cost_above_median"]],
                 hide_index=True, width="stretch", column_config={
                     "bank": "Bank", "afp_description": "Service", "unit_price": st.column_config.NumberColumn("Price", format="$%.3f"),
                     "p75": st.column_config.NumberColumn("p75", format="$%.3f"),
                     "median": st.column_config.NumberColumn("Median", format="$%.3f"),
                     "annual_volume": st.column_config.NumberColumn("Annual volume", format="localized"),
                     "annual_cost_above_p75": st.column_config.NumberColumn("Annual over p75", format="dollar"),
                     "annual_cost_above_median": st.column_config.NumberColumn("Annual over median", format="dollar")})
    caption(f"Annualized overpayment vs. p75: {money(above['annual_cost_above_p75'].sum())}; "
            f"vs. median: {money(above['annual_cost_above_median'].sum())}.")
    st.subheader("Same service, different banks")
    shared = b[b["banks_offering"] >= 2]
    pivot = shared.pivot_table(index="afp_description", columns="bank", values="unit_price")
    pivot["Benchmark median"] = shared.groupby("afp_description")["median"].first()
    pivot = pivot[[c for c in BANK_ORDER if c in pivot] + ["Benchmark median"]]
    pivot.index.name = "Service"
    st.dataframe(pivot.style.format("${:,.3f}", na_rep="-").highlight_max(
        axis=1, subset=[c for c in BANK_ORDER if c in pivot], color="#fde3d8"),
        width="stretch")
    caption("Highest price among the banks shaded. Useful for moving volume to the cheaper bank.")


def exceptions_page():
    page_header("Exceptions",
                "Every finding, with the calculation behind it and a recommended action. One-time items are "
                "refunds of past overcharges; run-rate items are annual savings going forward; idle cash is "
                "investment income, kept separate from fee savings.")
    c1, c2 = st.columns(2)
    cats = c1.multiselect("Category", sorted(exc["category"].unique()), placeholder="All categories")
    types = c2.multiselect("Savings type", sorted(exc["savings_type"].unique()), placeholder="All types")
    shown = exc[exc["category"].isin(cats or exc["category"]) & exc["savings_type"].isin(types or exc["savings_type"])]
    shown = shown.assign(months_label=shown["months_affected"].map(months_label))
    m = st.columns(3)
    m[0].metric("One-time refunds", money(shown["one_time_recovery"].sum(), cents=True))
    m[1].metric("Annual run-rate savings",
                money(shown.loc[shown["savings_type"] == analyze.RUN_RATE, "annualized_impact"].sum()))
    m[2].metric("Income opportunity (idle cash)",
                money(shown.loc[shown["savings_type"] == analyze.INCOME, "annualized_impact"].sum()))
    st.dataframe(shown[["finding_id", "category", "bank", "account_id", "afp_code", "service", "months_label",
                        "savings_type", "monthly_impact", "annualized_impact", "one_time_recovery", "evidence",
                        "recommended_action"]],
                 hide_index=True, width="stretch", height=560, column_config={
                     "finding_id": "ID", "category": "Category", "bank": "Bank", "service": "Service",
                     "account_id": "Account", "afp_code": "AFP code",
                     "months_label": "Months", "savings_type": "Type",
                     "monthly_impact": st.column_config.NumberColumn("Monthly", format="dollar"),
                     "annualized_impact": st.column_config.NumberColumn("Annualized", format="dollar"),
                     "one_time_recovery": st.column_config.NumberColumn("One-time", format="dollar"),
                     "evidence": st.column_config.TextColumn("Evidence (calculation)", width="large"),
                     "recommended_action": st.column_config.TextColumn("Recommended action", width="medium")})
    st.download_button("Download findings (CSV)", shown.drop(columns="months_label").to_csv(index=False).encode(),
                       "bank_fee_findings.csv",
                       "text/csv")


def what_if(comp, ecr_change_bps, mm_yield):
    """Re-run the compensation math with every ECR shifted and a different money market yield."""
    scen = comp.copy()
    scen["ecr"] = (scen["ecr"] + ecr_change_bps / 10_000).clip(lower=0.0001)
    scen["earnings_allowance"] = analyze.earnings_allowance(scen["avg_collected_balance"], scen["reserve_pct"],
                                                            scen["ecr"], scen["days"], scen["day_basis"])
    scen = analyze.apply_earnings_credit(scen)
    scen["balance_required"] = analyze.balance_required(scen["analyzed_fees"], scen["reserve_pct"], scen["ecr"],
                                                        scen["days"], scen["day_basis"])
    scen["excess_balance"] = scen["avg_collected_balance"] - scen["balance_required"]
    scen["opportunity_cost"] = analyze.opportunity_cost(scen["excess_balance"], scen["days"], mm_yield)
    return scen


def balances_page():
    page_header("Balances & ECR",
                "How each bank's earnings credit compares with its fees, the balance needed to cover fees versus "
                "what is actually held, the cost of idle cash, and each bank's ECR over time. Use the what-if "
                "sliders to test a different ECR or money market yield.")
    shown = [b for b in BANK_ORDER if b in banks]
    st.subheader("Collected balance vs. balance required")
    long = comp.melt(id_vars=["bank", "statement_month"], value_vars=["avg_collected_balance", "balance_required"],
                     var_name="measure", value_name="amount")
    long["measure"] = long["measure"].map({"avg_collected_balance": "Collected balance",
                                           "balance_required": "Balance required"})
    fig = px.line(long, x="statement_month", y="amount", color="bank", line_dash="measure", facet_col="bank",
                  color_discrete_map=BANK_COLORS, category_orders={"bank": shown},
                  line_dash_map={"Collected balance": "solid", "Balance required": "dot"},
                  labels={"statement_month": "", "amount": ""})
    fig.update_traces(line_width=2, hovertemplate="%{x|%b %Y}: $%{y:,.0f}")
    fig.update_yaxes(matches=None, showticklabels=True, tickprefix="$", tickformat="~s")
    st.plotly_chart(line_style_legend(style(fig, 360), {"Collected balance": "solid", "Balance required": "dot"}),
                    width="stretch")
    caption("Each bank has its own scale. Where the solid line is above the dotted line, the bank holds more than "
            "it needs to offset fees; below it, fees are not fully covered.")

    left, right = st.columns(2)
    with left:
        st.subheader("ECR by month")
        fig = px.line(comp, x="statement_month", y="ecr", color="bank", line_shape="hv",
                      color_discrete_map=BANK_COLORS, category_orders={"bank": shown},
                      labels={"statement_month": "", "ecr": ""})
        fig.update_traces(line_width=2, hovertemplate="%{x|%b %Y}: %{y:.2%}")
        fig.update_yaxes(tickformat=".2%", rangemode="tozero")
        st.plotly_chart(style(fig, 280), width="stretch")
    with right:
        st.subheader("Excess balances and idle-cash cost")
        t = (comp.groupby("bank").agg(collected=("avg_collected_balance", "mean"),
                                      required=("balance_required", "mean"),
                                      excess=("excess_balance", "mean"),
                                      opportunity=("opportunity_cost", "sum")).reindex(shown))
        t.index.name = "Bank"
        st.dataframe(t, width="stretch", column_config={
            "collected": st.column_config.NumberColumn("Avg collected", format="dollar"),
            "required": st.column_config.NumberColumn("Avg required", format="dollar"),
            "excess": st.column_config.NumberColumn("Avg excess", format="dollar"),
            "opportunity": st.column_config.NumberColumn("Income forgone", format="dollar")})
        caption(f"Income forgone = positive excess x {config.MONEY_MARKET_YIELD:.1%} money market yield, "
                f"{period_label}. A negative excess means fees were not fully covered.")

    st.subheader("Earnings allowance vs. analyzed fees")
    long = comp.melt(id_vars=["bank", "statement_month"], value_vars=["analyzed_fees", "earnings_allowance"],
                     var_name="measure", value_name="amount")
    long["measure"] = long["measure"].map({"analyzed_fees": "Analyzed fees", "earnings_allowance": "Earnings allowance"})
    fig = px.bar(long, x="statement_month", y="amount", color="measure", barmode="group", facet_col="bank",
                 category_orders={"bank": shown}, labels={"statement_month": "", "amount": ""},
                 color_discrete_map={"Analyzed fees": INK_2, "Earnings allowance": "#86b6ef"})
    fig.update_traces(marker_line_color=SURFACE, marker_line_width=1, hovertemplate="%{x|%b %Y}: $%{y:,.0f}")
    fig.update_yaxes(matches=None, showticklabels=True, tickprefix="$", tickformat="~s")
    fig = style(fig, 340)
    fig.update_layout(legend=dict(yanchor="bottom", y=1.08, xanchor="right", x=1), margin=dict(t=70))
    st.plotly_chart(fig, width="stretch")
    caption("Fees above the allowance are paid in cash (Riverbend from April 2026, after its ECR cut).")

    st.subheader("What-if: ECR and money market yield")
    c1, c2 = st.columns(2)
    bps = c1.slider("Change every bank's ECR by (basis points)", -100, 100, 0, 5)
    mm = c2.slider("Money market yield", 0.0, 6.0, config.MONEY_MARKET_YIELD * 100, 0.25, format="%.2f%%") / 100
    how = {"earnings_allowance": "sum", "net_service_charge": "sum", "excess_balance": "mean",
           "opportunity_cost": "sum"}
    # Baseline and scenario use the same formulas, so an unchanged scenario shows exactly $0 change.
    before = what_if(comp, 0, config.MONEY_MARKET_YIELD).groupby("bank").agg(how).reindex(shown)
    after = what_if(comp, bps, mm).groupby("bank").agg(how).reindex(shown)

    def change(col):
        """Scenario minus actual, as '+$1,234' / '-$1,234', or None when nothing changed."""
        diff = after[col].sum() - before[col].sum()
        return None if abs(diff) < 0.5 else f"{'+' if diff > 0 else '-'}{money(abs(diff))}"

    k = st.columns(3)
    k[0].metric("Net cash charges", money(after["net_service_charge"].sum()),
                delta=change("net_service_charge"), delta_color="inverse")
    k[1].metric("Earnings credit", money(after["earnings_allowance"].sum()), delta=change("earnings_allowance"))
    k[2].metric("Idle-cash income forgone", money(after["opportunity_cost"].sum()),
                delta=change("opportunity_cost"), delta_color="inverse")
    table = after.assign(net_change=after["net_service_charge"] - before["net_service_charge"])
    table.index.name = "Bank"
    st.dataframe(table, width="stretch", column_config={
        "earnings_allowance": st.column_config.NumberColumn("Earnings credit", format="dollar"),
        "net_service_charge": st.column_config.NumberColumn("Net cash charges", format="dollar"),
        "net_change": st.column_config.NumberColumn("Change in cash charges", format="dollar"),
        "excess_balance": st.column_config.NumberColumn("Avg excess balance", format="dollar"),
        "opportunity_cost": st.column_config.NumberColumn("Income forgone", format="dollar")})
    caption(f"Scenario for {period_label}. Excess balance is the monthly average; the other columns are totals.")


def mapping_page():
    page_header("AI Mapping Review",
                "How each bank fee description was matched to an AFP Service Code: printed on the statement by "
                "the bank, matched by text similarity, or suggested by Claude. Anything Claude was unsure about is "
                "flagged for a human to confirm.")
    acc, mp = DATA["accuracy"], DATA["mapping"][DATA["mapping"]["bank"].isin(banks)]
    m = st.columns(4)
    m[0].metric("Descriptions mapped", f"{acc['descriptions']}")
    m[1].metric("Needed mapping", f"{acc['blank_code_descriptions']}")
    m[2].metric("Accuracy (blank codes)", f"{acc['accuracy_where_code_was_blank']:.0%}",
                help="Checked against the synthetic data's known answers when the pipeline ran")
    m[3].metric("Flagged for review", f"{int(mp['needs_review'].astype(str).eq('True').sum())}")
    only = st.toggle("Show only descriptions that needed mapping", value=True)
    view = mp[mp["method"] != "provided"] if only else mp
    st.dataframe(view[["bank", "bank_service_code", "bank_description", "mapped_afp_code", "mapped_afp_description",
                       "method", "confidence", "needs_review", "reasoning"]],
                 hide_index=True, width="stretch", column_config={
                     "bank": "Bank", "method": "Method", "bank_service_code": "Bank code",
                     "bank_description": "Bank's description",
                     "mapped_afp_code": "AFP code", "mapped_afp_description": "AFP description",
                     "confidence": st.column_config.ProgressColumn("Confidence", min_value=0, max_value=1, format="%.2f"),
                     "needs_review": "Needs review", "reasoning": st.column_config.TextColumn("Reasoning", width="large")})
    caption(f"Model: {config.ANTHROPIC_MODEL}. Answers are cached, so this page never calls the API. "
            "Confidence below 0.70 is flagged for review.")


def memo_for_display(text):
    """Memo Markdown sized to sit inside the page: smaller headings, kept line breaks, and $ escaped
    so Markdown does not read dollar amounts as math."""
    text = re.sub(r"^## ", "#### ", text, flags=re.MULTILINE)
    text = re.sub(r"^# ", "### ", text, flags=re.MULTILINE)
    return text.replace("$", "\\$").replace("\n", "  \n")


def memo_page():
    page_header("Negotiation Memo",
                "A one-page negotiation brief for each bank, drafted by Claude from that bank's findings. Claude "
                "only sees a summary of the findings (never raw statements), and every dollar figure in the memo "
                "is checked against the findings data.")
    options = [b for b in BANK_ORDER if b in banks]
    left, right = st.columns([2, 1])
    bank = left.selectbox("Bank", options)
    has_key = config.has_api_key()
    right.markdown("<div style='height: 1.75rem'></div>", unsafe_allow_html=True)
    if right.button("Regenerate memo", disabled=not has_key, width="stretch",
                    help="Draft a fresh memo with Claude (about $0.002)" if has_key
                    else "Set ANTHROPIC_API_KEY in .env to enable regeneration"):
        with st.spinner(f"Drafting the {bank} memo..."):
            try:
                memo.generate_memo(bank, force=True)
            except Exception as error:  # show the problem instead of crashing the page
                st.error(f"Could not draft the memo: {type(error).__name__}: {error}")
                return
        st.rerun()

    record, summary = memo.load_memo(bank), memo.build_summary(bank)
    if record is None:
        st.info("No memo has been drafted for this bank yet. Run `python -m src.memo` with an API key.")
        return
    caption(f"Drafted by {record['model']} on {record['generated_at']}.")
    if memo.is_stale(record, summary):
        st.warning("The findings have changed since this memo was drafted. Regenerate it to bring it up to date.")
    check = memo.check_figures(record["memo_markdown"], summary)  # re-checked against today's findings
    if check["mismatches"]:
        st.warning(f"{len(check['mismatches'])} of {check['checked']} dollar figures in this memo do not appear "
                   f"in the findings data: {', '.join(check['mismatches'])}. Verify them before sending.")
    else:
        st.success(f"All {check['checked']} dollar figures in this memo match the findings data.")
    with st.container(border=True):
        st.markdown(memo_for_display(record["memo_markdown"]))
    st.download_button("Download memo (Markdown)", record["memo_markdown"].encode(),
                       f"{memo.bank_slug(bank)}_negotiation_memo.md", "text/markdown")
    with st.expander("What Claude was given (findings summary sent with the prompt)"):
        st.json(record["summary"], expanded=False)


{
    "Executive Summary": executive_summary, "Fee Drivers": fee_drivers, "Price Benchmarking": price_benchmarking,
    "Exceptions": exceptions_page, "Balances & ECR": balances_page, "AI Mapping Review": mapping_page,
    "Negotiation Memo": memo_page,
}[page]()
footer()
