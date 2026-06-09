"""BERA Dashboard — Public Web Version
======================================
4 portfolios: Core (Live) | Core A/B (Paper) | Satellite Config H | Core New
No strategy parameters exposed. No local DB dependency.
"""
import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta

st.set_page_config(page_title="BERA Trading", page_icon="🧬", layout="wide")

# Mobile-friendly CSS
st.markdown("""
<style>
    /* Reduce padding on mobile */
    @media (max-width: 768px) {
        .block-container { padding: 1rem 0.5rem; }
        [data-testid="stMetric"] { padding: 0.3rem; }
        [data-testid="stMetricLabel"] { font-size: 0.75rem; }
        [data-testid="stMetricValue"] { font-size: 1.1rem; }
        [data-testid="stMetricDelta"] { font-size: 0.7rem; }
        .stDataFrame { font-size: 0.7rem; }
        [data-testid="stSidebar"] { min-width: 180px; max-width: 180px; }
    }
    /* General compact style */
    [data-testid="stMetric"] {
        border-radius: 8px;
        padding: 0.5rem 0.8rem;
        border: 1px solid #e9ecef;
    }
    h1 { font-size: 1.5rem !important; }
    .stDataFrame { overflow-x: auto; }
</style>
""", unsafe_allow_html=True)

# ═══ Portfolio Data ═══

# 1. Core Live (12종목, 한투 모의투자)
# MLYS SL 6/8: $26.52->$22.50 (-15.2%), 86 shares, loss $345.72
# Exit proceeds $1,935 -> 12종목 균등재배분 ($161.25/stock at 6/8 prices)
# Qty/entry updated to reflect redistribution (blended entry)
LIVE_ENTRY_DATE = "2026-05-18"
LIVE_SEED_KRW = 50_000_000
LIVE_SEED_USD = 35000  # approx
LIVE_BENCH = {'XBI': 129.64, 'IBB': 165.25, 'SPY': 739.57, 'QQQ': 707.26}
LIVE_PORTFOLIO = [
    {'ticker': 'CYTK', 'qty': 59, 'entry': 74.902},
    {'ticker': 'NBIX', 'qty': 25, 'entry': 158.897},
    {'ticker': 'LQDA', 'qty': 66, 'entry': 56.752},
    {'ticker': 'UTHR', 'qty': 7, 'entry': 565.10},
    {'ticker': 'DYN',  'qty': 144, 'entry': 16.845},
    {'ticker': 'AMRX', 'qty': 175, 'entry': 11.988},
    {'ticker': 'RCUS', 'qty': 100, 'entry': 23.781},
    {'ticker': 'XENE', 'qty': 45, 'entry': 53.456},
    {'ticker': 'CLDX', 'qty': 80, 'entry': 30.175},
    {'ticker': 'JAZZ', 'qty': 9, 'entry': 229.117},
    {'ticker': 'TVTX', 'qty': 49, 'entry': 43.329},
    {'ticker': 'SYRE', 'qty': 28, 'entry': 71.139},
]
LIVE_SL_LOSS = 345.72  # MLYS realized loss

# 2. Core A/B (paper, 20 core + 10 defense each)
COREAB_ENTRY_DATE = "2026-05-28"
COREAB_SEED_USD = 50000
COREAB_MACRO = 0.44  # Core 44% / Defense 56%
COREAB_BENCH = {'XBI': 135.59, 'IBB': 171.68, 'SPY': 754.68, 'QQQ': 735.86}

