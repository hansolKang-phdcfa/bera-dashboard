"""BERA Dashboard — Public Web Version
======================================
Core tracks: Core (5/18, v1) | Core (5/28, v2) | Core (6/5, v3) | Core (7/17, v4) | Satellite v2
No strategy parameters exposed. No local DB dependency.
Portfolio data loaded from data/portfolios.json (separate from UI code).
"""
import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.express as px
import plotly.graph_objects as go
import json, os
from datetime import datetime, timedelta

st.set_page_config(page_title="BERA Trading", page_icon="🧬", layout="wide")

# ═══ Load Portfolio Data from JSON ═══
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
with open(os.path.join(DATA_DIR, 'portfolios.json'), 'r', encoding='utf-8') as f:
    PF = json.load(f)

LIVE = PF['core_live']
CAB = PF['core_ab']
SAT = PF['satellite']
CNEW = PF['core_new']
CV2 = PF['core_v2_0717']

# ═══ Helpers ═══

@st.cache_data(ttl=300)
def get_prices_batch(tickers):
    """Fetch live intraday prices via fast_info.lastPrice (true current price).

    yfinance's daily endpoint can lag for some tickers (NaN on latest day even
    after the session closes), which would mix stale closes with fresh ones.
    fast_info.lastPrice goes through Yahoo's quote endpoint and stays current.
    """
    tickers = list(set(tickers))
    result = {}
    for tk in tickers:
        try:
            lp = float(yf.Ticker(tk).fast_info.get('lastPrice', 0))
            if lp > 0:
                result[tk] = lp
        except Exception:
            pass
    # Last-resort fallback: 5d daily batch, take latest non-NaN
    missing = [tk for tk in tickers if tk not in result]
    if missing:
        try:
            data = yf.download(missing, period='5d', interval='1d', progress=False)
            close = data['Close'] if 'Close' in data.columns else data
            if isinstance(close, pd.Series):
                close = close.to_frame(name=missing[0])
            for tk in missing:
                if tk in close.columns:
                    vals = close[tk].dropna()
                    if not vals.empty:
                        result[tk] = float(vals.iloc[-1])
        except Exception:
            pass
    return result

@st.cache_data(ttl=600)
def get_bench_data(entry_date, entry_prices):
    # Data-driven: iterate whatever benchmarks the portfolio's bench dict carries.
    # Core 계열 → IBB(+SPY/QQQ), Satellite 계열 → XBI(+SPY/QQQ). 계열별 bench는 portfolios.json에서 관리.
    result = {}
    for sym, ep in entry_prices.items():
        if not ep: continue
        try:
            t = yf.Ticker(sym)
            hist = t.history(start=entry_date, interval='1d')
            if hist.empty: continue
            close = hist['Close'].dropna()
            if close.empty: continue
            # 장중/장외 시간에 daily가 당일 close를 NaN으로 채우는 quirk → fast_info.lastPrice로 패치
            live = None
            try:
                lp = t.fast_info.get('lastPrice')
                if lp and lp > 0: live = float(lp)
            except Exception:
                pass
            cur = live if live else float(close.iloc[-1])
            ret = (cur - ep) / ep * 100
            normed = (close / ep - 1) * 100
            if live:
                today = pd.Timestamp.now(tz=close.index.tz).normalize()
                if today > close.index[-1].normalize():
                    normed.loc[today] = (live - ep) / ep * 100
                else:
                    normed.iloc[-1] = (live - ep) / ep * 100
            result[sym] = {'current': cur, 'ret': ret, 'series': normed}
        except Exception:
            pass
    return result


@st.cache_data(ttl=600)
def get_portfolio_daily(tickers_qty_entry, entry_date, sl_events=None):
    """Calculate daily portfolio cumulative return series.

    sl_events: list of {'date': 'YYYY-MM-DD', 'old_portfolio': [(tk,qty,ep),...]}
    Before sl_date: use old_portfolio (includes SL'd stock).
    After sl_date: use tickers_qty_entry (redistributed, SL'd stock removed).
    Denominator: always old_portfolio cost (= original investment).
    """
    try:
        tickers_qty_entry = list(tickers_qty_entry)
        all_tickers = set(t[0] for t in tickers_qty_entry)
        old_pf = None
        sl_date = None
        if sl_events:
            ev = sl_events[0]
            old_pf = ev['old_portfolio']
            sl_date = pd.Timestamp(ev['date'])
            for t in old_pf:
                all_tickers.add(t[0])
        all_tickers = list(all_tickers)

        data = yf.download(all_tickers, start=entry_date, interval='1d', progress=False)
        if data.empty:
            return None
        close = data['Close'] if 'Close' in data.columns else data
        if isinstance(close, pd.Series):
            close = close.to_frame(name=all_tickers[0])

        # Latest-day NaN fix: yfinance daily can lag for some tickers; pull
        # fast_info.lastPrice to fill the last row so today's close is real.
        if not close.empty:
            last_date = close.index[-1]
            for tk in close.columns:
                v = close.loc[last_date, tk]
                if pd.isna(v) or v <= 0:
                    try:
                        lp = float(yf.Ticker(tk).fast_info.get('lastPrice', 0))
                        if lp > 0:
                            close.loc[last_date, tk] = lp
                    except Exception:
                        pass
        # Mid-series NaN: forward-fill from prior close (avoids entry-price spike)
        close = close.ffill()

        # Delisted/untradeable holdings (no price data anywhere in the window, e.g.
        # SLNO) are dropped from BOTH numerator and denominator — same as the
        # headline metric, which values them at entry (0 PnL) and excludes their
        # cost from orig_cost. Keeping them would peg the position flat at entry
        # while still counting its cost, diluting the % return below the metric.
        def _dead(tk):
            return tk not in close.columns or close[tk].notna().sum() == 0
        dead = {tk for tk, _, _ in (old_pf or [])} | {tk for tk, _, _ in tickers_qty_entry}
        dead = {tk for tk in dead if _dead(tk)}

        # Denominator: original cost (old portfolio if SL, else current), ex-dead
        cost_pf = old_pf if old_pf else tickers_qty_entry
        total_cost = sum(qty * ep for tk, qty, ep in cost_pf if tk not in dead)
        if total_cost <= 0:
            return None

        daily_vals = []
        for date in close.index:
            pf = old_pf if (old_pf and sl_date and date < sl_date) else tickers_qty_entry

            port_val = 0
            for tk, qty, ep in pf:
                if tk in dead:
                    continue
                if tk in close.columns:
                    p = close.loc[date, tk]
                    if pd.notna(p) and p > 0:
                        port_val += qty * p
                    else:
                        port_val += qty * ep
                else:
                    port_val += qty * ep
            daily_vals.append({'date': date, 'ret': (port_val / total_cost - 1) * 100})
        if not daily_vals:
            return None
        return pd.DataFrame(daily_vals).set_index('date')['ret']
    except:
        return None


def show_bench(total_pnl_pct, entry_date, bench_prices, label, portfolio=None, sl_events=None,
               bera_daily_override=None):
    st.markdown(f"### vs Benchmarks (since {entry_date})")
    bench = get_bench_data(entry_date, bench_prices)
    syms = list(bench_prices.keys())
    cols = st.columns(1 + len(syms))
    cols[0].metric(f"BERA {label}", f"{total_pnl_pct:+.2f}%")
    for i, sym in enumerate(syms):
        if sym in bench:
            r = bench[sym]['ret']
            cols[i+1].metric(sym, f"{r:+.2f}%", delta=f"{total_pnl_pct - r:+.2f}%p")
    if bench:
        fig = go.Figure()
        # BERA as line chart (same as benchmarks). Override lets callers pass a
        # precomputed daily equity curve (e.g. SL + redistribution) instead of buy&hold.
        if bera_daily_override is not None:
            bera_daily = bera_daily_override
        else:
            bera_daily = get_portfolio_daily(portfolio, entry_date, sl_events=sl_events) if portfolio else None
        if bera_daily is not None:
            fig.add_trace(go.Scatter(
                x=bera_daily.index, y=bera_daily.values,
                mode='lines+markers',
                name=f"BERA {label} ({total_pnl_pct:+.1f}%)",
                line=dict(color='#1976D2', width=3),
                marker=dict(size=4),
            ))
        else:
            fig.add_hline(y=total_pnl_pct, line_dash="solid", line_color="#1976D2",
                          annotation_text=f"BERA {total_pnl_pct:+.1f}%", annotation_position="bottom right")
        fig.add_hline(y=0, line_dash="dot", line_color="gray")
        clr = {'XBI': '#E53935', 'IBB': '#FB8C00', 'SPY': '#43A047', 'QQQ': '#7B1FA2',
               'SOXX': '#00838F'}
        for sym in syms:
            if sym in bench:
                s = bench[sym]['series']
                fig.add_trace(go.Scatter(x=s.index, y=s.values,
                    name=f"{sym} ({bench[sym]['ret']:+.1f}%)",
                    line=dict(color=clr.get(sym, 'gray'), width=2)))
        fig.update_layout(yaxis_title='Return (%)', height=380,
                          hovermode='x unified',
                          margin=dict(t=30),
                          legend=dict(orientation="h", yanchor="bottom", y=1.02))
        st.caption(f"Cumulative Return since {entry_date}")
        st.plotly_chart(fig, use_container_width=True)


