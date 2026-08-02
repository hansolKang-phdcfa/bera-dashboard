"""BERA Dashboard — Public Web Version
======================================
Core tracks: Core (5/18, v1) | Core (5/28, v2) | Core (6/5, v3) | Core (6/30, v4) | Core (7/17, v5) | Satellite v2
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


def render_tracker_track(cfg, title, sub_caption, bench_label):
    """Render a Satellite ticker-list track via compute_shared_tracker (SL/vol/hold exit)."""
    st.markdown(f"### {title}")
    st.caption(sub_caption)
    pr, trows, xbi_ret, n, daily_series = compute_shared_tracker(
        tuple(cfg['tickers']), cfg['entry_date'], cfg['sl'], cfg['vol_mult'], cfg['drop_th'], cfg['hold'])
    if pr is None:
        st.info("트래커 데이터를 불러오지 못했습니다.")
        return
    held = [r for r in trows if r['상태'] == '보유']
    exited = [r for r in trows if r['상태'] != '보유']
    c1, c2, c3 = st.columns(3)
    c1.metric("Portfolio", f"{pr:+.2f}%")
    c2.metric("보유 / 손절", f"{len(held)} / {len(exited)}")
    c3.metric("Entry", cfg['entry_date'])
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
    show_bench(pr, cfg['entry_date'], cfg['bench'], bench_label, bera_daily_override=daily_series)


@st.cache_data(ttl=300)
def compute_satellite_sl(names, entry_date, sl):
    """Daily close-based SL replay for the weighted Satellite v2 portfolio.

    names: tuple of (ticker, entry_price, weight_pct). A position is stopped the
    first day its close is <= entry*(1+sl); the loss is realized at that close and
    the freed capital is redistributed to survivors in proportion to their current
    value (relative weights preserved). This applies the same SL discipline the
    Config H tracker tracks use — Satellite v2 was previously pure buy&hold.
    Returns (rows_by_ticker, port_ret%, daily_series). Falls back to (None,..) on
    missing history so the caller can degrade to live buy&hold.
    """
    tickers = [n[0] for n in names]
    entry = {n[0]: n[1] for n in names}
    w0 = {n[0]: n[2] for n in names}
    C = {}
    for tk in tickers:
        try:
            h = yf.Ticker(tk).history(start='2026-04-20', interval='1d', auto_adjust=False)
            c = h['Close'].dropna()
            if not c.empty:
                C[tk] = c
        except Exception:
            pass
    cl = pd.DataFrame(C).sort_index().ffill()
    if cl.empty:
        return None, None, None
    tks = [t for t in tickers if t in cl.columns]
    days = [d for d in cl.index if str(d.date()) >= entry_date]
    if not days or not tks:
        return None, None, None
    tot_w = sum(w0[tk] for tk in tks)
    val = {tk: w0[tk] / tot_w for tk in tks}   # fraction of deployed capital
    alive = set(tks); cash = 0.0
    status = {tk: '보유' for tk in tks}; exret = {}; expx = {}
    daily = {}
    for i, d in enumerate(days):
        loc_d = cl.index.get_loc(d)
        pv = cl.index[loc_d - 1]
        for tk in list(alive):
            base = entry[tk] if i == 0 else cl.loc[pv, tk]
            m = (cl.loc[d, tk] / base) if base > 0 else 1.0
            if np.isfinite(m):
                val[tk] *= m
        ex = [tk for tk in list(alive)
              if entry[tk] > 0 and (cl.loc[d, tk] - entry[tk]) / entry[tk] <= sl]
        if ex:
            freed = sum(val[tk] for tk in ex)
            for tk in ex:
                alive.discard(tk); val[tk] = 0.0
                status[tk] = f"SL@{str(d.date())[5:]}"
                exret[tk] = (cl.loc[d, tk] - entry[tk]) / entry[tk] * 100
                expx[tk] = float(cl.loc[d, tk])
            surv_tot = sum(val[tk] for tk in alive)
            if alive and surv_tot > 0:
                for tk in alive:
                    val[tk] += freed * val[tk] / surv_tot
            else:
                cash += freed
        daily[d] = (sum(val.values()) + cash - 1) * 100
    port_ret = (sum(val.values()) + cash - 1) * 100
    last = cl.index[-1]
    rows = {}
    for tk in tks:
        if status[tk] != '보유':
            rows[tk] = {'current': expx[tk], 'ret': exret[tk], 'status': status[tk]}
        else:
            cur = float(cl.loc[last, tk])
            rows[tk] = {'current': cur, 'ret': (cur - entry[tk]) / entry[tk] * 100,
                        'status': '보유'}
    return rows, port_ret, pd.Series(daily).sort_index()


@st.cache_data(ttl=300)
def compute_core_monthend_sl(names, entry_date, sl):
    """Month-end −15%-from-entry SL replay for a Core cohort (buy&hold between checks).

    names: tuple of (ticker, entry_price, weight_mult). At each COMPLETED month's
    last trading day, any holding whose close is <= entry*(1+sl) is stopped and its
    capital moved to cash (no redistribution — Core rebalances quarterly, not
    intra-quarter). Mirrors 26_core_v3_sl_overlay.py: ret measured from entry, checked
    only at month-end (is_me). Returns (rows_by_ticker, port_ret%, daily_series) or
    (None,..) if history is unavailable. Transaction cost is omitted (display only).
    """
    tickers = [n[0] for n in names]
    entry = {n[0]: n[1] for n in names}
    wm = {n[0]: n[2] for n in names}
    C = {}
    for tk in tickers:
        try:
            h = yf.Ticker(tk).history(start='2026-06-01', interval='1d', auto_adjust=False)
            c = h['Close'].dropna()
            if not c.empty:
                C[tk] = c
        except Exception:
            pass
    cl = pd.DataFrame(C).sort_index().ffill()
    if cl.empty:
        return None, None, None
    tks = [t for t in tickers if t in cl.columns]
    days = [d for d in cl.index if str(d.date()) >= entry_date]
    if not days or not tks:
        return None, None, None
    # Check dates = last trade day of each COMPLETED month (exclude the ongoing month).
    now = pd.Timestamp.now()
    seen = {}
    for d in days:
        seen[(d.year, d.month)] = d
    me_days = {d for (y, m), d in seen.items() if (now.year, now.month) > (y, m)}
    tot = sum(wm[t] for t in tks)
    val = {t: wm[t] / tot for t in tks}
    alive = set(tks); cash = 0.0
    status = {t: '보유' for t in tks}; exret = {}; expx = {}
    daily = {}
    for i, d in enumerate(days):
        loc = cl.index.get_loc(d); pv = cl.index[loc - 1]
        for t in list(alive):
            base = entry[t] if i == 0 else cl.loc[pv, t]
            m = (cl.loc[d, t] / base) if base > 0 else 1.0
            if np.isfinite(m):
                val[t] *= m
        if d in me_days:
            for t in list(alive):
                if entry[t] > 0 and (cl.loc[d, t] - entry[t]) / entry[t] <= sl:
                    cash += val[t]; val[t] = 0.0; alive.discard(t)
                    status[t] = f"SL@{str(d.date())[5:]}"
                    exret[t] = (cl.loc[d, t] - entry[t]) / entry[t] * 100
                    expx[t] = float(cl.loc[d, t])
        daily[d] = (sum(val.values()) + cash - 1) * 100
    port_ret = (sum(val.values()) + cash - 1) * 100
    last = cl.index[-1]
    rows = {}
    for t in tks:
        if status[t] != '보유':
            rows[t] = {'current': expx[t], 'ret': exret[t], 'status': status[t]}
        else:
            cur = float(cl.loc[last, t])
            rows[t] = {'current': cur, 'ret': (cur - entry[t]) / entry[t] * 100,
                       'status': '보유'}
    return rows, port_ret, pd.Series(daily).sort_index()


def render_satellite_v2(SAT):
    """Render the Satellite v2 (6/5) weighted portfolio with SL-30 discipline
    (weight_pct + prob + smart money). Falls back to live buy&hold if the daily
    history needed for the SL replay is unavailable."""
    st.markdown(SAT['entry_note'])
    st.markdown(SAT['backtest_note'])
    sl = SAT.get('sl', -0.30)
    meta = {p['ticker']: p for p in SAT['portfolio']}
    names = tuple((p['ticker'], p['entry'], p['weight_pct']) for p in SAT['portfolio'])
    rows, port_ret, daily_series = compute_satellite_sl(names, SAT['entry_date'], sl)

    if rows is None:  # history unavailable → degrade to live buy&hold
        tickers = list(meta)
        prices = get_prices_batch(tickers)
        rows = {}
        for tk, p in meta.items():
            cur = prices.get(tk, p['entry'])
            if cur <= 0: cur = p['entry']
            rows[tk] = {'current': cur, 'ret': (cur - p['entry']) / p['entry'] * 100,
                        'status': '보유'}
        w = {tk: meta[tk]['weight_pct'] for tk in rows}
        tw = sum(w.values())
        port_ret = sum(rows[tk]['ret'] * w[tk] / tw for tk in rows) if tw else 0.0
        daily_series = None
        st.caption("⚠️ 일별 히스토리 로드 실패 — SL 미적용 buy&hold로 임시 표시")

    held = [tk for tk in rows if rows[tk]['status'] == '보유']
    stopped = [tk for tk in rows if rows[tk]['status'] != '보유']
    seed = SAT['seed_usd']
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Active", f"{len(held)} / {SAT['max_slots']} slots")
    c2.metric("Invested", f"${seed:,.0f}")
    c3.metric("PnL", f"${seed * port_ret / 100:+,.0f}", delta=f"{port_ret:+.2f}%")
    c4.metric("손절 / 보유", f"{len(stopped)} / {len(held)}")
    if stopped:
        parts = [f"{tk} {rows[tk]['ret']:+.1f}% ({rows[tk]['status']})" for tk in stopped]
        st.warning(f"🛑 손절 SL{int(sl*100)}% (수익률에 반영·재분배됨): " + " · ".join(parts))
    if held:
        df = pd.DataFrame([{
            'Ticker': tk, 'Weight': f"{meta[tk]['weight_pct']}%", 'Prob': meta[tk]['prob'],
            'Entry': meta[tk]['entry'], 'Current': rows[tk]['current'], 'PnL%': rows[tk]['ret'],
            'Smart Money': meta[tk]['smart_money']} for tk in held])
        st.dataframe(df.style.format({
            'Prob': '{:.3f}', 'Entry': '${:.2f}', 'Current': '${:.2f}', 'PnL%': '{:+.1f}%'
        }).map(lambda v: 'color:#2ecc71' if isinstance(v,(int,float)) and v>0 else
                    ('color:#e74c3c' if isinstance(v,(int,float)) and v<0 else ''),
                    subset=['PnL%']),
            width='stretch', hide_index=True)
    show_bench(port_ret, SAT['entry_date'], SAT['bench'], "Satellite v2",
               bera_daily_override=daily_series)


# ═══ Satellite-family discovery tracks ═══
# Each discovery date is its own sidebar page (mirrors the Core date-page layout),
# sorted by entry_date ASCENDING — same convention as the Core pages. The freshest
# discovery sits at the bottom of the Satellite group, just above the Core group.
#   세대 숫자 = 전략 세대 (라이브 트랙이 어느 세대 룰로 발굴됐는지 표시)
#   🔴1️⃣ = Market-only     (순수 스마트머니, 임상 미적용) — 그 전 세대
#   🟡2️⃣ = Config H · v2   (스마트머니 + 임상 성공확률 게이트 + SL-30) — Config H 세대
#   🟢3️⃣ = 신규룰 v3       ($2B 상한 + R&D≥$50M + T-bill 현금) — 2026-08-02 확정
_SL_SUB = "진입 {entry} 시가 · {n}종목 동일가중 · SL-30/vol3x/drop-7%/hold120"
_MKT_SUB = "진입 {entry} 시초가 · {n}종목 동일가중 · SL {sl}% + 재분배 · 순수 스마트머니(임상 미적용)"
_V3_SUB = "진입 {entry} 시가 · {n}종목 동일가중 · SL-30/vol3x/hold120 · 신규룰($2B상한+R&D≥$50M) + 남는현금 T-bill"
SAT_TRACKS = [
    {'key': 'shared_tracker', 'label': '🔴1️⃣ Satellite (5/26)', 'ai': False, 'kind': 'tracker',
     'heading': '🔴1️⃣ 5/26 추천종목 (Scouter 15선 · Market-only)', 'sub': _MKT_SUB, 'bench_label': '5/26'},
    {'key': 'config_h_pit_0527', 'label': '🟡2️⃣ Satellite (5/28)', 'ai': True, 'kind': 'tracker',
     'heading': '🟡2️⃣ Config H (5/28 발굴 · 스마트머니+임상AI)', 'sub': _SL_SUB, 'bench_label': 'Config H 5/28'},
    {'key': 'satellite', 'label': '🟡2️⃣ Satellite v2 (6/5)', 'ai': True, 'kind': 'weighted'},
    {'key': 'config_h_pit_0610', 'label': '🟡2️⃣ Satellite (6/11)', 'ai': True, 'kind': 'tracker',
     'heading': '🟡2️⃣ Config H (6/11 발굴 · 스마트머니+임상AI)', 'sub': _SL_SUB, 'bench_label': 'Config H 6/11'},
    {'key': 'config_h_pit_0618a', 'label': '🟡2️⃣ Satellite (6/18)', 'ai': True, 'kind': 'tracker',
     'heading': '🟡2️⃣ Config H (6/18 발굴 · 스마트머니+임상AI)', 'sub': _SL_SUB, 'bench_label': 'Config H 6/18'},
    {'key': 'config_h_pit_0618b', 'label': '🟡2️⃣ Satellite (6/22)', 'ai': True, 'kind': 'tracker',
     'heading': '🟡2️⃣ Config H (6/22 발굴 · 스마트머니+임상AI)', 'sub': _SL_SUB, 'bench_label': 'Config H 6/22'},
    {'key': 'strong_buy_tracker', 'label': '🔴1️⃣ Satellite (6/25)', 'ai': False, 'kind': 'tracker',
     'heading': '🔴1️⃣ 6/25 Strong Buy 보드 · Market-only', 'sub': _MKT_SUB, 'bench_label': '6/25'},
    {'key': 'config_h_0702', 'label': '🟡2️⃣ Satellite (7/2)', 'ai': True, 'kind': 'tracker',
     'heading': '🟡2️⃣ Config H (7/2 발굴 · 스마트머니+임상AI)', 'sub': _SL_SUB, 'bench_label': 'Config H 7/2'},
    {'key': 'config_h_0714', 'label': '🟡2️⃣ Satellite (7/14)', 'ai': True, 'kind': 'tracker',
     'heading': '🟡2️⃣ Config H (7/14 재발굴 · 스마트머니+임상AI)', 'sub': _SL_SUB, 'bench_label': 'Config H'},
    {'key': 'config_h_0721', 'label': '🟡2️⃣ Satellite (7/21)', 'ai': True, 'kind': 'tracker',
     'heading': '🟡2️⃣ Config H (7/21 발굴 · 스마트머니+임상AI)', 'sub': _SL_SUB, 'bench_label': 'Config H 7/21'},
    {'key': 'config_h_0728', 'label': '🟡2️⃣ Satellite (7/28)', 'ai': True, 'kind': 'tracker',
     'heading': '🟡2️⃣ Config H (7/28 재발굴 · 스마트머니+임상AI)', 'sub': _SL_SUB, 'bench_label': 'Config H 7/28'},
    {'key': 'satellite_v3_0721', 'label': '🟢3️⃣ Satellite (7/21 신규룰)', 'ai': True, 'kind': 'tracker',
     'heading': '🟢3️⃣ Satellite v3 (7/21 발굴 · 신규룰: $2B상한 + R&D≥$50M + T-bill)', 'sub': _V3_SUB, 'bench_label': 'v3 7/21'},
    {'key': 'satellite_v3_0728', 'label': '🟢3️⃣ Satellite (7/28 신규룰)', 'ai': True, 'kind': 'tracker',
     'heading': '🟢3️⃣ Satellite v3 (7/28 발굴 · 신규룰: $2B상한 + R&D≥$50M + T-bill)', 'sub': _V3_SUB, 'bench_label': 'v3 7/28'},
]
# Only keep tracks whose data is actually present in portfolios.json.
SAT_TRACKS = [t for t in SAT_TRACKS if t['key'] in PF]
SAT_PAGE_MAP = {t['label']: t for t in SAT_TRACKS}


# ═══ Sidebar ═══
st.sidebar.title("BERA")
st.sidebar.caption("Biotech Event-driven Research & Alpha")
st.sidebar.markdown("---")

TERMINAL_PAGE = "🛰️ Signal Terminal"
# Nav grouped by STRATEGY FAMILY so the two lanes are obvious at a glance:
#   🔵 Core 계열     = 중대형주(시총 $2B+) 위주, 벤치마크 IBB
#   🟢 Satellite 계열 = 소형주 포함 이벤트드리븐, 벤치마크 XBI (Signal Terminal 포함)
OVERVIEW_PAGES = ["📊 Summary", "🧬 Quality Score", "📈 종목별 상세"]
CORE_PAGES = [
    "💰 Core (5/18, v1)",
    "🅰️ Core (5/28, v2)",
    "🏛️ Core (6/5, v3)",
    "🗓️ Core (6/30, v4)",
    "🚀 Core (7/17, v5)",
]
SATELLITE_PAGES = [t['label'] for t in SAT_TRACKS]  # date-per-page, ascending
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

# Satellite 계열이 전략의 중심 → Core 계열보다 위에 배치.
# With-AI(임상 게이트) / Market-only(임상 미적용)를 하위그룹으로 분리 — 둘은
# 같은 Satellite지만 임상 AI 유무가 달라 섞어 놓으면 혼동됨. 각 그룹 내 날짜순.
st.sidebar.markdown("**🟢 Satellite 계열**")
st.sidebar.caption("소형주 포함 · 이벤트드리븐 · vs XBI")
st.sidebar.caption("With BERA AI · 🟡2️⃣ Config H(스마트머니+임상) · 🟢3️⃣ 신규룰($2B상한+R&D+T-bill)")
for t in SAT_TRACKS:
    if t['ai']:
        _nav_button(t['label'])
st.sidebar.caption("Market-only · 🔴1️⃣ 순수 스마트머니(임상 미적용)")
for t in SAT_TRACKS:
    if not t['ai']:
        _nav_button(t['label'])

st.sidebar.markdown("**🔵 Core 계열**")
st.sidebar.caption("중대형주 · 시총 $2B+ · vs IBB")
for label in CORE_PAGES:
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

# Stale session on a page that no longer exists (e.g. the old grouped Satellite
# labels before the date-per-page split) → bounce to the default landing page.
_ALL_PAGES = set(OVERVIEW_PAGES + CORE_PAGES + SATELLITE_PAGES + INSTITUTIONAL_PAGES)
if st.session_state.page not in _ALL_PAGES:
    st.session_state.page = "🧬 Quality Score"

page = st.session_state.page

st.sidebar.markdown("---")
if st.sidebar.button("Refresh"):
    st.cache_data.clear()
st.sidebar.caption("Updated: 2026-07-30")


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


# ═══ Page: Satellite discovery (date-per-page, dispatched from SAT_TRACKS) ═══
elif page in SAT_PAGE_MAP:
    t = SAT_PAGE_MAP[page]
    cfg = PF[t['key']]
    ai_line = ("스마트머니 시그널 + 임상 AI 게이트(prob≥0.5, 3yr P2/3)" if t['ai']
               else "순수 스마트머니(Scouter), 임상 게이트 없음")
    st.title(f"{t['label']}")
    st.caption(f"🟢 Satellite 계열 · 소형주 이벤트드리븐 · 벤치 XBI · {ai_line}")
    st.markdown("---")

    if t['kind'] == 'weighted':
        render_satellite_v2(cfg)
    else:
        sub = t['sub'].format(entry=cfg['entry_date'], n=len(cfg['tickers']),
                              sl=int(cfg.get('sl', 0) * 100))
        if t.get('bench_label') == 'Config H' and cfg.get('backtest_note'):
            sub = f"{sub} · {cfg['backtest_note']}"
        render_tracker_track(cfg, t['heading'], sub, t['bench_label'])


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


# ═══ Page: Core (7/17, v5) ═══
elif page == "🚀 Core (7/17, v5)":
    st.title("Core (7/17, v5) -- 2026-07-17 신규 발굴 트랙")
    st.caption("🔵 Core 계열 · Core v2 전략(순수 prob top20 + SI-drop) · 시총 $2B+ · 벤치 IBB · 동일가중")
    st.markdown(CV2['entry_note'])
    st.markdown(CV2['backtest_note'])
    st.markdown("---")

    sl = CV2.get('sl', -0.15)
    meta = {p['ticker']: p for p in CV2['portfolio']}
    total_mult = sum(p['weight_mult'] for p in CV2['portfolio'])
    names = tuple((p['ticker'], p['entry'], p['weight_mult']) for p in CV2['portfolio'])
    rows, port_ret, daily_series = compute_core_monthend_sl(names, CV2['entry_date'], sl)

    if rows is None:  # history unavailable → degrade to live buy&hold
        prices = get_prices_batch(list(meta))
        rows = {}
        for tk, p in meta.items():
            cur = prices.get(tk, p['entry'])
            if cur <= 0: cur = p['entry']
            rows[tk] = {'current': cur, 'ret': (cur - p['entry']) / p['entry'] * 100, 'status': '보유'}
        port_ret = sum(rows[tk]['ret'] * meta[tk]['weight_mult'] / total_mult for tk in rows)
        daily_series = None
        st.caption("⚠️ 일별 히스토리 로드 실패 — SL 미적용 buy&hold로 임시 표시")

    held = [tk for tk in rows if rows[tk]['status'] == '보유']
    stopped = [tk for tk in rows if rows[tk]['status'] != '보유']
    seed = CV2['seed_usd']
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Active", f"{len(held)} / {CV2['max_slots']} slots")
    c2.metric("Invested", f"${seed:,.0f}")
    c3.metric("PnL", f"${seed * port_ret / 100:+,.0f}", delta=f"{port_ret:+.2f}%")
    c4.metric("손절 / 보유", f"{len(stopped)} / {len(held)}")
    if stopped:
        parts = [f"{tk} {rows[tk]['ret']:+.1f}% ({rows[tk]['status']})" for tk in stopped]
        st.warning(f"🛑 손절 진입가−{abs(int(sl*100))}% · 월말 체크 (현금화, 재분배 없음): " + " · ".join(parts))
    if held:
        df = pd.DataFrame([{'Ticker': tk, 'Weight': f"{meta[tk]['weight_mult']/total_mult*100:.1f}%",
                            'Prob': meta[tk]['prob'], 'Entry': meta[tk]['entry'],
                            'Current': rows[tk]['current'], 'PnL%': rows[tk]['ret']} for tk in held])
        st.dataframe(df.style.format({
            'Prob': '{:.3f}', 'Entry': '${:.2f}', 'Current': '${:.2f}', 'PnL%': '{:+.1f}%'
        }).map(lambda v: 'color:#2ecc71' if isinstance(v,(int,float)) and v>0 else
                    ('color:#e74c3c' if isinstance(v,(int,float)) and v<0 else ''),
                    subset=['PnL%']),
            width='stretch', hide_index=True)
    show_bench(port_ret, CV2['entry_date'], CV2['bench'], "Core (7/17, v5)",
               bera_daily_override=daily_series)


# ═══ Page: Core (6/30, v4) — 6/30 PIT 발굴 코호트 ═══
elif page == "🗓️ Core (6/30, v4)":
    st.title("Core (6/30, v4) -- 2026-06-30 발굴 코호트")
    st.caption("🔵 Core 계열 · 시총 $2B+ · 벤치 IBB · Core v2 전략 as-of 6/30 발굴(PIT)")
    PIT = PF.get('core_v2_pit_0630')
    if PIT:
        st.markdown(PIT['note'] + " · " + PIT['backtest_note'])
        sl = PIT.get('sl', -0.15)
        meta = {p['ticker']: p for p in PIT['portfolio']}
        names = tuple((p['ticker'], p['entry'], p['weight_mult']) for p in PIT['portfolio'])
        rows, port_ret, daily_series = compute_core_monthend_sl(names, PIT['entry_date'], sl)

        if rows is None:  # history unavailable → degrade to live buy&hold
            prices = get_prices_batch(list(meta))
            rows = {}
            for tk, p in meta.items():
                cur = prices.get(tk, p['entry'])
                if cur <= 0: cur = p['entry']
                rows[tk] = {'current': cur, 'ret': (cur - p['entry']) / p['entry'] * 100,
                            'status': '보유'}
            tw = sum(meta[tk]['weight_mult'] for tk in rows)
            port_ret = sum(rows[tk]['ret'] * meta[tk]['weight_mult'] / tw for tk in rows) if tw else 0.0
            daily_series = None
            st.caption("⚠️ 일별 히스토리 로드 실패 — SL 미적용 buy&hold로 임시 표시")

        held = [tk for tk in rows if rows[tk]['status'] == '보유']
        stopped = [tk for tk in rows if rows[tk]['status'] != '보유']
        seed = PIT['seed_usd']
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Portfolio", f"{port_ret:+.2f}%")
        c2.metric("Invested", f"${seed:,.0f}")
        c3.metric("손절 / 보유", f"{len(stopped)} / {len(held)}")
        c4.metric("Entry", PIT['entry_date'])
        if stopped:
            parts = [f"{tk} {rows[tk]['ret']:+.1f}% ({rows[tk]['status']})" for tk in stopped]
            st.warning(f"🛑 손절 진입가−{abs(int(sl*100))}% · 월말 체크 (현금화, 재분배 없음): " + " · ".join(parts))
        if held:
            _df = pd.DataFrame([{'Ticker': tk, 'Prob': meta[tk]['prob'], 'Entry': meta[tk]['entry'],
                                 'Current': rows[tk]['current'], 'PnL%': rows[tk]['ret']} for tk in held])
            st.dataframe(_df.style.format({'Prob': '{:.3f}', 'Entry': '${:.2f}', 'Current': '${:.2f}',
                'PnL%': '{:+.1f}%'}).map(
                lambda v: 'color:#2ecc71' if isinstance(v, (int, float)) and v > 0 else
                    ('color:#e74c3c' if isinstance(v, (int, float)) and v < 0 else ''), subset=['PnL%']),
                width='stretch', hide_index=True)
        show_bench(port_ret, PIT['entry_date'], PIT['bench'], "Core (6/30, v4)",
                   bera_daily_override=daily_series)


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

    st.caption("라이브 페이퍼 트래킹 요약 · Return% = 진입일 이후 실현 수익률 · 벤치 대비. Satellite 계열이 전략 중심.")

    def _bench_ret(entry_date, bench_dict):
        try:
            bd = get_bench_data(entry_date, bench_dict)
            sym = list(bench_dict.keys())[0]
            return (sym, bd[sym]['ret']) if sym in bd else (list(bench_dict.keys())[0], None)
        except Exception:
            return (list(bench_dict.keys())[0] if bench_dict else '—', None)

    rows_s = []

    # ── 🟢 Satellite 계열 (중심) — 날짜순, tracker/weighted 자동 ──
    for t in SAT_TRACKS:
        cfg = PF[t['key']]
        try:
            if t['kind'] == 'weighted':
                tks = [p['ticker'] for p in cfg['portfolio']]
                pr = get_prices_batch(tks)
                tc = tv = 0
                for p in cfg['portfolio']:
                    cur = pr.get(p['ticker'], p['entry']) or p['entry']
                    if cur <= 0: cur = p['entry']
                    q = int(cfg['seed_usd'] * p['weight_pct'] / 100 / p['entry'])
                    tc += q * p['entry']; tv += q * cur
                ret = (tv / tc - 1) * 100 if tc > 0 else None
                n = len(cfg['portfolio'])
                bsym, bret = _bench_ret(cfg['entry_date'], cfg['bench'])
            else:
                ret, _tr, bret, n, _d = compute_shared_tracker(
                    tuple(cfg['tickers']), cfg['entry_date'], cfg['sl'],
                    cfg['vol_mult'], cfg['drop_th'], cfg['hold'])
                bsym = 'XBI'
            rows_s.append({'Family': '🟢 Sat', 'Portfolio': t['label'],
                'Entry': cfg['entry_date'], 'N': n, 'Return%': ret, 'Bench': bsym,
                'Bench%': bret, 'vs Bench': (ret - bret) if (ret is not None and bret is not None) else None})
        except Exception:
            pass

    # ── 🔵 Core 계열 ──
    def _core_bh(cfg, label, weight_key='weight_mult'):
        tks = [p['ticker'] for p in cfg['portfolio']]
        pr = get_prices_batch(tks)
        tm = sum(p.get(weight_key, 1) for p in cfg['portfolio'])
        tc = tv = 0
        for p in cfg['portfolio']:
            cur = pr.get(p['ticker'], p['entry']) or p['entry']
            if cur <= 0: cur = p['entry']
            q = max(1, int(cfg['seed_usd'] * p.get(weight_key, 1) / tm / p['entry']))
            tc += q * p['entry']; tv += q * cur
        ret = (tv / tc - 1) * 100 if tc > 0 else None
        bsym, bret = _bench_ret(cfg['entry_date'], cfg['bench'])
        return {'Family': '🔵 Core', 'Portfolio': label, 'Entry': cfg['entry_date'],
                'N': len(cfg['portfolio']), 'Return%': ret, 'Bench': bsym, 'Bench%': bret,
                'vs Bench': (ret - bret) if (ret is not None and bret is not None) else None}

    try:
        # Core Live (5/18) — qty 기반, SL 반영
        pxl = get_prices_batch([p['ticker'] for p in LIVE['portfolio']])
        tc = tv = 0
        for p in LIVE['portfolio']:
            cur = pxl.get(p['ticker'], p['entry']) or p['entry']
            if cur <= 0: cur = p['entry']
            tc += p['qty'] * p['entry']; tv += p['qty'] * cur
        lret = (tv - tc - LIVE['sl_loss']) / LIVE['orig_cost'] * 100 if LIVE['orig_cost'] > 0 else None
        lb, lbr = _bench_ret(LIVE['entry_date'], LIVE['bench'])
        rows_s.append({'Family': '🔵 Core', 'Portfolio': 'Core (5/18, v1) Live', 'Entry': LIVE['entry_date'],
            'N': len(LIVE['portfolio']), 'Return%': lret, 'Bench': lb, 'Bench%': lbr,
            'vs Bench': (lret - lbr) if (lret is not None and lbr is not None) else None})
    except Exception:
        pass
    for _k, _lbl in [('core_new', 'Core (6/5, v3)'), ('core_v2_pit_0630', 'Core (6/30, v4)'),
                     ('core_v2_0717', 'Core (7/17, v5)')]:
        _cfg = PF.get(_k)
        if _cfg:
            try:
                rows_s.append(_core_bh(_cfg, _lbl))
            except Exception:
                pass

    sdf = pd.DataFrame(rows_s)
    st.dataframe(sdf.style.format({
        'Return%': '{:+.1f}%', 'Bench%': '{:+.1f}%', 'vs Bench': '{:+.1f}%p'
    }, na_rep='—').map(lambda v: 'color:#2ecc71' if isinstance(v, (int, float)) and v > 0 else
                ('color:#e74c3c' if isinstance(v, (int, float)) and v < 0 else ''),
                subset=['Return%', 'vs Bench']),
        width='stretch', hide_index=True)
    st.caption("Satellite tracker = SL−30/vol/hold 규율 반영 실현 수익 · Core = buy&hold. 벤치: Satellite XBI / Core IBB.")

    st.markdown("---")
    st.markdown("""
