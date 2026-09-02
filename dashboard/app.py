"""NYC boba joints dashboard.  `just dashboard` (container) or `just dashboard-local`."""

from __future__ import annotations

import datetime as dt
import re

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import text

from boba.db import engine

st.set_page_config(page_title="NYC boba joints", page_icon="🧋", layout="wide")
THIS_YEAR = dt.date.today().year
SINCE = 2022

STATUS_COLOR = {"open": "#26a69a", "closed": "#e57373", "unknown": "#b0bec5"}
KIND_COLOR = {"first seen": "#26a69a", "closed": "#e57373"}
ALL_IDENT = ["yelp_category", "name_pattern"]
VERIFIED = ["dohmh_active", "yelp_open"]

# Canonical chain names, matched against the shop name (brand is not a stored
# field -- Overture used to supply it; now we derive it for the big chains).
_BRANDS = {
    "Kung Fu Tea": r"kung\s*fu\s*tea",
    "Gong Cha": r"gong\s*cha",
    "CoCo": r"\bcoco\b.*(?:tea|fresh|bubble)|coco\s*fresh",
    "Chatime": r"chatime",
    "ViVi Bubble Tea": r"vivi\s*bubble",
    "Sharetea": r"share\s*tea|sharetea",
    "Tiger Sugar": r"tiger\s*sugar",
    "Happy Lemon": r"happy\s*lemon",
    "Yi Fang": r"yi\s*fang",
    "Xing Fu Tang": r"xing\s*fu\s*tang",
    "Machi Machi": r"machi\s*machi",
    "The Alley": r"the\s*alley",
    "Ten Ren": r"ten\s*ren",
    "Möge Tee": r"m[o0]ge\s*tee",
    "Meet Fresh": r"meet\s*fresh",
    "Quickly": r"quickly",
    "Boba Guys": r"boba\s*guys",
    "HeyTea": r"hey\s*tea|heytea",
    "Molly Tea": r"molly\s*tea",
    "Auntea Jenny": r"auntea",
    "TP Tea": r"\btp\s*tea\b",
    "Chagee": r"chagee",
    "Sunright Tea Studio": r"sunright",
    "Truedan": r"truedan",
    "Come Buy": r"come\s*buy|comebuy",
}
_BRAND_RES = [(name, re.compile(pat, re.I)) for name, pat in _BRANDS.items()]


def brand_of(name: str | None) -> str | None:
    for canon, rx in _BRAND_RES:
        if name and rx.search(name):
            return canon
    return None


@st.cache_data(ttl=300)
def load_shops() -> pd.DataFrame:
    df = pd.read_sql(
        text(
            """
            select s.id, s.name, s.borough, s.status, s.status_basis, s.identified_by,
                   s.first_seen_date, s.first_seen_source, s.last_seen_date,
                   s.closed_date, s.closed_source,
                   coalesce(
                       nullif(trim(concat_ws(' ', e.building, e.street)), ''),
                       y.address
                   ) as address,
                   y.rating as yelp_rating, y.review_count as yelp_reviews, y.url as yelp_url,
                   st_x(s.geom) as lon, st_y(s.geom) as lat
            from boba_shops s
            left join dohmh_establishments e on e.camis = s.camis
            left join yelp_businesses y      on y.yelp_id = s.yelp_id
            """
        ),
        engine,
        parse_dates=["first_seen_date", "last_seen_date", "closed_date"],
    )
    df["brand"] = df["name"].map(brand_of)
    return df


@st.cache_data(ttl=300)
def load_manifest() -> pd.DataFrame:
    return pd.read_sql(
        text(
            """
            select distinct on (source) source, status, started_at, finished_at,
                   row_count, kept_count, min_date, max_date, detail
            from ingest_runs order by source, started_at desc
            """
        ),
        engine,
    )


shops = load_shops()

# --- sidebar filters --------------------------------------------------
st.sidebar.header("Filters")
boroughs = sorted(b for b in shops["borough"].dropna().unique())
brands = sorted(b for b in shops["brand"].dropna().unique())
f_boro = st.sidebar.multiselect("Borough", boroughs, default=boroughs)
f_ident = st.sidebar.multiselect("Identified by", ALL_IDENT, default=ALL_IDENT)
f_status = st.sidebar.multiselect(
    "Status", ["open", "closed", "unknown"], default=["open", "closed", "unknown"]
)
f_brand = st.sidebar.multiselect("Brand (blank = all)", brands)
f_verified = st.sidebar.checkbox(
    "Open shops: verified only",
    help="keep only 'open' shops backed by a recent DOHMH inspection or Yelp",
)

f = shops[
    shops["borough"].isin(f_boro)
    & shops["identified_by"].isin(f_ident)
    & shops["status"].isin(f_status)
]
if f_brand:
    f = f[f["brand"].isin(f_brand)]
if f_verified:
    f = f[(f["status"] != "open") | (f["status_basis"].isin(VERIFIED))]

