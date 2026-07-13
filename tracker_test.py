import yfinance as yf, pandas as pd, numpy as np
def compute(tickers, entry_date, sl, vol_mult, drop_th, hold):
    O,C,V={},{},{}
    for tk in list(tickers)+['XBI']:
        try:
            h=yf.Ticker(tk).history(start='2026-04-20',interval='1d',auto_adjust=True)[['Open','Close','Volume']].dropna()
            if not h.empty: O[tk],C[tk],V[tk]=h['Open'],h['Close'],h['Volume']
        except: pass
    cl=pd.DataFrame(C).sort_index().ffill()
    op=pd.DataFrame(O).reindex(cl.index).ffill(); vol=pd.DataFrame(V).reindex(cl.index).ffill()
    tks=[t for t in tickers if t in cl.columns]
    days=[d for d in cl.index if str(d.date())>=entry_date]; e0=days[0]
    entry={tk:float(op.loc[e0,tk]) for tk in tks}
    val={tk:1/len(tks) for tk in tks}; alive=set(tks); cash=0.0; status={tk:'hold' for tk in tks}; exret={}
    for i,d in enumerate(days):
        pv=cl.index[cl.index.get_loc(d)-1]
        for tk in list(alive):
            m=(cl.loc[d,tk]/op.loc[d,tk]) if i==0 else (cl.loc[d,tk]/cl.loc[pv,tk])
            if np.isfinite(m): val[tk]*=m
        ex=[]
        for tk in list(alive):
            pt=cl.loc[d,tk]; ep=entry[tk]; pp=cl.loc[pv,tk]
            if i+1>=hold: ex.append((tk,'time')); continue
            if ep>0 and (pt-ep)/ep<=sl: ex.append((tk,'SL')); continue
            vloc=vol.index.get_loc(d); av=vol[tk].iloc[max(0,vloc-20):vloc].mean()
            if av>0 and pp>0 and vol.loc[d,tk]>av*vol_mult and (pt-pp)/pp<=drop_th: ex.append((tk,'vol'))
        if ex:
            freed=sum(val[tk] for tk,_ in ex)
            for tk,why in ex: alive.discard(tk); val[tk]=0.0; status[tk]=why+'@'+str(d.date())[5:]; exret[tk]=(cl.loc[d,tk]-entry[tk])/entry[tk]*100
            if alive:
                a=freed/len(alive)
                for tk in alive: val[tk]+=a
            else: cash+=freed
    pr=(sum(val.values())+cash-1)*100
    last=cl.index[-1]
    xbi=(float(cl.loc[last,'XBI'])/float(op.loc[e0,'XBI'])-1)*100
    exits={tk:status[tk] for tk in tks if status[tk]!='hold'}
    return pr,xbi,exits
tks=("TRDA","IKT","KRRO","TENX","BDSX","ARTV","LENZ","ALXO","OBIO","TNXP","ALEC","SRZN","STRO","ABCL","CMPX")
pr,xbi,exits=compute(tks,'2026-05-26',-0.25,3.0,-0.07,120)
print('portfolio %+.2f%%  XBI %+.2f%%  gap %+.2f%%p'%(pr,xbi,pr-xbi))
print('exits:',exits)