def show_charts(df):
    col_a, col_b = st.columns(2)
    with col_a:
        fig = px.pie(df, values='Value', names='Ticker', title='Weight', hole=0.4)
        st.plotly_chart(fig, use_container_width=True)
    with col_b:
        s = df.sort_values('PnL%')
        colors = ['#2ecc71' if v >= 0 else '#e74c3c' for v in s['PnL%']]
        fig = go.Figure(go.Bar(x=s['PnL%'], y=s['Ticker'], orientation='h', marker_color=colors))
        fig.update_layout(title='PnL (%)', xaxis_title='%')
        st.plotly_chart(fig, use_container_width=True)


@st.cache_data(ttl=900)
def compute_shared_tracker(tickers, entry_date, sl, vol_mult, drop_th, hold):
    """Live-replay a past Signal Board snapshot with the real Satellite exit rules.

    Entry = open on entry_date. Daily close-based SL + vol-exit + time-stop, and
    equal-weight redistribution of freed cash into survivors (fractional shares).
    Returns (portfolio_ret%, per-name rows, XBI ret%, n_tickers). Mirrors
    satellite_hybrid_test.py exit logic.
    """
    O, C, V = {}, {}, {}
    for tk in list(tickers) + ['XBI']:
        try:
            h = yf.Ticker(tk).history(start='2026-04-20', interval='1d', auto_adjust=True)
            h = h[['Open', 'Close', 'Volume']].dropna()
            if not h.empty:
                O[tk], C[tk], V[tk] = h['Open'], h['Close'], h['Volume']
        except Exception:
            pass
    cl = pd.DataFrame(C).sort_index().ffill()
    if cl.empty:
        return None, [], None, 0
    op = pd.DataFrame(O).reindex(cl.index).ffill()
    vol = pd.DataFrame(V).reindex(cl.index).ffill()
    tks = [t for t in tickers if t in cl.columns]
    days = [d for d in cl.index if str(d.date()) >= entry_date]
    if not days or not tks:
        return None, [], None, 0
    e0 = days[0]
    entry = {tk: float(op.loc[e0, tk]) for tk in tks}

    val = {tk: 1.0 / len(tks) for tk in tks}
    alive = set(tks); cash = 0.0
    status = {tk: '보유' for tk in tks}; exret = {}; expx = {}
    daily = {}
    for i, d in enumerate(days):
        loc_d = cl.index.get_loc(d)
        pv = cl.index[loc_d - 1]
        for tk in list(alive):
            m = (cl.loc[d, tk] / op.loc[d, tk]) if i == 0 else (cl.loc[d, tk] / cl.loc[pv, tk])
            if np.isfinite(m):
                val[tk] *= m
        ex = []
        for tk in list(alive):
            pt = cl.loc[d, tk]; ep = entry[tk]; pp = cl.loc[pv, tk]
            if i + 1 >= hold:
                ex.append((tk, 'time')); continue
            if ep > 0 and (pt - ep) / ep <= sl:
                ex.append((tk, 'SL')); continue
            vloc = vol.index.get_loc(d)
            av = vol[tk].iloc[max(0, vloc - 20):vloc].mean()
            if av > 0 and pp > 0 and vol.loc[d, tk] > av * vol_mult and (pt - pp) / pp <= drop_th:
                ex.append((tk, 'vol'))
        if ex:
            freed = sum(val[tk] for tk, _ in ex)
            for tk, why in ex:
                alive.discard(tk); val[tk] = 0.0
                status[tk] = f"{why}@{str(d.date())[5:]}"
                exret[tk] = (cl.loc[d, tk] - entry[tk]) / entry[tk] * 100
                expx[tk] = float(cl.loc[d, tk])
            if alive:
                add = freed / len(alive)
                for tk in alive:
                    val[tk] += add
            else:
                cash += freed
        daily[d] = (sum(val.values()) + cash - 1) * 100
    port_ret = (sum(val.values()) + cash - 1) * 100

    last = cl.index[-1]
    rows = []
    for tk in tks:
        if status[tk] != '보유':
            cur = expx[tk]; r = exret[tk]
        else:
            cur = float(cl.loc[last, tk]); r = (cur - entry[tk]) / entry[tk] * 100
        rows.append({'Ticker': tk, 'Entry': round(entry[tk], 2), 'Current': round(cur, 2),
                     'PnL%': round(r, 1), '상태': status[tk]})
    xbi_ret = None
    if 'XBI' in cl.columns:
        xbi_ret = (float(cl.loc[last, 'XBI']) / float(op.loc[e0, 'XBI']) - 1) * 100
    daily_series = pd.Series(daily).sort_index()
    return port_ret, rows, xbi_ret, len(tks), daily_series


# ═══ Sidebar ═══
st.sidebar.title("BERA")
st.sidebar.caption("Biotech Event-driven Research & Alpha")
st.sidebar.markdown("---")

TERMINAL_PAGE = "🛰️ Signal Terminal"
# Nav grouped by STRATEGY FAMILY so the two lanes are obvious at a glance:
#   🔵 Core 계열     = 중대형주(시총 $2B+) 위주, 벤치마크 IBB
#   🟢 Satellite 계열 = 소형주 포함 이벤트드리븐, 벤치마크 XBI (Signal Terminal 포함)
OVERVIEW_PAGES = ["📊 Summary", "📈 종목별 상세"]
CORE_PAGES = [
    "🧬 Quality Score",
    "💰 Core (5/18, v1)",
    "🅰️ Core (5/28, v2)",
    "🏛️ Core (6/5, v3)",
    "🚀 Core (7/17, v4)",
]
SATELLITE_PAGES = [
    "🎯 Satellite v2 (Paper)",
]
INSTITUTIONAL_PAGES = [TERMINAL_PAGE]  # Satellite 계열 · URL 게이트

if 'page' not in st.session_state:
    st.session_state.page = "🧬 Quality Score"

def _nav(p):
    st.session_state.page = p

def _nav_button(label):
    is_current = st.session_state.page == label
    st.sidebar.button(
        label, width='stretch',
        type="primary" if is_current else "secondary",
        on_click=_nav, args=(label,), key=f"nav_{label}",
    )

st.sidebar.markdown("**Overview**")
for label in OVERVIEW_PAGES:
    _nav_button(label)

st.sidebar.markdown("**🔵 Core 계열**")
st.sidebar.caption("중대형주 · 시총 $2B+ · vs IBB")
for label in CORE_PAGES:
    _nav_button(label)

st.sidebar.markdown("**🟢 Satellite 계열**")
st.sidebar.caption("소형주 포함 · 이벤트드리븐 · vs XBI")
for label in SATELLITE_PAGES:
    _nav_button(label)

# Signal Terminal is a Satellite-family institutional page, hidden unless the URL
# carries ?institutional (e.g. bera-dashboard.streamlit.app/?institutional).
# Rendered under the Satellite group so its strategy family reads clearly.
_inst_unlocked = "institutional" in st.query_params
if _inst_unlocked:
    for label in INSTITUTIONAL_PAGES:
        _nav_button(label)
elif st.session_state.page in INSTITUTIONAL_PAGES:
    # Stale session landed on the terminal without the unlock param — bounce out.
    st.session_state.page = "🧬 Quality Score"

page = st.session_state.page

st.sidebar.markdown("---")
if st.sidebar.button("Refresh"):
    st.cache_data.clear()
st.sidebar.caption("Updated: 2026-06-15")