# Core stocks (qty, entry) — A has SLNO/EXEL, B has UTHR/BIIB
# MLYS SL 6/3: $31.10->$25.06 (-19.4%), 37 shares, loss $223.48
# Exit proceeds $927.22 -> 19종목 균등 재배분 ($48.80/stock at 6/3 prices)
# Qty/entry updated to reflect redistribution (blended entry for affected stocks)
COREA_CORE = [
    ('AMRX', 77, 12.88), ('LQDA', 15, 62.01), ('LLY', 1, 1127.32),
    ('BBIO', 17, 67.32), ('SLNO', 21, 53.01), ('EXEL', 21, 52.66),
    ('ALNY', 3, 302.50), ('TVTX', 21, 47.24), ('ERAS', 95, 12.59),
    ('XENE', 21, 53.92), ('GPCR', 25, 39.94), ('GILD', 8, 135.25),
    ('VERA', 34, 34.19), ('CRSP', 17, 55.92), ('CYTK', 15, 76.80),
    ('RYTM', 12, 92.00), ('IMVT', 35, 33.27), ('ZLAB', 54, 18.33),
    ('CLDX', 37, 31.70),
]
COREB_CORE = [
    ('AMRX', 77, 12.88), ('LQDA', 15, 62.01), ('LLY', 1, 1127.32),
    ('BBIO', 17, 67.32), ('UTHR', 2, 568.91), ('BIIB', 6, 196.62),
    ('ALNY', 3, 302.50), ('TVTX', 21, 47.24), ('ERAS', 95, 12.59),
    ('XENE', 21, 53.92), ('GPCR', 25, 39.94), ('GILD', 8, 135.25),
    ('VERA', 34, 34.19), ('CRSP', 17, 55.92), ('CYTK', 15, 76.80),
    ('RYTM', 12, 92.00), ('IMVT', 35, 33.27), ('ZLAB', 54, 18.33),
    ('CLDX', 37, 31.70),
]
COREAB_SL_LOSS = 223.48  # MLYS realized loss
DEFENSE_BASKET = [
    ('ABBV', 12, 218.49), ('AMGN', 8, 335.34), ('LLY', 2, 1127.32),
    ('REGN', 4, 624.86), ('BMY', 49, 56.53), ('VRTX', 6, 444.79),
    ('MRK', 23, 120.39), ('JNJ', 12, 231.46), ('PFE', 106, 26.20),
    ('GILD', 20, 135.25),
]

# 3. Satellite Config H (paper, 5종목)
SAT_ENTRY_DATE = "2026-06-05"
SAT_SEED_USD = 10000
SAT_BENCH = {'XBI': 128.67, 'IBB': 168.49, 'SPY': 737.40, 'QQQ': 705.21}
SAT_PORTFOLIO = [
    {'ticker': 'ALMS', 'weight_pct': 20, 'entry': 19.06, 'prob': 0.864, 'smart_money': 'Deep Track + Foresite + Samsara'},
    {'ticker': 'AVTX', 'weight_pct': 20, 'entry': 13.09, 'prob': 0.596, 'smart_money': 'Point72 + T.Rowe + OrbiMed'},
    {'ticker': 'ANRO', 'weight_pct': 20, 'entry': 18.27, 'prob': 0.551, 'smart_money': 'Point72 + Sirenia + Vestal Point'},
    {'ticker': 'IMRX', 'weight_pct': 20, 'entry': 4.26, 'prob': 0.658, 'smart_money': 'Empery + insider cluster'},
    {'ticker': 'BIOA', 'weight_pct': 20, 'entry': 15.68, 'prob': 0.574, 'smart_money': 'Cormorant + Khosla'},
]

# 4. Core New (paper, 7종목)
CNEW_ENTRY_DATE = "2026-06-05"
CNEW_SEED_USD = 50000
CNEW_BENCH = {'XBI': 128.67, 'IBB': 168.49, 'SPY': 737.40, 'QQQ': 705.21}
CNEW_PORTFOLIO = [
    {'ticker': 'MLYS', 'weight_mult': 1.0, 'entry': 23.72, 'prob': 0.866},
    {'ticker': 'ALMS', 'weight_mult': 1.0, 'entry': 19.06, 'prob': 0.864},
    {'ticker': 'INDV', 'weight_mult': 1.0, 'entry': 37.63, 'prob': 0.808},
    {'ticker': 'LLY',  'weight_mult': 1.0, 'entry': 1132.53, 'prob': 0.562},
    {'ticker': 'GILD', 'weight_mult': 1.5, 'entry': 129.01, 'prob': 0.759},
    {'ticker': 'ABBV', 'weight_mult': 1.5, 'entry': 227.26, 'prob': 0.638},
    {'ticker': 'ARQT', 'weight_mult': 1.0, 'entry': 21.22, 'prob': 0.560},
]