fs = f.dropna(subset=["first_seen_date"])
cl = f[(f["status"] == "closed") & f["closed_date"].notna()]

DATE_COLS = {
    "first_seen_date": st.column_config.DateColumn("first seen", format="YYYY-MM-DD"),
    "last_seen_date": st.column_config.DateColumn("last seen", format="YYYY-MM-DD"),
    "closed_date": st.column_config.DateColumn("closed", format="YYYY-MM-DD"),
}

# --- header + KPIs --------------------------------------------------
st.title("🧋 NYC boba joints — 2022–2026")
st.caption(
    "Discovered from Yelp's curated `bubbletea` category; NYC DOHMH inspections supply "
    "the timeline. Counts are a **lower bound**; **dates are evidence bounds, not "
    "lifecycle events** (see below)."
)
opn = f["status"] == "open"
verified = opn & f["status_basis"].isin(VERIFIED)
k1, k2, k3, k4 = st.columns(4)
k1.metric("Boba shops", len(f))
k2.metric("Open · verified", int(verified.sum()))
k3.metric("Closed", int((f["status"] == "closed").sum()))
k4.metric("Unknown", int((f["status"] == "unknown").sum()))
st.caption(
    ":grey[**first seen** = first DOHMH health inspection — this *lags* the real opening by "
    "the permit gap, so read it as *operating by* this date, ±1 quarter. Only shops with a "
    "DOHMH match have one. **verified** = a DOHMH inspection within ~18 months, or Yelp "
    "`is_closed=false`. **unknown** = no positive signal (a stale record can't be told from "
    "a live one).]"
)

# --- recent activity -----------------------------------------------
a1, a2 = st.columns(2)
a1.markdown("**Most recently first seen**")
a1.dataframe(
    fs.nlargest(15, "first_seen_date")[
        ["first_seen_date", "name", "brand", "address", "borough", "first_seen_source"]
    ],
    hide_index=True,
    width="stretch",
    column_config=DATE_COLS,
)
a2.markdown("**Most recent closings**")
a2.dataframe(
    cl.nlargest(15, "closed_date")[
        ["closed_date", "name", "brand", "address", "borough", "closed_source"]
    ],
    hide_index=True,
    width="stretch",
    column_config=DATE_COLS,
)

tab_tl, tab_map, tab_tbl, tab_dq, tab_chain = st.tabs(
    ["Timeline", "Map", "Shops", "Data quality", "By chain"]
)

# --- Timeline ----------------------------------------------------
with tab_tl:
    years = list(range(SINCE, THIS_YEAR + 1))
    tl = pd.DataFrame({"year": years})
    tl["first seen"] = (
        tl["year"].map(fs["first_seen_date"].dt.year.value_counts()).fillna(0).astype(int)
    )
    tl["closed"] = tl["year"].map(cl["closed_date"].dt.year.value_counts()).fillna(0).astype(int)

    long = tl.melt("year", ["first seen", "closed"], "kind", "count")
    fig = px.bar(
        long,
        x="year",
        y="count",
        color="kind",
        barmode="group",
        color_discrete_map=KIND_COLOR,
        height=320,
    )
    fig.update_layout(legend_title_text="", xaxis_title="", margin=dict(t=10, b=0))
    st.plotly_chart(fig, width="stretch")
    st.caption(
        "'first seen' ≈ openings but dated by first inspection (lags, ±1 quarter). "
        "'closed' is a DOHMH forced-closure or Yelp `is_closed` — noisy and lagging. "
        "Inspection silence is *unknown*, not closed."
    )
    st.dataframe(tl.set_index("year"), width="stretch")

    st.markdown("**Every first-seen / closing on a date axis** — click a borough to hide it")
    ev = pd.concat(
        [
            fs.assign(kind="first seen", date=fs["first_seen_date"]),
            cl.assign(kind="closed", date=cl["closed_date"]),
        ]
    )[["date", "kind", "name", "brand", "borough"]]
    ev["brand"] = ev["brand"].fillna("")
    fig = px.strip(
        ev,
        x="date",
        y="kind",
        color="borough",
        hover_name="name",
        hover_data={"brand": True, "date": "|%Y-%m-%d", "kind": False},
        stripmode="overlay",
        height=260,
    )
    fig.update_traces(marker={"size": 7, "opacity": 0.6}, jitter=0.8)
    fig.update_layout(xaxis_title="", yaxis_title="", legend_title_text="", margin=dict(t=10, b=0))
    st.plotly_chart(fig, width="stretch")

    yr = st.selectbox("List shops for year", years[::-1])
    d1, d2 = st.columns(2)
    d1.markdown(f"**First seen in {yr}**")
    d1.dataframe(
        fs[fs["first_seen_date"].dt.year == yr][
            ["first_seen_date", "name", "brand", "address", "borough", "first_seen_source"]
        ].sort_values("first_seen_date"),
        hide_index=True,
        width="stretch",
        column_config=DATE_COLS,
    )
    d2.markdown(f"**Closed in {yr}**")
    d2.dataframe(
        cl[cl["closed_date"].dt.year == yr][
            ["closed_date", "name", "brand", "address", "borough", "closed_source"]
        ].sort_values("closed_date"),
        hide_index=True,
        width="stretch",
        column_config=DATE_COLS,
    )
    st.info(
        f"{int(f['first_seen_date'].isna().sum())} filtered shops have no first-seen date "
        "(no DOHMH match) and are absent from the timeline."
    )