# ═══ Page: Core Live ═══
if page == "💰 Core (5/18, v1)":
    st.title("Core Portfolio -- Live Trading")
    st.caption("🔵 Core 계열 · 중대형주(시총 $2B+) · 벤치마크 IBB")
    st.markdown(LIVE['entry_note'])
    if LIVE.get('sl_warning'):
        st.warning(LIVE['sl_warning'])
    st.markdown("---")

    tickers = [p['ticker'] for p in LIVE['portfolio']]
    prices = get_prices_batch(tickers)

    rows = []
    for p in LIVE['portfolio']:
        cur = prices.get(p['ticker'], p['entry'])
        if cur <= 0: cur = p['entry']
        cost = p['qty'] * p['entry']
        val = p['qty'] * cur
        pnl = val - cost
        rows.append({'Ticker': p['ticker'], 'Qty': p['qty'], 'Entry': p['entry'],
                      'Current': cur, 'Value': val, 'PnL': pnl,
                      'PnL%': pnl/cost*100 if cost > 0 else 0})

    df = pd.DataFrame(rows)
    tc = df['Value'].sum(); tp = df['PnL'].sum() - LIVE['sl_loss']
    tpp = tp / LIVE['orig_cost'] * 100

    c1, c2, c3 = st.columns(3)
    c1.metric("Portfolio Value", f"${tc:,.0f}")
    c2.metric("Total PnL", f"${tp:+,.0f}", delta=f"{tpp:+.2f}%")
    c3.metric("Stocks", f"{len(LIVE['portfolio'])}")

    st.dataframe(df.sort_values('Value', ascending=False).style.format({
        'Entry': '${:.2f}', 'Current': '${:.2f}', 'Value': '${:,.0f}',
        'PnL': '${:+,.0f}', 'PnL%': '{:+.1f}%'
    }).map(lambda v: 'color:#2ecc71' if isinstance(v,(int,float)) and v>0 else
                ('color:#e74c3c' if isinstance(v,(int,float)) and v<0 else ''),
                subset=['PnL','PnL%']),
        width='stretch', hide_index=True)

    show_charts(df)
    st.markdown("---")
    live_tqe = tuple((p['ticker'], p['qty'], p['entry']) for p in LIVE['portfolio'])
    # SL-aware daily chart: use old portfolio before SL date
    sl_ev = None
    if LIVE.get('sl_event'):
        ev = LIVE['sl_event']
        sl_ev = [{'date': ev['date'],
                   'old_portfolio': [(s['ticker'], s['qty'], s['entry']) for s in ev['old_portfolio']]}]
    show_bench(tpp, LIVE['entry_date'], LIVE['bench'], "Core Live",
               portfolio=live_tqe, sl_events=sl_ev)


# ═══ Page: Core (5/28, v2) ═══
elif page == "🅰️ Core (5/28, v2)":
    st.title("Core (5/28, v2) -- Paper Trading")
    st.caption("🔵 Core 계열 · 중대형주(시총 $2B+) · 벤치마크 IBB")
    st.markdown(CAB['entry_note'])
    if CAB.get('sl_warning'):
        st.warning(CAB['sl_warning'])
    st.markdown("---")

    all_tickers = list(set(
        [s['ticker'] for s in CAB['core_a']] +
        [s['ticker'] for s in CAB['core_b']] +
        [s['ticker'] for s in CAB['defense']]
    ))
    prices = get_prices_batch(all_tickers)

    last_tpp = 0
    for label, core_stocks_raw in [("Core A", CAB['core_a']), ("Core B", CAB['core_b'])]:
        st.markdown(f"### {label}")

        # Core stocks
        st.markdown(f"**Core ({len(core_stocks_raw)} stocks, {CAB['macro']*100:.0f}% allocation)**")
        rows = []
        for s in core_stocks_raw:
            tk, qty, ep = s['ticker'], s['qty'], s['entry']
            cur = prices.get(tk, ep)
            if cur <= 0: cur = ep
            cost = qty * ep; val = qty * cur; pnl = val - cost
            rows.append({'Ticker': tk, 'Qty': qty, 'Entry': ep, 'Current': cur,
                          'Value': val, 'PnL': pnl, 'PnL%': pnl/cost*100 if cost>0 else 0})
        cdf = pd.DataFrame(rows)

        # Defense basket
        defense_raw = CAB['defense']
        st.markdown(f"**Defense ({len(defense_raw)} stocks, {(1-CAB['macro'])*100:.0f}% allocation)**")
        drows = []
        for s in defense_raw:
            tk, qty, ep = s['ticker'], s['qty'], s['entry']
            cur = prices.get(tk, ep)
            if cur <= 0: cur = ep
            cost = qty * ep; val = qty * cur; pnl = val - cost
            drows.append({'Ticker': tk, 'Qty': qty, 'Entry': ep, 'Current': cur,
                           'Value': val, 'PnL': pnl, 'PnL%': pnl/cost*100 if cost>0 else 0})
        ddf = pd.DataFrame(drows)

        # Combined
        combined = pd.concat([cdf, ddf], ignore_index=True)
        tc = combined['Value'].sum(); tp = combined['PnL'].sum() - CAB['sl_loss']
        tpp = tp / CAB['orig_cost'] * 100
        core_pnl = cdf['PnL'].sum()
        def_pnl = ddf['PnL'].sum()

        c1, c2, c3, c4 = st.columns(4)
        c1.metric(f"{label} Total", f"${tc:,.0f}", delta=f"{tpp:+.2f}%")
        c2.metric("Core PnL", f"${core_pnl:+,.0f}")
        c3.metric("Defense PnL", f"${def_pnl:+,.0f}")
        c4.metric("Stocks", f"{len(core_stocks_raw)} + {len(defense_raw)}")

        fmt = {'Entry': '${:.2f}', 'Current': '${:.2f}', 'Value': '${:,.0f}',
               'PnL': '${:+,.0f}', 'PnL%': '{:+.1f}%'}
        style_fn = lambda v: 'color:#2ecc71' if isinstance(v,(int,float)) and v>0 else \
                    ('color:#e74c3c' if isinstance(v,(int,float)) and v<0 else '')

        tab1, tab2 = st.tabs(["Core", "Defense"])
        with tab1:
            st.dataframe(cdf.sort_values('PnL%', ascending=False).style.format(fmt).map(style_fn, subset=['PnL','PnL%']),
                width='stretch', hide_index=True)
        with tab2:
            st.dataframe(ddf.sort_values('PnL%', ascending=False).style.format(fmt).map(style_fn, subset=['PnL','PnL%']),
                width='stretch', hide_index=True)

        # Aggregate duplicate tickers (GILD, LLY in both Core & Defense) for charts
        chart_df = combined.groupby('Ticker', as_index=False).agg({
            'Value': 'sum', 'PnL': 'sum', 'Qty': 'sum',
            'Entry': 'first', 'Current': 'first',
        })
        chart_df['PnL%'] = chart_df.apply(
            lambda r: r['PnL'] / (r['Value'] - r['PnL']) * 100 if (r['Value'] - r['PnL']) > 0 else 0, axis=1)
        show_charts(chart_df)
        st.markdown("---")
        core_tqe = [(s['ticker'], s['qty'], s['entry']) for s in core_stocks_raw]
        def_tqe = [(s['ticker'], s['qty'], s['entry']) for s in defense_raw]
        if label == "Core A":
            a_tpp = tpp
            a_tqe = tuple(core_tqe + def_tqe)

    # SL-aware daily chart for Core A/B (use Core A + Defense, old portfolio before SL)
    cab_sl_ev = None
    if CAB.get('sl_event'):
        ev = CAB['sl_event']
        old_core = [(s['ticker'], s['qty'], s['entry']) for s in ev['old_core_a']]
        old_def = [(s['ticker'], s['qty'], s['entry']) for s in CAB['defense']]
        cab_sl_ev = [{'date': ev['date'], 'old_portfolio': old_core + old_def}]
    show_bench(a_tpp, CAB['entry_date'], CAB['bench'], "Core (5/28, v2)",
               portfolio=a_tqe, sl_events=cab_sl_ev)


