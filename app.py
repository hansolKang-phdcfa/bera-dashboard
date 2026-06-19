"""BERA Dashboard — Public Web Version
======================================
4 portfolios: Core (Live) | Core A/B (Paper) | Satellite Config H | Core New
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
with open(os.path.join(DATA_DIR, 'portfolios.json'), 'r') as f:
    PF = json.load(f)

LIVE = PF['core_live']
CAB = PF['core_ab']
SAT = PF['satellite']
CNEW = PF['core_new']

BENCH_SYMS = ['XBI', 'IBB', 'SPY', 'QQQ']

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
    result = {}
    for sym in BENCH_SYMS:
        ep = entry_prices.get(sym)
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

        # Denominator: original cost (old portfolio if SL, else current)
        if old_pf:
            total_cost = sum(qty * ep for _, qty, ep in old_pf)
        else:
            total_cost = sum(qty * ep for _, qty, ep in tickers_qty_entry)
        if total_cost <= 0:
            return None

        daily_vals = []
        for date in close.index:
            pf = old_pf if (old_pf and sl_date and date < sl_date) else tickers_qty_entry

            port_val = 0
            for tk, qty, ep in pf:
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


def show_bench(total_pnl_pct, entry_date, bench_prices, label, portfolio=None, sl_events=None):
    st.markdown(f"### vs Benchmarks (since {entry_date})")
    bench = get_bench_data(entry_date, bench_prices)
    cols = st.columns(5)
    cols[0].metric(f"BERA {label}", f"{total_pnl_pct:+.2f}%")
    for i, sym in enumerate(BENCH_SYMS):
        if sym in bench:
            r = bench[sym]['ret']
            cols[i+1].metric(sym, f"{r:+.2f}%", delta=f"{total_pnl_pct - r:+.2f}%p")
    if bench:
        fig = go.Figure()
        # BERA as line chart (same as benchmarks)
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
        clr = {'XBI': '#E53935', 'IBB': '#FB8C00', 'SPY': '#43A047', 'QQQ': '#7B1FA2'}
        for sym in BENCH_SYMS:
            if sym in bench:
                s = bench[sym]['series']
                fig.add_trace(go.Scatter(x=s.index, y=s.values,
                    name=f"{sym} ({bench[sym]['ret']:+.1f}%)",
                    line=dict(color=clr.get(sym, 'gray'), width=2)))
        fig.update_layout(title=f'Cumulative Return since {entry_date}',
                          yaxis_title='Return (%)', height=380,
                          hovermode='x unified',
                          legend=dict(orientation="h", yanchor="bottom", y=1.02))
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


# ═══ Sidebar ═══
st.sidebar.title("BERA")
st.sidebar.caption("Biotech Event-driven Research & Alpha")
st.sidebar.markdown("---")

OVERVIEW_PAGES = ["🧬 Quality Score", "📊 Summary"]
PORTFOLIO_PAGES = [
    "💰 Core (Live)",
    "🅰️ Core A/B (Paper)",
    "🎯 Satellite v2 (Paper)",
    "🏛️ Core v2 (Paper)",
]

if 'page' not in st.session_state:
    st.session_state.page = "🧬 Quality Score"

def _nav(p):
    st.session_state.page = p

def _nav_button(label):
    is_current = st.session_state.page == label
    st.sidebar.button(
        label, use_container_width=True,
        type="primary" if is_current else "secondary",
        on_click=_nav, args=(label,), key=f"nav_{label}",
    )

st.sidebar.markdown("**Overview**")
for label in OVERVIEW_PAGES:
    _nav_button(label)

st.sidebar.markdown("**Portfolios**")
for label in PORTFOLIO_PAGES:
    _nav_button(label)

page = st.session_state.page

st.sidebar.markdown("---")
if st.sidebar.button("Refresh"):
    st.cache_data.clear()
st.sidebar.caption("Updated: 2026-06-15")


# ═══ Page: Core Live ═══
if page == "💰 Core (Live)":
    st.title("Core Portfolio -- Live Trading")
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
        use_container_width=True, hide_index=True)

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


# ═══ Page: Core A/B ═══
elif page == "🅰️ Core A/B (Paper)":
    st.title("Core A/B -- Paper Trading")
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
                use_container_width=True, hide_index=True)
        with tab2:
            st.dataframe(ddf.sort_values('PnL%', ascending=False).style.format(fmt).map(style_fn, subset=['PnL','PnL%']),
                use_container_width=True, hide_index=True)

        # Aggregate duplicate tickers (GILD, LLY in both Core & Defense) for charts
        chart_df = combined.groupby('Ticker', as_index=False).agg({
            'Value': 'sum', 'PnL': 'sum', 'Qty': 'sum',
            'Entry': 'first', 'Current': 'first',
        })
        chart_df['PnL%'] = chart_df.apply(
            lambda r: r['PnL'] / (r['Value'] - r['PnL']) * 100 if (r['Value'] - r['PnL']) > 0 else 0, axis=1)
        show_charts(chart_df)
        st.markdown("---")
        last_tpp = tpp
        core_tqe = [(s['ticker'], s['qty'], s['entry']) for s in core_stocks_raw]
        def_tqe = [(s['ticker'], s['qty'], s['entry']) for s in defense_raw]
        last_tqe = tuple(core_tqe + def_tqe)

    # SL-aware daily chart for Core A/B (use Core A + Defense, old portfolio before SL)
    cab_sl_ev = None
    if CAB.get('sl_event'):
        ev = CAB['sl_event']
        old_core = [(s['ticker'], s['qty'], s['entry']) for s in ev['old_core_a']]
        old_def = [(s['ticker'], s['qty'], s['entry']) for s in CAB['defense']]
        cab_sl_ev = [{'date': ev['date'], 'old_portfolio': old_core + old_def}]
    show_bench(last_tpp, CAB['entry_date'], CAB['bench'], "Core A/B",
               portfolio=last_tqe, sl_events=cab_sl_ev)


# ═══ Page: Satellite Config H ═══
elif page == "🎯 Satellite v2 (Paper)":
    st.title("Satellite v2 -- Paper Tracking")
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
        use_container_width=True, hide_index=True)

    show_charts(df)
    st.markdown("---")
    sat_tqe = tuple((p['ticker'], int(SAT['seed_usd'] * p['weight_pct'] / 100 / p['entry']), p['entry']) for p in SAT['portfolio'])
    show_bench(tpp, SAT['entry_date'], SAT['bench'], "Satellite", portfolio=sat_tqe)


# ═══ Page: Core New ═══
elif page == "🏛️ Core v2 (Paper)":
    st.title("Core v2 -- Paper Tracking")
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
        use_container_width=True, hide_index=True)

    show_charts(df)
    st.markdown("---")
    cnew_tqe = tuple((p['ticker'], max(1, int(CNEW['seed_usd'] * p['weight_mult'] / total_mult / p['entry'])), p['entry']) for p in CNEW['portfolio'])
    show_bench(tpp, CNEW['entry_date'], CNEW['bench'], "Core v2", portfolio=cnew_tqe)


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
    summaries.append({'Portfolio': 'Core (Live)', 'Entry': LIVE['entry_date'],
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
    summaries.append({'Portfolio': 'Core A (Paper)', 'Entry': CAB['entry_date'],
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
    summaries.append({'Portfolio': 'Satellite v2 (Paper)', 'Entry': SAT['entry_date'],
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
    summaries.append({'Portfolio': 'Core v2 (Paper)', 'Entry': CNEW['entry_date'],
                       'Seed': f"${CNEW['seed_usd']:,}", 'Stocks': len(CNEW['portfolio']),
                       'Value': tv, 'PnL': tv-tc, 'PnL%': (tv-tc)/tc*100 if tc>0 else 0,
                       'Days': (pd.Timestamp.now()-pd.Timestamp(CNEW['entry_date'])).days})

    sdf = pd.DataFrame(summaries)
    st.dataframe(sdf.style.format({
        'Value': '${:,.0f}', 'PnL': '${:+,.0f}', 'PnL%': '{:+.2f}%',
    }).map(lambda v: 'color:#2ecc71' if isinstance(v,(int,float)) and v>0 else
                ('color:#e74c3c' if isinstance(v,(int,float)) and v<0 else ''),
                subset=['PnL','PnL%']),
        use_container_width=True, hide_index=True)

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
        'XBI': [30.5, 49.0, -20.5, -28.1, 9.5, -0.0, 33.7, 10.9],
        'SPY': [31.1, 17.2, 30.5, -18.6, 26.7, 25.6, 18.0, 8.3],
    })

    fig_annual = go.Figure()
    fig_annual.add_trace(go.Bar(
        x=annual_data['Year'], y=annual_data['Quality Score Strategy'],
        name='Quality Score Strategy', marker_color='#1976D2',
    ))
    fig_annual.add_trace(go.Scatter(
        x=annual_data['Year'], y=annual_data['XBI'],
        name='XBI (Biotech ETF)', mode='lines+markers',
        line=dict(color='#E53935', width=2),
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
- Positive returns in every year including 2022 (+39%) when XBI fell -28% and SPY fell -19%
- Outperformed XBI in 7 of 8 years — the Quality Score consistently identifies pipeline strength
- 2026 YTD as of May (*partial year)
""")

    st.markdown("---")

    # Load data
    SCORES_PATH = os.path.join(DATA_DIR, 'trial_survival_ticker_scores.csv')
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
        use_container_width=True, height=min(top_n * 38 + 40, 800),
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
        use_container_width=True,
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