### About BERA
BERA (Biotech Event-driven Research & Alpha) is a quantitative biotech investment research system.

**Satellite Strategy** (전략 중심): Smart-money signal tracking (13G/13F · insider · short-interest)
+ clinical AI confirmation gate for small/mid-cap event-driven biotech. Point-in-time backtest
**CAGR 86.0% / Sharpe 2.16** (2023-04~2026-07) — see Quality Score page.

**Core Strategy**: AI clinical trial success prediction + fundamental filters for large-cap ($2B+).

Paper tracking started 2026. Live results via yfinance.
""")
    st.caption("BERA | hansol.kang@bera.ai")


# ═══ Page: Quality Score ═══
elif page == "🧬 Quality Score":
    st.title("Quality Score — Clinical AI Pipeline Scoring")
    st.caption("🧬 임상 성공확률 스코어 · 전 계열 공통 신호 · 투자 검증은 Satellite 이벤트드리븐(Point-in-Time, 벤치 XBI)")
    st.markdown("""
BERA's proprietary AI model predicts clinical trial success probability for every active trial
across 814 US-listed biotech companies. The **Quality Score** is the average predicted success
probability of a company's currently active clinical trials — a composite measure of pipeline
strength at any given point in time.

Why does this generate alpha? Clinical trial design documents are publicly available, but the
market has not yet priced in the systematic success/failure probabilities embedded in them.
This information asymmetry — new information in the EMH sense — is what BERA exploits.
""")

    # ── Backtest Performance: clinical gate in the Satellite strategy (point-in-time) ──
    st.markdown("---")
    st.markdown("### Backtest: Clinical AI Gate in the Satellite Strategy (Point-in-Time)")
    st.markdown("""
