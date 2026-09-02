"""NYC boba joints dashboard.  Run: `just dashboard` (container) or `just dashboard-local`."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import text

from boba.db import engine

st.set_page_config(page_title="NYC boba joints", page_icon="🧋", layout="wide")
THIS_YEAR = dt.date.today().year
SINCE = 2022

# plotly legends toggle categories on click, so colour is the interactive filter
STATUS_COLOR = {"open": "#26a69a", "closed": "#e57373", "unknown": "#b0bec5"}
KIND_COLOR = {"opened": "#26a69a", "closed": "#e57373"}
ALL_IDENT = ["overture_category", "both", "name_pattern", "propagated"]


@st.cache_data(ttl=300)
def load_shops() -> pd.DataFrame:
    return pd.read_sql(
        text(
            """
            select s.id, s.name, s.borough, s.status, s.status_basis, s.identified_by,
                   s.opened_date, s.opened_source, s.opened_precision,
                   s.closed_date, s.closed_source,
                   (s.overture_id is not null) as has_overture,
                   (s.camis is not null)      as has_dohmh,
                   o.brand,
                   coalesce(
                       o.addr_freeform,
                       nullif(trim(concat_ws(' ', e.building, e.street)), '')
                   ) as address,
                   st_x(s.geom) as lon, st_y(s.geom) as lat
            from boba_shops s
            left join overture_places o      on o.id = s.overture_id
            left join dohmh_establishments e on e.camis = s.camis
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

# --- sidebar filters -----------------------------------------------------
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
    "Open shops: DOHMH-verified only",
    help="drop 'open' shops whose only signal is Overture's operating_status",
)

f = shops[
    shops["borough"].isin(f_boro)
    & shops["identified_by"].isin(f_ident)
    & shops["status"].isin(f_status)
]
if f_brand:
    f = f[f["brand"].isin(f_brand)]
if f_verified:
    f = f[(f["status"] != "open") | (f["status_basis"] == "dohmh_active")]

op = f.dropna(subset=["opened_date"])
cl = f[(f["status"] == "closed") & f["closed_date"].notna()]

DATE_COLS = {
    "opened_date": st.column_config.DateColumn("opened", format="YYYY-MM-DD"),
    "closed_date": st.column_config.DateColumn("closed", format="YYYY-MM-DD"),
}

# --- header + KPIs ----------------------------------------------------
st.title("🧋 NYC boba joints — openings & closings, 2022–2026")
st.caption(
    "Overture Places (boba label + location) joined to NYC DOHMH inspections (the timeline). "
    "Counts are a **lower bound** — shops are identified by category/name, not a ground-truth list."
)
opn = f["status"] == "open"
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Boba shops", len(f))
k2.metric("Open · DOHMH-verified", int((opn & (f["status_basis"] == "dohmh_active")).sum()))
k3.metric("Open · Overture's word", int((opn & (f["status_basis"] == "overture_open")).sum()))
k4.metric("Closed", int((f["status"] == "closed").sum()))
k5.metric("Unknown", int((f["status"] == "unknown").sum()))
st.caption(
    ":grey[**DOHMH-verified** = inspected within ~18 months. **Overture's word** = only "
    "Overture's `operating_status` says so — unreliable (it lags real closures). "
    "**unknown** = no signal either way.]"
)