BENCH_SYMS = ['XBI', 'IBB', 'SPY', 'QQQ']

# ═══ Helpers ═══

@st.cache_data(ttl=300)
def get_price(ticker):
    try:
        t = yf.Ticker(ticker)
        # Try info first
        try:
            info = t.info
            pre = info.get('preMarketPrice')
            post = info.get('postMarketPrice')
            reg = info.get('currentPrice') or info.get('regularMarketPrice')
            p = float(pre or post or reg or 0)
            if p > 0:
                return p
        except:
            pass
        # Try fast_info
        try:
            p = float(t.fast_info.get('lastPrice', 0))
            if p > 0:
                return p
        except:
            pass
        # Fallback: last close from history
        try:
            hist = t.history(period='5d')
            if not hist.empty:
                return float(hist['Close'].iloc[-1])
        except:
            pass
        return 0.0
    except:
        return 0.0

@st.cache_data(ttl=300)
def get_prices_batch(tickers):
    result = {}
    for tk in tickers:
        p = get_price(tk)
        result[tk] = p
    return result

@st.cache_data(ttl=600)
def get_bench_data(entry_date, entry_prices):
    result = {}
    for sym in BENCH_SYMS:
        ep = entry_prices.get(sym)
        if not ep: continue
        try:
            hist = yf.Ticker(sym).history(start=entry_date, interval='1d')
            if not hist.empty:
                cur = float(hist['Close'].iloc[-1])
                ret = (cur - ep) / ep * 100
                normed = (hist['Close'] / ep - 1) * 100
                result[sym] = {'current': cur, 'ret': ret, 'series': normed}
        except:
            pass
    return result


@st.cache_data(ttl=600)
def get_portfolio_daily(tickers_qty_entry, entry_date):
    """Calculate daily portfolio return series."""
    try:
        tickers = [t[0] for t in tickers_qty_entry]
        data = yf.download(tickers, start=entry_date, interval='1d', progress=False)
        if data.empty:
            return None
        close = data['Close'] if 'Close' in data.columns else data
        if isinstance(close, pd.Series):
            close = close.to_frame(name=tickers[0])

        daily_vals = []
        for date in close.index:
            port_val = 0
            port_cost = 0
            for tk, qty, ep in tickers_qty_entry:
                if tk in close.columns:
                    p = close.loc[date, tk]
                    if pd.notna(p) and p > 0:
                        port_val += qty * p
                        port_cost += qty * ep
            if port_cost > 0:
                daily_vals.append({'date': date, 'ret': (port_val / port_cost - 1) * 100})

        if not daily_vals:
            return None
        return pd.DataFrame(daily_vals).set_index('date')['ret']
    except:
        return None


def show_bench(total_pnl_pct, entry_date, bench_prices, label, portfolio_daily=None):
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
        fig.add_hline(y=0, line_dash="dot", line_color="gray")

        # BERA daily line (bold)
        if portfolio_daily is not None:
            fig.add_trace(go.Scatter(
                x=portfolio_daily.index, y=portfolio_daily.values,
                name=f"BERA {label} ({total_pnl_pct:+.1f}%)",
                line=dict(color='#1976D2', width=3.5),
                mode='lines+markers',
                marker=dict(size=4),
            ))
        else:
            fig.add_hline(y=total_pnl_pct, line_dash="solid", line_color="#1976D2",
                          annotation_text=f"BERA {total_pnl_pct:+.1f}%", annotation_position="bottom right")

        clr = {'XBI': '#E53935', 'IBB': '#FB8C00', 'SPY': '#43A047', 'QQQ': '#7B1FA2'}
        for sym in BENCH_SYMS:
            if sym in bench:
                s = bench[sym]['series']
                fig.add_trace(go.Scatter(x=s.index, y=s.values,
                    name=f"{sym} ({bench[sym]['ret']:+.1f}%)",
                    line=dict(color=clr.get(sym, 'gray'), width=2)))
        fig.update_layout(title=f'Cumulative Return since {entry_date}',
                          yaxis_title='Return (%)', height=380,
                          legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5, font=dict(size=11)),
                          margin=dict(t=40, b=80))
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
page = st.sidebar.radio("Portfolio", [
    "💰 Core (Live)",
    "🅰️ Core A/B (Paper)",
    "🎯 Satellite v2 (Paper)",
    "🏛️ Core v2 (Paper)",
    "📊 Summary",
])
if st.sidebar.button("Refresh"):
    st.cache_data.clear()
