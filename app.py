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

# ═══ Portfolio Data ═══

# 1. Core Live (13종목, 한투 모의투자)
LIVE_ENTRY_DATE = "2026-05-18"
LIVE_SEED_KRW = 50_000_000
LIVE_SEED_USD = 35000  # approx
LIVE_BENCH = {'XBI': 129.64, 'IBB': 165.25, 'SPY': 739.57, 'QQQ': 707.26}
LIVE_PORTFOLIO = [
    {'ticker': 'CYTK', 'qty': 57, 'entry': 75.148},
    {'ticker': 'NBIX', 'qty': 25, 'entry': 158.897},
    {'ticker': 'LQDA', 'qty': 64, 'entry': 56.529},
    {'ticker': 'UTHR', 'qty': 7, 'entry': 565.10},
    {'ticker': 'DYN',  'qty': 135, 'entry': 16.847},
    {'ticker': 'AMRX', 'qty': 164, 'entry': 11.872},
    {'ticker': 'RCUS', 'qty': 94, 'entry': 23.81},
    {'ticker': 'XENE', 'qty': 42, 'entry': 53.616},
    {'ticker': 'CLDX', 'qty': 75, 'entry': 30.283},
    {'ticker': 'JAZZ', 'qty': 9, 'entry': 229.117},
    {'ticker': 'TVTX', 'qty': 46, 'entry': 43.084},
    {'ticker': 'MLYS', 'qty': 86, 'entry': 26.52},
    {'ticker': 'SYRE', 'qty': 26, 'entry': 71.073},
]

# 2. Core A/B (paper, 20 core + 10 defense each)
COREAB_ENTRY_DATE = "2026-05-28"
COREAB_SEED_USD = 50000
COREAB_MACRO = 0.44  # Core 44% / Defense 56%
COREAB_BENCH = {'XBI': 135.59, 'IBB': 171.68, 'SPY': 754.68, 'QQQ': 735.86}

# Core stocks (qty, entry) — A has SLNO/EXEL, B has UTHR/BIIB
COREA_CORE = [
    ('AMRX', 74, 12.88), ('LQDA', 15, 62.01), ('LLY', 1, 1127.32),
    ('BBIO', 17, 67.32), ('SLNO', 21, 53.01), ('EXEL', 21, 52.66),
    ('ALNY', 3, 302.50), ('TVTX', 20, 47.34), ('ERAS', 92, 12.57),
    ('XENE', 21, 53.92), ('GPCR', 24, 40.04), ('GILD', 8, 135.25),
    ('VERA', 33, 34.28), ('CRSP', 17, 55.92), ('CYTK', 15, 76.80),
    ('RYTM', 12, 92.00), ('IMVT', 34, 33.33), ('ZLAB', 52, 18.42),
    ('CLDX', 36, 31.75), ('MLYS', 37, 31.10),
]
COREB_CORE = [
    ('AMRX', 74, 12.88), ('LQDA', 15, 62.01), ('LLY', 1, 1127.32),
    ('BBIO', 17, 67.32), ('UTHR', 2, 568.91), ('BIIB', 6, 196.62),
    ('ALNY', 3, 302.50), ('TVTX', 20, 47.34), ('ERAS', 92, 12.57),
    ('XENE', 21, 53.92), ('GPCR', 24, 40.04), ('GILD', 8, 135.25),
    ('VERA', 33, 34.28), ('CRSP', 17, 55.92), ('CYTK', 15, 76.80),
    ('RYTM', 12, 92.00), ('IMVT', 34, 33.33), ('ZLAB', 52, 18.42),
    ('CLDX', 36, 31.75), ('MLYS', 37, 31.10),
]
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
        info = t.info
        pre = info.get('preMarketPrice')
        post = info.get('postMarketPrice')
        reg = info.get('currentPrice') or info.get('regularMarketPrice')
        return float(pre or post or reg or t.fast_info.get('lastPrice', 0))
    except:
        return 0.0

@st.cache_data(ttl=300)
def get_prices_batch(tickers):
    return {tk: get_price(tk) for tk in tickers}

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


def show_bench(total_pnl_pct, entry_date, bench_prices, label):
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
page = st.sidebar.radio("Portfolio", [
    "💰 Core (Live)",
    "🅰️ Core A/B (Paper)",
    "🎯 Satellite v2 (Paper)",
    "🏛️ Core v2 (Paper)",
    "📊 Summary",
    "🧬 Quality Score",
])
if st.sidebar.button("Refresh"):
    st.cache_data.clear()
st.sidebar.markdown("---")
st.sidebar.caption("Updated: 2026-06-08")