# --- Map -------------------------------------------------------
with tab_map:
    color_by = st.radio(
        "Colour by", ["status", "status_basis", "identified_by", "borough"], horizontal=True
    )
    m = f.dropna(subset=["lon", "lat"]).copy()
    m["brand"] = m["brand"].fillna("")
    m["first_seen"] = m["first_seen_date"].dt.strftime("%Y-%m-%d").fillna("—")
    m["closed"] = m["closed_date"].dt.strftime("%Y-%m-%d").fillna("—")
    fig = px.scatter_map(
        m,
        lat="lat",
        lon="lon",
        color=color_by,
        color_discrete_map=STATUS_COLOR if color_by == "status" else {},
        hover_name="name",
        hover_data={
            "brand": True,
            "borough": True,
            "status": True,
            "status_basis": True,
            "first_seen": True,
            "closed": True,
            "lat": False,
            "lon": False,
        },
        map_style="carto-positron",
        center={"lat": 40.72, "lon": -73.94},
        zoom=10.3,
        height=700,
    )
    fig.update_traces(marker={"size": 11, "opacity": 0.85})
    fig.update_layout(
        margin=dict(t=0, b=0, l=0, r=0),
        legend_title_text="",
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
    )
    st.plotly_chart(fig, width="stretch")
    st.caption(
        f"{len(m)} of {len(f)} filtered shops have coordinates. Click a legend entry to hide it."
    )

# --- Shops table --------------------------------------------
with tab_tbl:
    cols = [
        "name",
        "brand",
        "borough",
        "status",
        "status_basis",
        "address",
        "first_seen_date",
        "first_seen_source",
        "last_seen_date",
        "closed_date",
        "closed_source",
        "identified_by",
        "yelp_rating",
        "yelp_reviews",
    ]
    view = f[cols].sort_values("first_seen_date", ascending=False, na_position="last")
    st.dataframe(view, width="stretch", hide_index=True, column_config=DATE_COLS)
    st.download_button("Download CSV", view.to_csv(index=False), "boba_shops.csv", "text/csv")

# --- Data quality ------------------------------------------
with tab_dq:
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("How shops were identified")
        prov = f["identified_by"].value_counts().rename_axis("identified_by").reset_index(name="n")
        st.plotly_chart(
            px.bar(prov, x="n", y="identified_by", orientation="h", height=200).update_layout(
                yaxis_title="", xaxis_title="", margin=dict(t=10, b=0)
            ),
            width="stretch",
        )
        st.caption(
            "`yelp_category` — from Yelp's curated `bubbletea` search (primary). "
            "`name_pattern` — a boba-named DOHMH health permit that Yelp doesn't list "
            "(mostly older closures)."
        )
    with c2:
        st.subheader("Shops with a first-seen date")
        has_date = f["first_seen_date"].notna().sum()
        st.metric("have a DOHMH-derived date", f"{has_date} / {len(f)}")
        st.caption(
            "A first-seen date needs a DOHMH inspection match. Yelp-only shops "
            "(no match, often too new to have been inspected) have none."
        )

    st.subheader("What each status rests on")
    sb = f.groupby(["status", "status_basis"]).size().reset_index(name="n")
    st.dataframe(sb.sort_values("n", ascending=False), width="stretch", hide_index=True)
    st.caption(
        "`dohmh_active` / `yelp_open` are checked. `dohmh_inactive` = 18+ months without an "
        "inspection and nothing else says open → *unknown*, not closed."
    )

    st.subheader("Ingest manifest")
    st.dataframe(load_manifest(), width="stretch", hide_index=True)

# --- By chain ----------------------------------------------
with tab_chain:
    ch = (
        f.dropna(subset=["brand"])
        .groupby("brand")
        .agg(
            shops=("id", "count"),
            open=("status", lambda s: (s == "open").sum()),
            closed=("status", lambda s: (s == "closed").sum()),
            earliest_seen=("first_seen_date", "min"),
            latest_seen=("first_seen_date", "max"),
        )
        .sort_values("shops", ascending=False)
        .head(25)
    )
    st.dataframe(
        ch,
        width="stretch",
        column_config={
            "earliest_seen": st.column_config.DateColumn(format="YYYY-MM-DD"),
            "latest_seen": st.column_config.DateColumn(format="YYYY-MM-DD"),
        },
    )
    st.caption(
        "Brand is inferred from the shop name against a list of ~25 known chains "
        "(`dashboard/app.py:_BRANDS`); independents don't appear here."
    )