st.sidebar.markdown("---")
st.sidebar.markdown("[BERA 네프콘](https://contents.premium.naver.com/bera/biostock)")
st.sidebar.caption("Updated: 2026-06-09")


# ═══ Page: Core Live ═══
if page == "💰 Core (Live)":
    st.title("Core Portfolio -- Live Trading")
    st.markdown(f"Entry: 2026-05-18 10:30 AM ET | Seed: 50,000,000 KRW | 12 stocks")
    st.warning("SL: MLYS 6/8 $26.52->$22.50 (-15.2%) | Vol spike + offering | Excluded")
    st.markdown("---")

    tickers = [p['ticker'] for p in LIVE_PORTFOLIO]
    prices = get_prices_batch(tickers)

    rows = []
    for p in LIVE_PORTFOLIO:
        cur = prices.get(p['ticker'], p['entry'])
        if cur <= 0: cur = p['entry']
        cost = p['qty'] * p['entry']
        val = p['qty'] * cur
        pnl = val - cost
        rows.append({'Ticker': p['ticker'], 'Qty': p['qty'], 'Entry': p['entry'],
                      'Current': cur, 'Value': val, 'PnL': pnl,
                      'PnL%': pnl/cost*100 if cost > 0 else 0})

    df = pd.DataFrame(rows)
    tc = df['Value'].sum(); tp = df['PnL'].sum() - LIVE_SL_LOSS
    total_cost = (tc - df['PnL'].sum()) + LIVE_SL_LOSS
    tpp = tp / total_cost * 100 if total_cost > 0 else 0

    c1, c2, c3 = st.columns(3)
    c1.metric("Portfolio Value", f"${tc:,.0f}")
    c2.metric("Total PnL", f"${tp:+,.0f}", delta=f"{tpp:+.2f}%")
    c3.metric("Stocks", f"{len(LIVE_PORTFOLIO)}")

    st.dataframe(df.sort_values('Value', ascending=False).style.format({
        'Entry': '${:.2f}', 'Current': '${:.2f}', 'Value': '${:,.0f}',
        'PnL': '${:+,.0f}', 'PnL%': '{:+.1f}%'
    }).map(lambda v: 'color:#2ecc71' if isinstance(v,(int,float)) and v>0 else
                ('color:#e74c3c' if isinstance(v,(int,float)) and v<0 else ''),
                subset=['PnL','PnL%']),
        use_container_width=True, hide_index=True)

    show_charts(df)
    st.markdown("---")
    live_tqe = [(p['ticker'], p['qty'], p['entry']) for p in LIVE_PORTFOLIO]
    live_daily = get_portfolio_daily(live_tqe, LIVE_ENTRY_DATE)
    show_bench(tpp, LIVE_ENTRY_DATE, LIVE_BENCH, "Core Live", portfolio_daily=live_daily)