The clinical Quality Score is used as a **confirmation gate** on top of smart-money signals
(institutional 13G/13F · insider buying · short-interest) in BERA's small/mid-cap event-driven
**Satellite** strategy. Below is the **point-in-time** backtest — every position uses only data
that was public on the trade date: survivorship-free, with SEC/FINRA publication-lag corrected.
Period 2023-04 — 2026-07 (3.3 yrs) · equal-weight top-10 · buy & hold · \$100M float floor · vs XBI.
""")

    @st.cache_data(ttl=3600)
    def _load_sat_curve():
        try:
            return pd.read_csv(os.path.join(DATA_DIR, 'satellite_pit_curve.csv'), parse_dates=['date'])
        except Exception:
            return None
    curve = _load_sat_curve()

    bt_c1, bt_c2, bt_c3, bt_c4 = st.columns(4)
    bt_c1.metric("CAGR", "86.0%")
    bt_c2.metric("Sharpe Ratio", "2.16")
    bt_c3.metric("Max Drawdown", "-44.9%")
    bt_c4.metric("누적 (3.3y) vs XBI", "+685%", delta="vs XBI +93%")

    st.info(
        "★ **임상 게이트의 기여**: 같은 스마트머니 시그널에서 임상 게이트가 없으면 CAGR 44.5% (Sharpe 1.11), "
        "임상 Quality Score를 얹으면 **86.0% (Sharpe 2.16)** — 동일 PIT 조건에서 +41.5%p. "
        "임상 필터가 스마트머니 위에 곱셈으로 작동하는 것이 핵심."
    )

    if curve is not None:
        # (1) Cumulative return line chart vs XBI
        fig_cum = go.Figure()
        fig_cum.add_trace(go.Scatter(x=curve['date'], y=curve['bera_cumret'],
            name='BERA Satellite (PIT)', line=dict(color='#1976D2', width=2.5)))
        fig_cum.add_trace(go.Scatter(x=curve['date'], y=curve['xbi_cumret'],
            name='XBI (Biotech ETF)', line=dict(color='#E53935', width=2)))
        fig_cum.update_layout(
            title='Cumulative Return: BERA Satellite (PIT) vs XBI (%)',
            yaxis_title='Cumulative Return (%)', height=430, hovermode='x unified',
            legend=dict(orientation="h", yanchor="bottom", y=1.02), margin=dict(t=40))
        fig_cum.add_hline(y=0, line_dash="dot", line_color="gray")
        st.plotly_chart(fig_cum, use_container_width=True)

        # (2) Annual returns bar chart (BERA vs XBI), derived from the curve
        _c = curve.copy()
        _c['year'] = _c['date'].dt.year
        _c['b_eq'] = 1 + _c['bera_cumret'] / 100
        _c['x_eq'] = 1 + _c['xbi_cumret'] / 100
        rows_a = []
        pb = px_ = 1.0
        for yr in sorted(_c['year'].unique()):
            g = _c[_c['year'] == yr]
            be, xe = g['b_eq'].iloc[-1], g['x_eq'].iloc[-1]
            rows_a.append({'Year': ('%d*' % yr if yr == _c['year'].max() else str(yr)),
                           'BERA Satellite': (be / pb - 1) * 100, 'XBI': (xe / px_ - 1) * 100})
            pb, px_ = be, xe
        adf = pd.DataFrame(rows_a)
        fig_ann = go.Figure()
        fig_ann.add_trace(go.Bar(x=adf['Year'], y=adf['BERA Satellite'],
            name='BERA Satellite (PIT)', marker_color='#1976D2'))
        fig_ann.add_trace(go.Bar(x=adf['Year'], y=adf['XBI'], name='XBI', marker_color='#E53935'))
        fig_ann.update_layout(
            title='Annual Returns: BERA Satellite (PIT) vs XBI (%)',
            yaxis_title='Return (%)', barmode='group', height=380,
            legend=dict(orientation="h", yanchor="bottom", y=1.02), margin=dict(t=40))
        fig_ann.add_hline(y=0, line_dash="dot", line_color="gray")
        st.plotly_chart(fig_ann, use_container_width=True)
        st.caption(
            "2026* = YTD(부분연도, ~7월). PIT(Point-in-Time) = 발굴 시점에 공개돼 있던 데이터만 사용 "
            "— 현재 시총 소급(생존편향)·미공개 공시(look-ahead) 제거. 소형주 유니버스라 MDD −45%는 크지만 "
            "매년 XBI 상회. (3.3년·1사이클·small-N caveat)"
        )
    else:
        st.info("백테스트 곡선 데이터를 불러오지 못했습니다.")

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

    st.markdown("---")
    st.caption("BERA Satellite Signal Terminal · 기관 전용 · 무단 배포 금지")