# --- recent activity (exact dates) --------------------------------
a1, a2 = st.columns(2)
a1.markdown("**Most recent openings**")
a1.dataframe(
    op.nlargest(15, "opened_date")[
        ["opened_date", "name", "brand", "address", "borough", "opened_source"]
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

# --- Timeline -------------------------------------------------------
with tab_tl:
    years = list(range(SINCE, THIS_YEAR + 1))
    tl = pd.DataFrame({"year": years})
    tl["opened"] = tl["year"].map(op["opened_date"].dt.year.value_counts()).fillna(0).astype(int)
    tl["closed"] = tl["year"].map(cl["closed_date"].dt.year.value_counts()).fillna(0).astype(int)
    tl["net"] = tl["opened"] - tl["closed"]
    tl["active (cumulative)"] = tl["net"].cumsum()

    long = tl.melt("year", ["opened", "closed"], "kind", "count")
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
    st.dataframe(tl.set_index("year"), width="stretch")

    st.markdown("**Every opening / closing on a date axis** — click a borough to hide it")
    ev = pd.concat(
        [
            op.assign(kind="opened", date=op["opened_date"]),
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
    d1.markdown(f"**Opened in {yr}**")
    d1.dataframe(
        op[op["opened_date"].dt.year == yr][
            ["opened_date", "name", "brand", "address", "borough", "opened_source"]
        ].sort_values("opened_date"),
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
        "Openings are DOHMH first-inspection dates (month precision). "
        f"{int(f['opened_date'].isna().sum())} filtered shops have no known opening date "
        "(no DOHMH match) and are absent from the timeline."
    )

# --- Map ---------------------------------------------------------
with tab_map:
    color_by = st.radio(
        "Colour by", ["status", "status_basis", "identified_by", "borough"], horizontal=True
    )
    m = f.dropna(subset=["lon", "lat"]).copy()
    m["brand"] = m["brand"].fillna("")
    m["opened"] = m["opened_date"].dt.strftime("%Y-%m-%d").fillna("—")
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
            "opened": True,
            "closed": True,
            "lat": False,
            "lon": False,
        },
        map_style="carto-positron",  # clean grey basemap so the dots pop
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
        f"{len(m)} of {len(f)} filtered shops have coordinates. "
        "Click a legend entry to hide that group."
    )

# --- Shops table ------------------------------------------------
with tab_tbl:
    cols = [
        "name",
        "brand",
        "borough",
        "status",
        "status_basis",
        "address",
        "opened_date",
        "opened_precision",
        "opened_source",
        "closed_date",
        "closed_source",
        "identified_by",
    ]
    view = f[cols].sort_values("opened_date", ascending=False, na_position="last")
    st.dataframe(view, width="stretch", hide_index=True, column_config=DATE_COLS)
    st.download_button("Download CSV", view.to_csv(index=False), "boba_shops.csv", "text/csv")

# --- Data quality --------------------------------------------
with tab_dq:
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("How shops were identified")
        prov = f["identified_by"].value_counts().rename_axis("identified_by").reset_index(name="n")
        st.plotly_chart(
            px.bar(prov, x="n", y="identified_by", orientation="h", height=280).update_layout(
                yaxis_title="", xaxis_title="", margin=dict(t=10, b=0)
            ),
            width="stretch",
        )
        st.caption(
            "`overture_category` / `both` rest on Overture's curated `bubble_tea` tag; "
            "`name_pattern` is regex-only; `propagated` is pure spatial inference."
        )
    with c2:
        st.subheader("Match score distribution")
        mt = load_matches()
        st.plotly_chart(
            px.histogram(mt, x="score", nbins=25, height=280).update_layout(
                yaxis_title="", margin=dict(t=10, b=0)
            ),
            width="stretch",
        )
        st.caption(f"{len(mt)} Overture↔DOHMH matches. Low scores → review in notebook 03.")

    st.subheader("What each status rests on")
    sb = f.groupby(["status", "status_basis"]).size().reset_index(name="n")
    st.dataframe(sb.sort_values("n", ascending=False), width="stretch", hide_index=True)
    st.caption(
        "`overture_open` = we're trusting Overture's `operating_status` with nothing "
        "to corroborate it; those 'open's are the least trustworthy."
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
            first_opened=("opened_date", "min"),
            latest_opened=("opened_date", "max"),
        )
        .sort_values("shops", ascending=False)
        .head(25)
    )
    st.dataframe(
        ch,
        width="stretch",
        column_config={
            "first_opened": st.column_config.DateColumn(format="YYYY-MM-DD"),
            "latest_opened": st.column_config.DateColumn(format="YYYY-MM-DD"),
        },
    )
    st.caption("Brands from Overture. Only shops matched to an Overture place with a brand appear.")