# ═══ Page: Core A/B ═══
elif page == "🅰️ Core A/B (Paper)":
    st.title("Core A/B -- Paper Trading")
    st.markdown(f"Entry: 2026-05-28 12:23 PM ET | Seed: $50,000 | Macro 44% Core / 56% Defense")
    st.markdown("---")

    all_tickers = list(set([t[0] for t in COREA_CORE + COREB_CORE + DEFENSE_BASKET]))
    prices = get_prices_batch(all_tickers)

    st.warning("SL: MLYS 6/3 $31.10->$25.06 (-19.4%) | Vol spike 6.9x + $150M offering | Loss: $223")

    last_tpp = 0
    for label, core_stocks in [("Core A", COREA_CORE), ("Core B", COREB_CORE)]:
        st.markdown(f"### {label}")

        # Core stocks
        st.markdown(f"**Core ({len(core_stocks)} stocks, {COREAB_MACRO*100:.0f}% allocation)**")
        rows = []
        for tk, qty, ep in core_stocks:
            cur = prices.get(tk, ep)
            if cur <= 0: cur = ep
            cost = qty * ep; val = qty * cur; pnl = val - cost
            rows.append({'Ticker': tk, 'Qty': qty, 'Entry': ep, 'Current': cur,
                          'Value': val, 'PnL': pnl, 'PnL%': pnl/cost*100 if cost>0 else 0})
        cdf = pd.DataFrame(rows)

        # Defense basket
        st.markdown(f"**Defense ({len(DEFENSE_BASKET)} stocks, {(1-COREAB_MACRO)*100:.0f}% allocation)**")
        drows = []
        for tk, qty, ep in DEFENSE_BASKET:
            cur = prices.get(tk, ep)
            if cur <= 0: cur = ep
            cost = qty * ep; val = qty * cur; pnl = val - cost
            drows.append({'Ticker': tk, 'Qty': qty, 'Entry': ep, 'Current': cur,
                           'Value': val, 'PnL': pnl, 'PnL%': pnl/cost*100 if cost>0 else 0})
        ddf = pd.DataFrame(drows)

        # Combined (including MLYS realized loss)
        combined = pd.concat([cdf, ddf], ignore_index=True)
        tc = combined['Value'].sum(); tp = combined['PnL'].sum() - COREAB_SL_LOSS
        total_cost = (tc - combined['PnL'].sum()) + COREAB_SL_LOSS
        tpp = tp / total_cost * 100 if total_cost > 0 else 0
        core_pnl = cdf['PnL'].sum() - COREAB_SL_LOSS
        def_pnl = ddf['PnL'].sum()

        c1, c2, c3, c4 = st.columns(4)
        c1.metric(f"{label} Total", f"${tc:,.0f}", delta=f"{tpp:+.2f}%")
        c2.metric("Core PnL", f"${core_pnl:+,.0f}")
        c3.metric("Defense PnL", f"${def_pnl:+,.0f}")
        c4.metric("Stocks", f"{len(core_stocks)} + {len(DEFENSE_BASKET)}")

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

        show_charts(combined)
        st.markdown("---")
        last_tpp = tpp

    # Use Core A for daily tracking
    corea_tqe = [(tk, qty, ep) for tk, qty, ep in COREA_CORE + DEFENSE_BASKET]
    corea_daily = get_portfolio_daily(corea_tqe, COREAB_ENTRY_DATE)
    show_bench(last_tpp, COREAB_ENTRY_DATE, COREAB_BENCH, "Core A/B", portfolio_daily=corea_daily)