# ═══ Page: Satellite Config H ═══
elif page == "🎯 Satellite v2 (Paper)":
    st.title("Satellite v2 -- Paper Tracking")
    st.caption("🟢 Satellite 계열 · 소형주 포함 · 벤치마크 XBI")
    st.markdown(SAT['entry_note'])
    st.markdown(SAT['backtest_note'])
    st.markdown("---")

    tickers = [p['ticker'] for p in SAT['portfolio']]
    prices = get_prices_batch(tickers)

    rows = []
    for p in SAT['portfolio']:
        cur = prices.get(p['ticker'], p['entry'])
        if cur <= 0: cur = p['entry']
        alloc = SAT['seed_usd'] * p['weight_pct'] / 100
        qty = int(alloc / p['entry'])
        cost = qty * p['entry']; val = qty * cur; pnl = val - cost
        rows.append({'Ticker': p['ticker'], 'Weight': f"{p['weight_pct']}%",
                      'Prob': p['prob'], 'Entry': p['entry'], 'Current': cur,
                      'Value': val, 'PnL': pnl, 'PnL%': pnl/cost*100 if cost>0 else 0,
                      'Smart Money': p['smart_money']})

    df = pd.DataFrame(rows)
    tc = df['Value'].sum(); tp = df['PnL'].sum()
    tpp = tp / (tc - tp) * 100 if (tc - tp) > 0 else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Active", f"{len(SAT['portfolio'])} / {SAT['max_slots']} slots")
    c2.metric("Invested", f"${tc:,.0f}")
    c3.metric("PnL", f"${tp:+,.0f}", delta=f"{tpp:+.2f}%")
    c4.metric("Slots Available", f"{SAT['max_slots'] - len(SAT['portfolio'])}")

    st.dataframe(df.style.format({
        'Prob': '{:.3f}', 'Entry': '${:.2f}', 'Current': '${:.2f}',
        'Value': '${:,.0f}', 'PnL': '${:+,.0f}', 'PnL%': '{:+.1f}%'
    }).map(lambda v: 'color:#2ecc71' if isinstance(v,(int,float)) and v>0 else
                ('color:#e74c3c' if isinstance(v,(int,float)) and v<0 else ''),
                subset=['PnL','PnL%']),
        width='stretch', hide_index=True)

    show_charts(df)
    st.markdown("---")
    sat_tqe = tuple((p['ticker'], int(SAT['seed_usd'] * p['weight_pct'] / 100 / p['entry']), p['entry']) for p in SAT['portfolio'])
    show_bench(tpp, SAT['entry_date'], SAT['bench'], "Satellite", portfolio=sat_tqe)


# ═══ Page: Core New ═══
elif page == "🏛️ Core (6/5, v3)":
    st.title("Core (6/5, v3) -- Paper Tracking")
    st.caption("🔵 Core 계열 · 중대형주(시총 $2B+) · 벤치마크 IBB")
    st.markdown(CNEW['entry_note'])
    st.markdown(CNEW['backtest_note'])
    st.markdown("---")

    tickers = [p['ticker'] for p in CNEW['portfolio']]
    prices = get_prices_batch(tickers)
    total_mult = sum(p['weight_mult'] for p in CNEW['portfolio'])

    rows = []
    for p in CNEW['portfolio']:
        cur = prices.get(p['ticker'], p['entry'])
        if cur <= 0: cur = p['entry']
        alloc = CNEW['seed_usd'] * p['weight_mult'] / total_mult
        qty = max(1, int(alloc / p['entry']))
        cost = qty * p['entry']; val = qty * cur; pnl = val - cost
        rows.append({'Ticker': p['ticker'],
                      'Weight': f"{p['weight_mult']/total_mult*100:.1f}%",
                      'Prob': p['prob'], 'Entry': p['entry'], 'Current': cur,
                      'Value': val, 'PnL': pnl, 'PnL%': pnl/cost*100 if cost>0 else 0})

    df = pd.DataFrame(rows)
    tc = df['Value'].sum(); tp = df['PnL'].sum()
    tpp = tp / (tc - tp) * 100 if (tc - tp) > 0 else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Active", f"{len(CNEW['portfolio'])} / {CNEW['max_slots']} slots")
    c2.metric("Invested", f"${tc:,.0f}")
    c3.metric("PnL", f"${tp:+,.0f}", delta=f"{tpp:+.2f}%")
    c4.metric("Slots Available", f"{CNEW['max_slots'] - len(CNEW['portfolio'])}")

    st.dataframe(df.style.format({
        'Prob': '{:.3f}', 'Entry': '${:.2f}', 'Current': '${:.2f}',
        'Value': '${:,.0f}', 'PnL': '${:+,.0f}', 'PnL%': '{:+.1f}%'
    }).map(lambda v: 'color:#2ecc71' if isinstance(v,(int,float)) and v>0 else
                ('color:#e74c3c' if isinstance(v,(int,float)) and v<0 else ''),
                subset=['PnL','PnL%']),
        width='stretch', hide_index=True)

    show_charts(df)
    st.markdown("---")
    cnew_tqe = tuple((p['ticker'], max(1, int(CNEW['seed_usd'] * p['weight_mult'] / total_mult / p['entry'])), p['entry']) for p in CNEW['portfolio'])
    show_bench(tpp, CNEW['entry_date'], CNEW['bench'], "Core (6/5, v3)", portfolio=cnew_tqe)


# ═══ Page: Core (7/17, v4) ═══
elif page == "🚀 Core (7/17, v4)":
    st.title("Core (7/17, v4) -- 2026-07-17 신규 발굴 트랙")
    st.caption("🔵 Core 계열 · Core v2 전략(순수 prob top20 + SI-drop) · 시총 $2B+ · 벤치 IBB/SPY/QQQ · 동일가중")
    st.markdown(CV2['entry_note'])
    st.markdown(CV2['backtest_note'])
    st.markdown("---")

    tickers = [p['ticker'] for p in CV2['portfolio']]
    prices = get_prices_batch(tickers)
    total_mult = sum(p['weight_mult'] for p in CV2['portfolio'])

    rows = []
    for p in CV2['portfolio']:
        cur = prices.get(p['ticker'], p['entry'])
        if cur <= 0: cur = p['entry']
        alloc = CV2['seed_usd'] * p['weight_mult'] / total_mult
        qty = max(1, int(alloc / p['entry']))
        cost = qty * p['entry']; val = qty * cur; pnl = val - cost
        rows.append({'Ticker': p['ticker'],
                      'Weight': f"{p['weight_mult']/total_mult*100:.1f}%",
                      'Prob': p['prob'], 'Entry': p['entry'], 'Current': cur,
                      'Value': val, 'PnL': pnl, 'PnL%': pnl/cost*100 if cost>0 else 0})

    df = pd.DataFrame(rows)
    tc = df['Value'].sum(); tp = df['PnL'].sum()
    tpp = tp / (tc - tp) * 100 if (tc - tp) > 0 else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Active", f"{len(CV2['portfolio'])} / {CV2['max_slots']} slots")
    c2.metric("Invested", f"${tc:,.0f}")
    c3.metric("PnL", f"${tp:+,.0f}", delta=f"{tpp:+.2f}%")
    c4.metric("Slots Available", f"{CV2['max_slots'] - len(CV2['portfolio'])}")

    st.dataframe(df.style.format({
        'Prob': '{:.3f}', 'Entry': '${:.2f}', 'Current': '${:.2f}',
        'Value': '${:,.0f}', 'PnL': '${:+,.0f}', 'PnL%': '{:+.1f}%'
    }).map(lambda v: 'color:#2ecc71' if isinstance(v,(int,float)) and v>0 else
                ('color:#e74c3c' if isinstance(v,(int,float)) and v<0 else ''),
                subset=['PnL','PnL%']),
        width='stretch', hide_index=True)

    show_charts(df)
    st.markdown("---")
    cv2_tqe = tuple((p['ticker'], max(1, int(CV2['seed_usd'] * p['weight_mult'] / total_mult / p['entry'])), p['entry']) for p in CV2['portfolio'])
    show_bench(tpp, CV2['entry_date'], CV2['bench'], "Core (7/17, v4)", portfolio=cv2_tqe)