# ═══ Page: Core Live ═══
if page == "💰 Core (Live)":
    st.title("Core Portfolio -- Live Trading")
    st.markdown(f"Entry: 2026-05-18 10:30 AM ET | Seed: 50,000,000 KRW | 13 stocks")
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
    tc = df['Value'].sum(); tp = df['PnL'].sum()
    tpp = tp / (tc - tp) * 100 if (tc - tp) > 0 else 0

    c1, c2, c3 = st.columns(3)
    c1.metric("Portfolio Value", f"${tc:,.0f}")
    c2.metric("Total PnL", f"${tp:+,.0f}", delta=f"{tpp:+.2f}%")
    c3.metric("Stocks", f"{len(LIVE_PORTFOLIO)}")

    st.dataframe(df.sort_values('Value', ascending=False).style.format({
        'Entry': '${:.2f}', 'Current': '${:.2f}', 'Value': '${:,.0f}',
        'PnL': '${:+,.0f}', 'PnL%': '{:+.1f}%'
    }).applymap(lambda v: 'color:#2ecc71' if isinstance(v,(int,float)) and v>0 else
                ('color:#e74c3c' if isinstance(v,(int,float)) and v<0 else ''),
                subset=['PnL','PnL%']),
        use_container_width=True, hide_index=True)

    show_charts(df)
    st.markdown("---")
    show_bench(tpp, LIVE_ENTRY_DATE, LIVE_BENCH, "Core Live")


# ═══ Page: Core A/B ═══
elif page == "🅰️ Core A/B (Paper)":
    st.title("Core A/B -- Paper Trading")
    st.markdown(f"Entry: 2026-05-28 12:23 PM ET | Seed: $50,000 | Macro 44% Core / 56% Defense")
    st.markdown("---")

    all_tickers = list(set([t[0] for t in COREA_CORE + COREB_CORE + DEFENSE_BASKET]))
    prices = get_prices_batch(all_tickers)

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

        # Combined
        combined = pd.concat([cdf, ddf], ignore_index=True)
        tc = combined['Value'].sum(); tp = combined['PnL'].sum()
        tpp = tp / (tc - tp) * 100 if (tc - tp) > 0 else 0
        core_pnl = cdf['PnL'].sum()
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
            st.dataframe(cdf.sort_values('PnL%', ascending=False).style.format(fmt).applymap(style_fn, subset=['PnL','PnL%']),
                use_container_width=True, hide_index=True)
        with tab2:
            st.dataframe(ddf.sort_values('PnL%', ascending=False).style.format(fmt).applymap(style_fn, subset=['PnL','PnL%']),
                use_container_width=True, hide_index=True)

        show_charts(combined)
        st.markdown("---")
        last_tpp = tpp

    show_bench(last_tpp, COREAB_ENTRY_DATE, COREAB_BENCH, "Core A/B")


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
    }).applymap(lambda v: 'color:#2ecc71' if isinstance(v,(int,float)) and v>0 else
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
    }).applymap(lambda v: 'color:#2ecc71' if isinstance(v,(int,float)) and v>0 else
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

    # Core Live
    tickers = [p['ticker'] for p in LIVE_PORTFOLIO]
    px_live = get_prices_batch(tickers)
    tc = 0; tv = 0
    for p in LIVE_PORTFOLIO:
        cur = px_live.get(p['ticker'], p['entry'])
        if cur <= 0: cur = p['entry']
        tc += p['qty'] * p['entry']; tv += p['qty'] * cur
    summaries.append({'Portfolio': 'Core (Live)', 'Entry': LIVE_ENTRY_DATE,
                       'Seed': f"${LIVE_SEED_USD:,}", 'Stocks': len(LIVE_PORTFOLIO),
                       'Value': tv, 'PnL': tv-tc, 'PnL%': (tv-tc)/tc*100 if tc>0 else 0,
                       'Days': (pd.Timestamp.now()-pd.Timestamp(LIVE_ENTRY_DATE)).days})

    # Core A (core + defense)
    all_ab = list(set([t[0] for t in COREA_CORE + DEFENSE_BASKET]))
    px_ab = get_prices_batch(all_ab)
    tc = 0; tv = 0
    for tk, qty, ep in COREA_CORE + DEFENSE_BASKET:
        cur = px_ab.get(tk, ep)
        if cur <= 0: cur = ep
        tc += qty * ep; tv += qty * cur
    summaries.append({'Portfolio': 'Core A (Paper)', 'Entry': COREAB_ENTRY_DATE,
                       'Seed': f"${COREAB_SEED_USD:,}", 'Stocks': len(COREA_CORE)+len(DEFENSE_BASKET),
                       'Value': tv, 'PnL': tv-tc, 'PnL%': (tv-tc)/tc*100 if tc>0 else 0,
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
    }).applymap(lambda v: 'color:#2ecc71' if isinstance(v,(int,float)) and v>0 else
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
    st.markdown("---")

    # Load data
    import os
    SCORES_PATH = os.path.join(os.path.dirname(__file__), 'data', 'trial_survival_ticker_scores.csv')
    UNIVERSE_PATH = os.path.join(os.path.dirname(__file__), 'data', 'universe.csv')

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
        top_df.style.format({'Quality Score': '{:.3f}'}).applymap(color_score, subset=['Quality Score']),
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
    fig_trials = px.histogram(
        scores_df, x='Active Trials', nbins=50,
        title='Number of Active Trials per Company',
        color_discrete_sequence=['#3498db'],
    )
    fig_trials.update_layout(
        xaxis_title='Number of Active Trials',
        yaxis_title='Number of Companies',
        height=350,
    )
    st.plotly_chart(fig_trials, use_container_width=True)

    st.markdown("---")
    st.caption("Quality Score = mean predicted success probability of currently active clinical trials per company. Updated quarterly.")