# ═══ Page: Satellite Config H ═══
elif page == "🎯 Satellite v2 (Paper)":
    st.title("Satellite v2 -- Paper Tracking")
    st.markdown(f"Entry: 2026-06-05 4:00 PM ET | Seed: $10,000 | Smart money + clinical AI scoring")
    st.markdown("Backtest: CAGR 83.8%, Sharpe 1.98, MDD -45.4% (3.1yr)")
    st.markdown("---")

    tickers = [p['ticker'] for p in SAT_PORTFOLIO]
    prices = get_prices_batch(tickers)

    rows = []
    for p in SAT_PORTFOLIO:
        cur = prices.get(p['ticker'], p['entry'])
        if cur <= 0: cur = p['entry']
        alloc = SAT_SEED_USD * p['weight_pct'] / 100
        qty = int(alloc / p['entry'])
        cost = qty * p['entry']; val = qty * cur; pnl = val - cost
        rows.append({'Ticker': p['ticker'], 'Weight': f"{p['weight_pct']}%",
                      'Prob': p['prob'], 'Entry': p['entry'], 'Current': cur,
                      'Value': val, 'PnL': pnl, 'PnL%': pnl/cost*100 if cost>0 else 0,
                      'Smart Money': p['smart_money']})

    df = pd.DataFrame(rows)
    tc = df['Value'].sum(); tp = df['PnL'].sum()
    tpp = tp / (tc - tp) * 100 if (tc - tp) > 0 else 0
    cash = SAT_SEED_USD - (df['Entry'] * df.apply(lambda r: int(SAT_SEED_USD * int(r['Weight'].replace('%','')) / 100 / r['Entry']), axis=1)).sum()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Active", f"{len(SAT_PORTFOLIO)} / 10 slots")
    c2.metric("Invested", f"${tc:,.0f}")
    c3.metric("PnL", f"${tp:+,.0f}", delta=f"{tpp:+.2f}%")
    c4.metric("Slots Available", f"{10 - len(SAT_PORTFOLIO)}")

    st.dataframe(df.style.format({
        'Prob': '{:.3f}', 'Entry': '${:.2f}', 'Current': '${:.2f}',
        'Value': '${:,.0f}', 'PnL': '${:+,.0f}', 'PnL%': '{:+.1f}%'
    }).map(lambda v: 'color:#2ecc71' if isinstance(v,(int,float)) and v>0 else
                ('color:#e74c3c' if isinstance(v,(int,float)) and v<0 else ''),
                subset=['PnL','PnL%']),
        use_container_width=True, hide_index=True)

    show_charts(df)
    st.markdown("---")
    show_bench(tpp, SAT_ENTRY_DATE, SAT_BENCH, "Satellite")


# ═══ Page: Core New ═══
elif page == "🏛️ Core v2 (Paper)":
    st.title("Core v2 -- Paper Tracking")
    st.markdown(f"Entry: 2026-06-05 4:00 PM ET | Seed: $50,000 | Clinical AI + fundamental filters")
    st.markdown("Backtest: CAGR 42.8%, Sharpe 1.23, MDD -32.5% (7.4yr)")
    st.markdown("---")

    tickers = [p['ticker'] for p in CNEW_PORTFOLIO]
    prices = get_prices_batch(tickers)
    total_mult = sum(p['weight_mult'] for p in CNEW_PORTFOLIO)

    rows = []
    for p in CNEW_PORTFOLIO:
        cur = prices.get(p['ticker'], p['entry'])
        if cur <= 0: cur = p['entry']
        alloc = CNEW_SEED_USD * p['weight_mult'] / total_mult
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
    c1.metric("Active", f"{len(CNEW_PORTFOLIO)} / 20 slots")
    c2.metric("Invested", f"${tc:,.0f}")
    c3.metric("PnL", f"${tp:+,.0f}", delta=f"{tpp:+.2f}%")
    c4.metric("Slots Available", f"{20 - len(CNEW_PORTFOLIO)}")

    st.dataframe(df.style.format({
        'Prob': '{:.3f}', 'Entry': '${:.2f}', 'Current': '${:.2f}',
        'Value': '${:,.0f}', 'PnL': '${:+,.0f}', 'PnL%': '{:+.1f}%'
    }).map(lambda v: 'color:#2ecc71' if isinstance(v,(int,float)) and v>0 else
                ('color:#e74c3c' if isinstance(v,(int,float)) and v<0 else ''),
                subset=['PnL','PnL%']),
        use_container_width=True, hide_index=True)

    show_charts(df)
    st.markdown("---")
    show_bench(tpp, CNEW_ENTRY_DATE, CNEW_BENCH, "Core v2")