# ═══ Page: 종목별 상세 (Per-stock Detail) ═══
elif page == "📈 종목별 상세":
    st.title("종목별 상세")
    st.caption("포트폴리오 보유 종목의 3개월 캔들차트와 진입가 대비 현재 손익")
    st.markdown("---")

    # ── 전 포트폴리오 포지션 통합 (대시보드에 표시되는 종목들) ──
    cnew_total_mult = sum(p['weight_mult'] for p in CNEW['portfolio'])
    positions = {
        "💰 Core (5/18, v1)": [
            {'ticker': p['ticker'], 'qty': p['qty'], 'entry': p['entry']}
            for p in LIVE['portfolio']
        ],
        "🅰️ Core A (Paper)": [
            {'ticker': s['ticker'], 'qty': s['qty'], 'entry': s['entry']}
            for s in CAB['core_a']
        ],
        "🅱️ Core B (Paper)": [
            {'ticker': s['ticker'], 'qty': s['qty'], 'entry': s['entry']}
            for s in CAB['core_b']
        ],
        "🛡️ Defense (A/B)": [
            {'ticker': s['ticker'], 'qty': s['qty'], 'entry': s['entry']}
            for s in CAB['defense']
        ],
        "🎯 Satellite v2": [
            {'ticker': p['ticker'],
             'qty': int(SAT['seed_usd'] * p['weight_pct'] / 100 / p['entry']),
             'entry': p['entry']}
            for p in SAT['portfolio']
        ],
        "🏛️ Core (6/5, v3)": [
            {'ticker': p['ticker'],
             'qty': max(1, int(CNEW['seed_usd'] * p['weight_mult'] / cnew_total_mult / p['entry'])),
             'entry': p['entry']}
            for p in CNEW['portfolio']
        ],
    }

    csel1, csel2 = st.columns(2)
    pf_label = csel1.selectbox("포트폴리오", list(positions.keys()))
    pf_positions = positions[pf_label]
    selected = csel2.selectbox("종목", [p['ticker'] for p in pf_positions])

    pos = next(p for p in pf_positions if p['ticker'] == selected)
    qty, entry = pos['qty'], pos['entry']

    cur = get_prices_batch([selected]).get(selected, entry)
    if cur <= 0:
        cur = entry
    cost = qty * entry
    val = qty * cur
    pnl = val - cost
    pnl_pct = pnl / cost * 100 if cost > 0 else 0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("종목", selected)
    m2.metric("수량", f"{qty}주")
    m3.metric("평가금액", f"${val:,.0f}")
    m4.metric("손익", f"${pnl:+,.0f}", delta=f"{pnl_pct:+.2f}%")

    st.markdown(f"**{pf_label}** · 진입가 ${entry:,.2f} · 현재가 ${cur:,.2f}")

    # ── 3개월 캔들차트 + 진입가 기준선 ──
    try:
        hist = yf.Ticker(selected).history(period='3mo')
        if not hist.empty:
            fig = go.Figure()
            fig.add_trace(go.Candlestick(
                x=hist.index, open=hist['Open'], high=hist['High'],
                low=hist['Low'], close=hist['Close'], name=selected,
            ))
            fig.add_hline(y=entry, line_dash="dash", line_color="#1976D2",
                          annotation_text=f"진입가 ${entry:,.2f}",
                          annotation_position="top left")
            fig.update_layout(title=f"{selected} — 3M Chart",
                              xaxis_rangeslider_visible=False,
                              height=460, margin=dict(t=40))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.warning("차트 데이터를 불러오지 못했습니다.")
    except Exception as e:
        st.warning(f"차트 로딩 실패: {e}")


# ═══ Page: Summary ═══
elif page == "📊 Summary":
    st.title("Portfolio Summary")
    st.markdown("All BERA portfolios at a glance.")
    st.markdown("---")

    summaries = []

    # Core Live
    tickers = [p['ticker'] for p in LIVE['portfolio']]
    px_live = get_prices_batch(tickers)
    tc = 0; tv = 0
    for p in LIVE['portfolio']:
        cur = px_live.get(p['ticker'], p['entry'])
        if cur <= 0: cur = p['entry']
        tc += p['qty'] * p['entry']; tv += p['qty'] * cur
    pnl = tv - tc - LIVE['sl_loss']
    summaries.append({'Portfolio': 'Core (5/18, v1)', 'Family': '🔵 Core', 'Entry': LIVE['entry_date'],
                       'Seed': f"${LIVE['seed_usd']:,}", 'Stocks': len(LIVE['portfolio']),
                       'Value': tv, 'PnL': pnl, 'PnL%': pnl/LIVE['orig_cost']*100 if LIVE['orig_cost']>0 else 0,
                       'Days': (pd.Timestamp.now()-pd.Timestamp(LIVE['entry_date'])).days})

    # Core A (core + defense)
    all_ab = list(set([s['ticker'] for s in CAB['core_a']] + [s['ticker'] for s in CAB['defense']]))
    px_ab = get_prices_batch(all_ab)
    tc = 0; tv = 0
    for s in CAB['core_a'] + CAB['defense']:
        tk, qty, ep = s['ticker'], s['qty'], s['entry']
        cur = px_ab.get(tk, ep)
        if cur <= 0: cur = ep
        tc += qty * ep; tv += qty * cur
    pnl = tv - tc - CAB['sl_loss']
    summaries.append({'Portfolio': 'Core A (Paper)', 'Family': '🔵 Core', 'Entry': CAB['entry_date'],
                       'Seed': f"${CAB['seed_usd']:,}", 'Stocks': len(CAB['core_a'])+len(CAB['defense']),
                       'Value': tv, 'PnL': pnl, 'PnL%': pnl/CAB['orig_cost']*100 if CAB['orig_cost']>0 else 0,
                       'Days': (pd.Timestamp.now()-pd.Timestamp(CAB['entry_date'])).days})

    # Satellite v2
    tickers = [p['ticker'] for p in SAT['portfolio']]
    px_sat = get_prices_batch(tickers)
    tc = 0; tv = 0
    for p in SAT['portfolio']:
        cur = px_sat.get(p['ticker'], p['entry'])
        if cur <= 0: cur = p['entry']
        q = int(SAT['seed_usd'] * p['weight_pct'] / 100 / p['entry'])
        tc += q * p['entry']; tv += q * cur
    summaries.append({'Portfolio': 'Satellite v2 (Paper)', 'Family': '🟢 Satellite', 'Entry': SAT['entry_date'],
                       'Seed': f"${SAT['seed_usd']:,}", 'Stocks': len(SAT['portfolio']),
                       'Value': tv, 'PnL': tv-tc, 'PnL%': (tv-tc)/tc*100 if tc>0 else 0,
                       'Days': (pd.Timestamp.now()-pd.Timestamp(SAT['entry_date'])).days})

    # Core v2
    tickers = [p['ticker'] for p in CNEW['portfolio']]
    px_cnew = get_prices_batch(tickers)
    tm = sum(p['weight_mult'] for p in CNEW['portfolio'])
    tc = 0; tv = 0
    for p in CNEW['portfolio']:
        cur = px_cnew.get(p['ticker'], p['entry'])
        if cur <= 0: cur = p['entry']
        q = max(1, int(CNEW['seed_usd'] * p['weight_mult'] / tm / p['entry']))
        tc += q * p['entry']; tv += q * cur
    summaries.append({'Portfolio': 'Core (6/5, v3)', 'Family': '🔵 Core', 'Entry': CNEW['entry_date'],
                       'Seed': f"${CNEW['seed_usd']:,}", 'Stocks': len(CNEW['portfolio']),
                       'Value': tv, 'PnL': tv-tc, 'PnL%': (tv-tc)/tc*100 if tc>0 else 0,
                       'Days': (pd.Timestamp.now()-pd.Timestamp(CNEW['entry_date'])).days})

    sdf = pd.DataFrame(summaries)
    st.dataframe(sdf.style.format({
        'Value': '${:,.0f}', 'PnL': '${:+,.0f}', 'PnL%': '{:+.2f}%',
    }).map(lambda v: 'color:#2ecc71' if isinstance(v,(int,float)) and v>0 else
                ('color:#e74c3c' if isinstance(v,(int,float)) and v<0 else ''),
                subset=['PnL','PnL%']),
        width='stretch', hide_index=True)

    # Total
    total_val = sdf['Value'].sum()
    total_pnl = sdf['PnL'].sum()
    st.markdown(f"**Combined Value: ${total_val:,.0f} | Combined PnL: ${total_pnl:+,.0f}**")

    st.markdown("---")
    st.markdown("""
### About BERA
BERA (Biotech Event-driven Research & Alpha) is a quantitative biotech investment research system.

**Core Strategy**: AI-based clinical trial success prediction + fundamental filters for large-cap biotech ($2B+).

**Satellite Strategy**: Smart money signal tracking + clinical AI risk filter for small/mid-cap event-driven biotech.

Paper tracking started June 2026. Results updated in real-time via yfinance.
""")
    st.caption("BERA | hansol.kang@bera.ai")


