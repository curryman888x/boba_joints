"""NYC boba joints dashboard.  Run: `just dashboard` (or `streamlit run dashboard/app.py`)."""

from __future__ import annotations

import datetime as dt

import altair as alt
import pandas as pd
import pydeck as pdk
import streamlit as st
from sqlalchemy import text

from boba.db import engine

st.set_page_config(page_title="NYC boba joints", page_icon="🧋", layout="wide")
THIS_YEAR = dt.date.today().year
SINCE = 2022

STATUS_COLOR = {
    "open": [38, 166, 154, 180],
    "closed": [229, 115, 115, 210],
    "unknown": [144, 164, 174, 150],
}


@st.cache_data(ttl=300)
def load_shops() -> pd.DataFrame:
    return pd.read_sql(
        text(
            """
            select s.id, s.name, s.borough, s.status, s.identified_by,
                   s.opened_date, s.opened_source, s.opened_precision,
                   s.closed_date, s.closed_source,
                   (s.overture_id is not null) as has_overture,
                   (s.camis is not null) as has_dohmh,
                   o.brand,
                   st_x(s.geom) as lon, st_y(s.geom) as lat
            from boba_shops s
            left join overture_places o on o.id = s.overture_id
            """
        ),
        engine,
        parse_dates=["opened_date", "closed_date"],
    )


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


@st.cache_data(ttl=300)
def load_matches() -> pd.DataFrame:
    return pd.read_sql(
        text("select score, name_similarity, distance_m, method from place_matches"), engine
    )


shops = load_shops()

# --- sidebar filters -------------------------------------------------------
st.sidebar.header("Filters")
boroughs = sorted(b for b in shops["borough"].dropna().unique())
f_boro = st.sidebar.multiselect("Borough", boroughs, default=boroughs)
f_ident = st.sidebar.multiselect(
    "Identified by",
    ["overture_category", "both", "name_pattern", "propagated"],
    default=["overture_category", "both", "name_pattern", "propagated"],
)
f_status = st.sidebar.multiselect(
    "Status", ["open", "closed", "unknown"], default=["open", "closed"]
)

f = shops[
    shops["borough"].isin(f_boro)
    & shops["identified_by"].isin(f_ident)
    & shops["status"].isin(f_status)
]

# --- KPIs ---------------------------------------------------------------
st.title("🧋 NYC boba joints — openings & closings, 2022–2026")
st.caption(
    "Overture Places (boba label + location) joined to NYC DOHMH inspections (the timeline). "
    "Counts are a **lower bound** — shops are identified by category/name, not a ground-truth list."
)
k1, k2, k3, k4 = st.columns(4)
k1.metric("Boba shops", len(f))
k2.metric("Open now", int((f["status"] == "open").sum()))
k3.metric("Closed", int((f["status"] == "closed").sum()))
k4.metric("With a known opening date", int(f["opened_date"].notna().sum()))

tab_tl, tab_map, tab_tbl, tab_dq, tab_chain = st.tabs(
    ["Timeline", "Map", "Shops", "Data quality", "By chain"]
)

# --- Timeline ---------------------------------------------------------
with tab_tl:
    years = list(range(SINCE, THIS_YEAR + 1))
    op = f.dropna(subset=["opened_date"])
    cl = f[(f["status"] == "closed") & f["closed_date"].notna()]
    tl = pd.DataFrame({"year": years})
    tl["opened"] = tl["year"].map(op["opened_date"].dt.year.value_counts()).fillna(0).astype(int)
    tl["closed"] = tl["year"].map(cl["closed_date"].dt.year.value_counts()).fillna(0).astype(int)
    tl["net"] = tl["opened"] - tl["closed"]
    tl["active (cumulative)"] = tl["net"].cumsum()

    long = tl.melt("year", ["opened", "closed"], "kind", "count")
    bars = (
        alt.Chart(long)
        .mark_bar()
        .encode(
            x=alt.X("year:O", title=None),
            y=alt.Y("count:Q", title="shops"),
            color=alt.Color(
                "kind:N",
                scale=alt.Scale(domain=["opened", "closed"], range=["#26a69a", "#e57373"]),
                title=None,
            ),
            xOffset="kind:N",
            tooltip=["year", "kind", "count"],
        )
        .properties(height=320)
    )
    st.altair_chart(bars, width="stretch")
    st.dataframe(tl.set_index("year"), width="stretch")
    st.info(
        "Openings are DOHMH first-inspection dates (month precision). "
        f"{int(f['opened_date'].isna().sum())} filtered shops have no known opening date "
        "(no DOHMH match) and are absent from the bars."
    )

# --- Map ------------------------------------------------------------
with tab_map:
    m = f.dropna(subset=["lon", "lat"]).copy()
    m["color"] = m["status"].map(STATUS_COLOR)
    st.pydeck_chart(
        pdk.Deck(
            map_style="light",
            initial_view_state=pdk.ViewState(latitude=40.72, longitude=-73.94, zoom=10.2),
            layers=[
                pdk.Layer(
                    "ScatterplotLayer",
                    m,
                    get_position=["lon", "lat"],
                    get_fill_color="color",
                    get_radius=70,
                    radius_min_pixels=3,
                    radius_max_pixels=14,
                    pickable=True,
                )
            ],
            tooltip={"text": "{name}\n{borough} · {status} · {identified_by}"},
        )
    )
    st.caption(
        f"{len(m)} of {len(f)} filtered shops have coordinates.  🟢 open  🔴 closed  ⚪ unknown"
    )

# --- Shops table ----------------------------------------------------
with tab_tbl:
    cols = [
        "name",
        "borough",
        "status",
        "identified_by",
        "opened_date",
        "opened_source",
        "closed_date",
        "closed_source",
        "brand",
        "has_overture",
        "has_dohmh",
    ]
    st.dataframe(f[cols].sort_values("opened_date", na_position="last"), width="stretch")
    st.download_button("Download CSV", f[cols].to_csv(index=False), "boba_shops.csv", "text/csv")

# --- Data quality -------------------------------------------------
with tab_dq:
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("How shops were identified")
        prov = f["identified_by"].value_counts().rename_axis("identified_by").reset_index(name="n")
        st.altair_chart(
            alt.Chart(prov)
            .mark_bar()
            .encode(x="n:Q", y=alt.Y("identified_by:N", sort="-x"), tooltip=["identified_by", "n"]),
            width="stretch",
        )
        st.caption(
            "`overture_category` / `both` rest on Overture's curated `bubble_tea` tag; "
            "`name_pattern` is regex-only; `propagated` is pure spatial inference."
        )
    with c2:
        st.subheader("Match score distribution")
        mt = load_matches()
        st.altair_chart(
            alt.Chart(mt)
            .mark_bar()
            .encode(x=alt.X("score:Q", bin=alt.Bin(maxbins=25)), y="count()", tooltip=["count()"]),
            width="stretch",
        )
        st.caption(f"{len(mt)} Overture↔DOHMH matches. Low scores → review in notebook 03.")

    st.subheader("Ingest manifest")
    man = load_manifest()
    st.dataframe(man, width="stretch")

# --- By chain -----------------------------------------------------
with tab_chain:
    ch = (
        f.dropna(subset=["brand"])
        .groupby("brand")["status"]
        .value_counts()
        .unstack(fill_value=0)
        .assign(total=lambda d: d.sum(axis=1))
        .sort_values("total", ascending=False)
        .head(20)
    )
    st.dataframe(ch, width="stretch")
    st.caption("Brands from Overture. Only shops matched to an Overture place with a brand appear.")