# ═══ Page: Summary ═══
elif page == "📊 Summary":
    st.title("Portfolio Summary")
    st.markdown("All BERA portfolios at a glance.")
    st.markdown("---")

    summaries = []

    # Core Live (MLYS SL loss included)
    tickers = [p['ticker'] for p in LIVE_PORTFOLIO]
    px_live = get_prices_batch(tickers)
    tc = 0; tv = 0
    for p in LIVE_PORTFOLIO:
        cur = px_live.get(p['ticker'], p['entry'])
        if cur <= 0: cur = p['entry']
        tc += p['qty'] * p['entry']; tv += p['qty'] * cur
    live_pnl = tv - tc - LIVE_SL_LOSS
    live_cost = tc + LIVE_SL_LOSS
    summaries.append({'Portfolio': 'Core (Live)', 'Entry': LIVE_ENTRY_DATE,
                       'Seed': f"${LIVE_SEED_USD:,}", 'Stocks': len(LIVE_PORTFOLIO),
                       'Value': tv, 'PnL': live_pnl, 'PnL%': live_pnl/live_cost*100 if live_cost>0 else 0,
                       'Days': (pd.Timestamp.now()-pd.Timestamp(LIVE_ENTRY_DATE)).days})

    # Core A (core + defense, MLYS SL loss included)
    all_ab = list(set([t[0] for t in COREA_CORE + DEFENSE_BASKET]))
    px_ab = get_prices_batch(all_ab)
    tc = 0; tv = 0
    for tk, qty, ep in COREA_CORE + DEFENSE_BASKET:
        cur = px_ab.get(tk, ep)
        if cur <= 0: cur = ep
        tc += qty * ep; tv += qty * cur
    ab_pnl = tv - tc - COREAB_SL_LOSS
    ab_cost = tc + COREAB_SL_LOSS
    summaries.append({'Portfolio': 'Core A (Paper)', 'Entry': COREAB_ENTRY_DATE,
                       'Seed': f"${COREAB_SEED_USD:,}", 'Stocks': len(COREA_CORE)+len(DEFENSE_BASKET),
                       'Value': tv, 'PnL': ab_pnl, 'PnL%': ab_pnl/ab_cost*100 if ab_cost>0 else 0,
                       'Days': (pd.Timestamp.now()-pd.Timestamp(COREAB_ENTRY_DATE)).days})

    # Satellite v2
    tickers = [p['ticker'] for p in SAT_PORTFOLIO]
    px_sat = get_prices_batch(tickers)
    tc = 0; tv = 0
    for p in SAT_PORTFOLIO:
        cur = px_sat.get(p['ticker'], p['entry'])
        if cur <= 0: cur = p['entry']
        q = int(SAT_SEED_USD * p['weight_pct'] / 100 / p['entry'])
        tc += q * p['entry']; tv += q * cur
    summaries.append({'Portfolio': 'Satellite v2 (Paper)', 'Entry': SAT_ENTRY_DATE,
                       'Seed': f"${SAT_SEED_USD:,}", 'Stocks': len(SAT_PORTFOLIO),
                       'Value': tv, 'PnL': tv-tc, 'PnL%': (tv-tc)/tc*100 if tc>0 else 0,
                       'Days': (pd.Timestamp.now()-pd.Timestamp(SAT_ENTRY_DATE)).days})

    # Core v2
    tickers = [p['ticker'] for p in CNEW_PORTFOLIO]
    px_cnew = get_prices_batch(tickers)
    tm = sum(p['weight_mult'] for p in CNEW_PORTFOLIO)
    tc = 0; tv = 0
    for p in CNEW_PORTFOLIO:
        cur = px_cnew.get(p['ticker'], p['entry'])
        if cur <= 0: cur = p['entry']
        q = max(1, int(CNEW_SEED_USD * p['weight_mult'] / tm / p['entry']))
        tc += q * p['entry']; tv += q * cur
    summaries.append({'Portfolio': 'Core v2 (Paper)', 'Entry': CNEW_ENTRY_DATE,
                       'Seed': f"${CNEW_SEED_USD:,}", 'Stocks': len(CNEW_PORTFOLIO),
                       'Value': tv, 'PnL': tv-tc, 'PnL%': (tv-tc)/tc*100 if tc>0 else 0,
                       'Days': (pd.Timestamp.now()-pd.Timestamp(CNEW_ENTRY_DATE)).days})

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

**Satellite Strategy**: SEC filing-based smart money tracking (13G, Form4, 13F, Short Interest) + clinical AI risk filter for small/mid-cap event-driven biotech.

Paper tracking started June 2026. Results updated in real-time via yfinance.
""")
    st.markdown("[BERA 네프콘 (Naver Premium Contents)](https://contents.premium.naver.com/bera/biostock)")
    st.caption("BERA | hansol.kang@bera.ai")