# ═══ Page: Quality Score ═══
elif page == "🧬 Quality Score":
    st.title("Quality Score — Clinical AI Pipeline Scoring")
    st.caption("🔵 Core 계열 전략 · 중대형주(시총 $2B+) · 벤치마크 IBB")
    st.markdown("""
BERA's proprietary AI model predicts clinical trial success probability for every active trial
across 814 US-listed biotech companies. The **Quality Score** is the average predicted success
probability of a company's currently active clinical trials — a composite measure of pipeline
strength at any given point in time.

Why does this generate alpha? Clinical trial design documents are publicly available, but the
market has not yet priced in the systematic success/failure probabilities embedded in them.
This information asymmetry — new information in the EMH sense — is what BERA exploits.
""")

    # ── Backtest Performance ──
    st.markdown("---")
    st.markdown("### Backtest: Quality Score as Investment Signal")
    st.markdown("""
Strategy: Buy the top 20 stocks ranked by Quality Score (mean predicted success probability),
market cap $2B+, equal-weighted, quarterly rebalancing. Period: Jan 2019 — May 2026 (7.4 years).
""")

    bt_c1, bt_c2, bt_c3, bt_c4 = st.columns(4)
    bt_c1.metric("CAGR", "42.8%")
    bt_c2.metric("Sharpe Ratio", "1.23")
    bt_c3.metric("Max Drawdown", "-32.5%")
    bt_c4.metric("All Years Positive", "Yes")

    annual_data = pd.DataFrame({
        'Year': ['2019', '2020', '2021', '2022', '2023', '2024', '2025', '2026*'],
        'Quality Score Strategy': [40, 81, 14, 39, 52, 13, 88, 8],
        'IBB': [24.0, 26.7, 1.6, -13.5, 4.8, -4.0, 27.3, 0.6],
        'SPY': [31.1, 17.2, 30.5, -18.6, 26.7, 25.6, 18.0, 8.3],
    })

    fig_annual = go.Figure()
    fig_annual.add_trace(go.Bar(
        x=annual_data['Year'], y=annual_data['Quality Score Strategy'],
        name='Quality Score Strategy', marker_color='#1976D2',
    ))
    fig_annual.add_trace(go.Scatter(
        x=annual_data['Year'], y=annual_data['IBB'],
        name='IBB (Biotech ETF)', mode='lines+markers',
        line=dict(color='#FB8C00', width=2),
    ))
    fig_annual.add_trace(go.Scatter(
        x=annual_data['Year'], y=annual_data['SPY'],
        name='SPY (S&P 500)', mode='lines+markers',
        line=dict(color='#43A047', width=2),
    ))
    fig_annual.update_layout(
        title='Annual Returns: Quality Score Strategy vs Benchmarks (%)',
        yaxis_title='Return (%)',
        barmode='group', height=420,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    fig_annual.add_hline(y=0, line_dash="dot", line_color="gray")
    st.plotly_chart(fig_annual, use_container_width=True)

    st.markdown("""
Key observations:
- Positive returns in every year including 2022 (+39%) when IBB fell -13.5% and SPY fell -19%
- Outperformed IBB in all 8 years — the Quality Score consistently identifies pipeline strength
- 2026 YTD as of May (*partial year)
""")

    st.markdown("---")

    # Load data
    # Quality Score = per-ticker mean HINT/optionB clinical SUCCESS probability
    # over CURRENTLY ACTIVE US trials (re-wired 2026-06-25; was trial_survival
    # p_completed over all/completed trials). Same column schema.
    SCORES_PATH = os.path.join(DATA_DIR, 'quality_score_ticker.csv')
    UNIVERSE_PATH = os.path.join(DATA_DIR, 'universe.csv')

    try:
        scores_df = pd.read_csv(SCORES_PATH)
        scores_df = scores_df.rename(columns={
            'yahoo_ticker': 'Ticker',
            'mean_p_completed': 'Quality Score',
            'min_p_completed': 'Min Score',
            'n_trials': 'Active Trials',
            'n_risky': 'Risky Trials',
            'phase': 'Phase',
        })
    except Exception as e:
        st.error(f"Score data not available: {e}")
        st.stop()

    try:
        universe_df = pd.read_csv(UNIVERSE_PATH)
        universe_total = len(universe_df)
    except Exception:
        universe_total = 814

    # ── Family split by market-cap universe (Core $2B+ / Satellite $500M-$2B) ──
    # Each strategy family screens a different cap band: Core picks large-cap ($2B+),
    # Satellite picks small-cap ($500M-$2B, per satellite_backtest.py MIN/MAX_MCAP).
    fam = st.radio(
        "전략 계열 (시총 유니버스)",
        ["🔵 Core ($2B+)", "🟢 Satellite ($500M–$2B)", "전체"],
        horizontal=True, key="qs_family",
    )
    if 'market_cap' in scores_df.columns:
        if fam.startswith("🔵"):
            scores_df = scores_df[scores_df['market_cap'] >= 2e9]
        elif fam.startswith("🟢"):
            scores_df = scores_df[(scores_df['market_cap'] >= 5e8) & (scores_df['market_cap'] < 2e9)]

    scored_count = len(scores_df)
    unscored_count = universe_total - scored_count

    # ── Section 2: Top Ranking ──
    st.markdown("### Top-Ranked Tickers by Quality Score")

    c1, c2, c3 = st.columns(3)
    c1.metric("Target Universe", f"{universe_total}")
    c2.metric("Currently Scored", f"{scored_count}")
    c3.metric("Monitoring (no active trials)", f"{unscored_count}")

    top_n = st.slider("Show top N tickers", min_value=10, max_value=100, value=30, step=10)
    top_df = scores_df.nlargest(top_n, 'Quality Score')[
        ['Ticker', 'Quality Score', 'Phase', 'Active Trials', 'Risky Trials']
    ].reset_index(drop=True)
    top_df.index = top_df.index + 1  # 1-based ranking

    def color_score(val):
        if isinstance(val, (int, float)):
            if val >= 0.55:
                return 'color: #2ecc71; font-weight: bold'
            elif val < 0.35:
                return 'color: #e74c3c'
        return ''

    st.dataframe(
        top_df.style.format({'Quality Score': '{:.3f}'}).map(color_score, subset=['Quality Score']),
        width='stretch', height=min(top_n * 38 + 40, 800),
    )

    # ── Section 3: Phase Distribution ──
    st.markdown("---")
    st.markdown("### Quality Score Distribution by Phase")
    st.markdown("""
Earlier phases (P1) tend to have wider variance — fewer trials, less data, more uncertainty.
Later phases (P2/P3) converge toward the mean as more clinical evidence accumulates.
""")

    phase_order = ['P1', 'P2', 'P3']
    phase_df = scores_df[scores_df['Phase'].isin(phase_order)].copy()

    col_box, col_hist = st.columns(2)

    with col_box:
        fig_box = px.box(
            phase_df, x='Phase', y='Quality Score',
            color='Phase',
            category_orders={'Phase': phase_order},
            color_discrete_map={'P1': '#3498db', 'P2': '#f39c12', 'P3': '#2ecc71'},
            title='Distribution by Phase (Box Plot)',
        )
        fig_box.update_layout(showlegend=False, yaxis_title='Quality Score', height=400)
        st.plotly_chart(fig_box, use_container_width=True)

    with col_hist:
        fig_hist = px.histogram(
            phase_df, x='Quality Score', color='Phase',
            category_orders={'Phase': phase_order},
            color_discrete_map={'P1': '#3498db', 'P2': '#f39c12', 'P3': '#2ecc71'},
            barmode='overlay', nbins=30, opacity=0.7,
            title='Distribution by Phase (Histogram)',
        )
        fig_hist.update_layout(yaxis_title='Count', height=400)
        st.plotly_chart(fig_hist, use_container_width=True)

    # Phase stats table
    phase_stats = phase_df.groupby('Phase').agg(
        Count=('Quality Score', 'count'),
        Mean=('Quality Score', 'mean'),
        Median=('Quality Score', 'median'),
        Std=('Quality Score', 'std'),
        Min=('Quality Score', 'min'),
        Max=('Quality Score', 'max'),
    ).reindex(phase_order)
    st.dataframe(
        phase_stats.style.format({
            'Mean': '{:.3f}', 'Median': '{:.3f}', 'Std': '{:.3f}',
            'Min': '{:.3f}', 'Max': '{:.3f}',
        }),
        width='stretch',
    )

    # ── Section 4: Target Universe ──
    st.markdown("---")
    st.markdown("### Target Universe")
    st.markdown(f"""
BERA monitors **{universe_total} US-listed biotech companies**. Of these, **{scored_count}**
currently have active clinical trials and receive a Quality Score. The remaining **{unscored_count}**
are tracked but have no active trials at this time — they will be scored automatically when new
trials begin.
""")

    col_pie, col_bar = st.columns(2)

    with col_pie:
        phase_counts = phase_df['Phase'].value_counts().reindex(phase_order).fillna(0)
        fig_pie = px.pie(
            values=phase_counts.values,
            names=phase_counts.index,
            title=f'Scored Tickers by Phase ({scored_count} total)',
            color=phase_counts.index,
            color_discrete_map={'P1': '#3498db', 'P2': '#f39c12', 'P3': '#2ecc71'},
            hole=0.4,
        )
        st.plotly_chart(fig_pie, use_container_width=True)

    with col_bar:
        coverage = pd.DataFrame({
            'Status': ['Scored (active trials)', 'Monitoring (no active trials)'],
            'Count': [scored_count, unscored_count],
        })
        fig_cov = px.bar(
            coverage, x='Status', y='Count',
            color='Status',
            color_discrete_map={
                'Scored (active trials)': '#2ecc71',
                'Monitoring (no active trials)': '#95a5a6',
            },
            title=f'Universe Coverage ({universe_total} total)',
        )
        fig_cov.update_layout(showlegend=False, height=400)
        st.plotly_chart(fig_cov, use_container_width=True)

    # Trials distribution
    st.markdown("#### Trial Count Distribution")
    trial_bins = scores_df['Active Trials'].value_counts().sort_index()
    bin_edges = [0, 1, 3, 5, 10, 20, 50, 100, 9999]
    labels = ['1', '2-3', '4-5', '6-10', '11-20', '21-50', '51-100', '100+']
    scores_df['Trial Bin'] = pd.cut(scores_df['Active Trials'], bins=bin_edges, labels=labels, right=True)
    bin_counts = scores_df['Trial Bin'].value_counts().reindex(labels).fillna(0)
    fig_trials = go.Figure(go.Bar(
        x=labels, y=bin_counts.values,
        marker_color='#3498db',
    ))
    fig_trials.update_layout(
        title='Number of Active Trials per Company',
        xaxis_title='Number of Active Trials',
        yaxis_title='Number of Companies',
        height=350,
    )
    st.plotly_chart(fig_trials, use_container_width=True)

    st.markdown("---")
    st.caption("Quality Score = mean predicted success probability of currently active clinical trials per company. Updated quarterly.")


# ═══ Page: Satellite Signal Terminal (Institutional, URL-gated) ═══
elif page == TERMINAL_PAGE:
    # No password. The gate is the ?institutional URL itself: visitors without it
    # never see this page exists (sidebar section is hidden + stale sessions bounce
    # out above). Shared deliberately with relationship-stage contacts only.

    # Signal board is institutional data — served from Streamlit Secrets, NOT the
    # (public) repo. Local dev falls back to data/signals.json (gitignored).
    try:
        _sig_raw = st.secrets.get("signals_json")
    except Exception:
        _sig_raw = None
    if _sig_raw:
        SIG = json.loads(_sig_raw)
    else:
        with open(os.path.join(DATA_DIR, 'signals.json'), 'r', encoding='utf-8') as f:
            SIG = json.load(f)

    st.title("🛰️ Satellite Signal Terminal")
    st.caption("🟢 Satellite 계열 전략 · 소형주 포함 · 벤치마크 XBI")
    st.caption(
        f"기관 전용 · 시그널 기준일 {SIG['run_date']} · "
        f"보드 최종 업데이트 {SIG.get('generated_at', '—')} · 소형·중형 바이오텍 이벤트 드리븐"
    )
    st.markdown(
        "SEC/FINRA 공시 기반 바이오 헤지펀드·내부자·숏 시그널을 종합해 격주(2주마다) 갱신되는 "
        "Strong Buy 후보 보드입니다. 종목·시그널 유형·신선도는 공개하되, "
        "배점·임계치·펀드명 등 재현 정보는 비공개입니다."
    )

    tc = SIG.get('tier_counts', {})
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Strong Buy", tc.get('Strong Buy', 0))
    c2.metric("Buy", tc.get('Buy', 0))
    c3.metric("Watch", tc.get('Watch', 0))
    c4.metric("보드 발행", SIG.get('published_count', 0))

    st.markdown("---")
    st.markdown("### Live Signal Board — Strong Buy")
    st.caption("종합 conviction 최상위 등급. 신선도 순 정렬. (종합점수 비공개)")

    board = pd.DataFrame([{
        'Ticker': s['ticker'],
        '시그널 믹스': ' · '.join(s['tags']),
        '보유 펀드': s['smart_money'],
        '신선도': s['freshness'],
    } for s in SIG['signals']])
    st.dataframe(board, width='stretch', hide_index=True,
                 height=min(len(board) * 36 + 40, 760))

    st.markdown(
        "**시그널 범례** — ⭐ Cross(기관+내부자 동시, 가장 강한 시그널) · "
        "💰 바이오 헤지펀드 보유 · 🏦 기관 축적 · 👤 내부자 집중매수 · ⚡ 숏스퀴즈 셋업"
    )

    with st.expander("How it works — 시그널 소스 & 진입 타이밍 (개념)"):
        st.markdown("""
4종 공시 시그널(기관 지분, 내부자 매수, 공매도, 분기 기관 보유 변화)을 종합한 뒤,
시그널 성격에 맞춰 진입 시점을 달리합니다.

- 기관 지분: 바이오 헤지펀드가 포지션을 잡았지만 주가는 아직 반응하지 않은 종목에 진입.
- 공매도 · 내부자 매수: 빠르게 반영되는 시그널이라 공시 직후 진입.
- Cross: 기관 지분과 내부자 매수가 함께 잡히는, 가장 확신이 높은 케이스.
- 임상 AI (BERA): 스마트머니 시그널에 베라의 임상 성공확률 예측을 얹은 트랙("With BERA AI")과 순수 스마트머니 트랙("Without BERA AI")을 함께 운용해, 임상필터의 성과 기여를 라이브로 비교합니다.

퇴출은 손절 · 급락 · 보유기간 규율로 관리합니다. 세부 기준은 비공개입니다.
""")

    # ── Quality Score — Satellite universe ($500M-$2B) ──
    st.markdown("---")
    st.markdown("### 🧬 Quality Score — Satellite 유니버스 ($500M–$2B)")
    st.caption(
        "소형주 임상 성공확률 스코어. Satellite 계열이 스크리닝하는 시총 밴드 기준. "
        "(스마트머니 시그널 위에 얹는 임상 레이어) · Core($2B+) QS는 Quality Score 페이지 참조"
    )
    try:
        _qs = pd.read_csv(os.path.join(DATA_DIR, 'quality_score_ticker.csv'))
        _qs = _qs[(_qs['market_cap'] >= 5e8) & (_qs['market_cap'] < 2e9)].rename(columns={
            'yahoo_ticker': 'Ticker', 'mean_p_completed': 'Quality Score',
            'n_trials': 'Active Trials', 'phase': 'Phase'})
        _top = _qs.nlargest(15, 'Quality Score')[
            ['Ticker', 'Quality Score', 'Phase', 'Active Trials']].reset_index(drop=True)
        _top.index = _top.index + 1
        st.dataframe(
            _top.style.format({'Quality Score': '{:.3f}'}).map(
                lambda v: 'color:#2ecc71; font-weight:bold' if isinstance(v, (int, float)) and v >= 0.55
                else ('color:#e74c3c' if isinstance(v, (int, float)) and v < 0.35 else ''),
                subset=['Quality Score']),
            width='stretch', hide_index=False,
            height=min(len(_top) * 38 + 40, 640))
        st.caption(f"Satellite 유니버스 스코어링 종목 {len(_qs)}개 중 상위 15 · 임상 QS ≥ 0.55 강조")
    except Exception as e:
        st.info(f"QS 데이터를 불러오지 못했습니다: {e}")

    # ── Live paper portfolio (track record / proof) ──
    st.markdown("---")
    st.markdown("### 🧬 With BERA AI — Satellite v2 (스마트머니 + 임상 AI 필터)")
    st.caption(
        f"진입 {SAT['entry_date']} · 시드 ${SAT['seed_usd']:,} · "
        "바이오 헤지펀드 시그널 진입 + 베라 임상필터 통과 종목만 (임상 하위컷오프 적용)"
    )

    sat_tickers = [p['ticker'] for p in SAT['portfolio']]
    sat_prices = get_prices_batch(sat_tickers)
    prows = []
    for p in SAT['portfolio']:
        cur = sat_prices.get(p['ticker'], p['entry'])
        if cur <= 0:
            cur = p['entry']
        qty = int(SAT['seed_usd'] * p['weight_pct'] / 100 / p['entry'])
        cost = qty * p['entry']
        pnl = qty * cur - cost
        prows.append({
            'Ticker': p['ticker'], 'Weight': f"{p['weight_pct']}%",
            '임상필터': '✓ pass',
            'PnL%': pnl / cost * 100 if cost > 0 else 0,
        })
    pdf = pd.DataFrame(prows)
    st.dataframe(
        pdf.style.format({'PnL%': '{:+.1f}%'}).map(
            lambda v: 'color:#2ecc71' if isinstance(v, (int, float)) and v > 0 else
                      ('color:#e74c3c' if isinstance(v, (int, float)) and v < 0 else ''),
            subset=['PnL%']),
        width='stretch', hide_index=True)

    sat_tqe = tuple((p['ticker'], int(SAT['seed_usd'] * p['weight_pct'] / 100 / p['entry']), p['entry'])
                    for p in SAT['portfolio'])
    sat_cost = sum(q * e for _, q, e in sat_tqe)
    sat_val = sum(sat_prices.get(t, e) * q for t, q, e in sat_tqe)
    sat_pnl_pct = (sat_val / sat_cost - 1) * 100 if sat_cost > 0 else 0
    show_bench(sat_pnl_pct, SAT['entry_date'], SAT['bench'], "Satellite", portfolio=sat_tqe)

    # ── Second live paper portfolio: 2026-05-25 shared picks (5/26 open entry, SL-25% + redistribution) ──
    ST = PF.get('shared_tracker')
    if ST:
        st.markdown("---")
        st.markdown("### ⚪ Without BERA AI — 5/26 추천종목 (순수 스마트머니 · 임상필터 없음)")
        st.caption(
            f"진입 {ST['entry_date']} 시초가 · {len(ST['tickers'])}종목 동일가중 · "
            f"SL {int(ST['sl']*100)}% + 동일비중 재분배 · Scouter Strong Buy 원본(임상 미적용)"
        )
        pr, trows, xbi_ret, n, daily_series = compute_shared_tracker(
            tuple(ST['tickers']), ST['entry_date'], ST['sl'], ST['vol_mult'], ST['drop_th'], ST['hold'])
        if pr is not None:
            held = [r for r in trows if r['상태'] == '보유']
            exited = [r for r in trows if r['상태'] != '보유']
            c1, c2, c3 = st.columns(3)
            c1.metric("Portfolio", f"{pr:+.2f}%")
            c2.metric("보유 / 손절", f"{len(held)} / {len(exited)}")
            c3.metric("Entry", ST['entry_date'])

            # SL'd names: shown as a notice (like Core Live), removed from the table.
            if exited:
                parts = []
                for r in exited:
                    why, _, dt = r['상태'].partition('@')
                    parts.append(f"{r['Ticker']} {r['PnL%']:+.1f}% ({why} {dt})")
                st.warning("🛑 손절 (포트폴리오 수익률에 이미 반영됨): " + " · ".join(parts))

            if held:
                hdf = pd.DataFrame(held)[['Ticker', 'Entry', 'Current', 'PnL%']]
                st.dataframe(
                    hdf.style.format({'Entry': '${:.2f}', 'Current': '${:.2f}', 'PnL%': '{:+.1f}%'}).map(
                        lambda v: 'color:#2ecc71' if isinstance(v, (int, float)) and v > 0 else
                                  ('color:#e74c3c' if isinstance(v, (int, float)) and v < 0 else ''),
                        subset=['PnL%']),
                    width='stretch', hide_index=True)

            show_bench(pr, ST['entry_date'], ST['bench'], "추천종목", bera_daily_override=daily_series)
            st.caption("※ 표본 기간이 짧은 단기·소형주 변동성 구간입니다. 누적 기간이 길어질수록 벤치마크 비교 의미가 커집니다.")
        else:
            st.info("트래커 데이터를 불러오지 못했습니다.")

    # ── Third live paper portfolio: 2026-06-25 Strong Buy board (6/25 open entry, SL-30% + redistribution, 오래됨 제외) ──
    SB = PF.get('strong_buy_tracker')
    if SB:
        st.markdown("---")
        st.markdown("### ⚪ Without BERA AI — 6/25 Strong Buy 보드 (순수 스마트머니 · 임상필터 없음)")
        st.caption(
            f"진입 {SB['entry_date']} 시초가 · {len(SB['tickers'])}종목 동일가중 · "
            f"SL {int(SB['sl']*100)}% 종가기준 + 생존종목 동일비중 재분배 · Scouter Strong Buy 원본(임상 미적용) · {SB.get('exclude_note', '')}"
        )
        pr, trows, xbi_ret, n, daily_series = compute_shared_tracker(
            tuple(SB['tickers']), SB['entry_date'], SB['sl'], SB['vol_mult'], SB['drop_th'], SB['hold'])
        if pr is not None:
            held = [r for r in trows if r['상태'] == '보유']
            exited = [r for r in trows if r['상태'] != '보유']
            c1, c2, c3 = st.columns(3)
            c1.metric("Portfolio", f"{pr:+.2f}%")
            c2.metric("보유 / 손절", f"{len(held)} / {len(exited)}")
            c3.metric("Entry", SB['entry_date'])

            if exited:
                parts = []
                for r in exited:
                    why, _, dt = r['상태'].partition('@')
                    parts.append(f"{r['Ticker']} {r['PnL%']:+.1f}% ({why} {dt})")
                st.warning("🛑 손절 (포트폴리오 수익률에 이미 반영됨): " + " · ".join(parts))

            if held:
                hdf = pd.DataFrame(held)[['Ticker', 'Entry', 'Current', 'PnL%']]
                st.dataframe(
                    hdf.style.format({'Entry': '${:.2f}', 'Current': '${:.2f}', 'PnL%': '{:+.1f}%'}).map(
                        lambda v: 'color:#2ecc71' if isinstance(v, (int, float)) and v > 0 else
                                  ('color:#e74c3c' if isinstance(v, (int, float)) and v < 0 else ''),
                        subset=['PnL%']),
                    width='stretch', hide_index=True)

            show_bench(pr, SB['entry_date'], SB['bench'], "추천종목", bera_daily_override=daily_series)
            st.caption("※ 표본 기간이 짧은 단기·소형주 변동성 구간입니다. 누적 기간이 길어질수록 벤치마크 비교 의미가 커집니다.")
        else:
            st.info("트래커 데이터를 불러오지 못했습니다.")

    # ── With BERA AI: Config H (2026-07-16 re-discovery) ──
    CH = PF.get('config_h_0716')
    if CH:
        st.markdown("---")
        st.markdown("### 🧬 With BERA AI — Config H (7/16 재발굴)")
        st.caption(
            f"진입 {CH['entry_date']} 시가 · {len(CH['tickers'])}종목 동일가중 · "
            f"스마트머니 스코어 + 임상 AI 게이트(prob≥0.5, 3yr P2/3) · "
            f"SL {int(CH['sl']*100)}% + vol3x/일간{int(CH['drop_th']*100)}% exit + hold{CH['hold']}d · {CH['backtest_note']}"
        )
        pr, trows, xbi_ret, n, daily_series = compute_shared_tracker(
            tuple(CH['tickers']), CH['entry_date'], CH['sl'], CH['vol_mult'], CH['drop_th'], CH['hold'])
        if pr is not None:
            held = [r for r in trows if r['상태'] == '보유']
            exited = [r for r in trows if r['상태'] != '보유']
            c1, c2, c3 = st.columns(3)
            c1.metric("Portfolio", f"{pr:+.2f}%")
            c2.metric("보유 / 손절", f"{len(held)} / {len(exited)}")
            c3.metric("Entry", CH['entry_date'])
            if exited:
                parts = []
                for r in exited:
                    why, _, dt = r['상태'].partition('@')
                    parts.append(f"{r['Ticker']} {r['PnL%']:+.1f}% ({why} {dt})")
                st.warning("🛑 손절 (수익률에 반영됨): " + " · ".join(parts))
            if held:
                hdf = pd.DataFrame(held)[['Ticker', 'Entry', 'Current', 'PnL%']]
                st.dataframe(
                    hdf.style.format({'Entry': '${:.2f}', 'Current': '${:.2f}', 'PnL%': '{:+.1f}%'}).map(
                        lambda v: 'color:#2ecc71' if isinstance(v, (int, float)) and v > 0 else
                                  ('color:#e74c3c' if isinstance(v, (int, float)) and v < 0 else ''),
                        subset=['PnL%']),
                    width='stretch', hide_index=True)
            show_bench(pr, CH['entry_date'], CH['bench'], "Config H", bera_daily_override=daily_series)
            st.caption("※ 임상 AI 게이트 통과한 스마트머니 종목만 — 'Without BERA AI'(5/26·6/25) 트랙과 성과 비교용.")
        else:
            st.info("트래커 데이터를 불러오지 못했습니다.")

    st.markdown("---")
    st.caption("BERA Satellite Signal Terminal · 기관 전용 · 무단 배포 금지")
