"""osu! Farm Finder — acha mapas de farm (osu! standard) calibrados pro seu nível.

Como funciona:
  1. Lê seu perfil e seu top 100 pela API v2 do osu!.
  2. Pega jogadores perto do seu rank (um pouco acima) e lê o top 100 deles.
  3. Conta quais mapas+mods mais aparecem nesses tops (= mapas de farm do seu nível).
  4. Calcula com rosu-pp quanto cada um daria pra VOCÊ na sua acc típica.
  5. Filtra o que você já tem, o que é difícil demais e o que não entraria no seu top.
  6. Gera um relatório HTML e abre no navegador.
"""
import argparse
import html
import json
import math
import statistics
import sys
import time
import webbrowser
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import requests
import rosu_pp_py as rosu

import os, shutil
if getattr(sys, "frozen", False):
    BASE = Path(os.environ.get("APPDATA", Path.home())) / "osu Farm Finder"
    BASE.mkdir(parents=True, exist_ok=True)
    _bundled = Path(sys._MEIPASS) / "config.json"
    if _bundled.exists() and not (BASE / "config.json").exists():
        shutil.copy(_bundled, BASE / "config.json")
else:
    BASE = Path(__file__).resolve().parent
CONFIG = BASE / "config.json"
CACHE = BASE / "cache"
OSU_CACHE = CACHE / "osu"
TOPS_CACHE = CACHE / "tops"
API = "https://osu.ppy.sh/api/v2"

# mods que mexem em pp/dificuldade; o resto (NF, SD, PF, CL, TD...) é ignorado
KEEP_MODS = {"EZ", "HD", "HR", "DT", "HT", "FL"}
MOD_ALIAS = {"NC": "DT", "DC": "HT"}
MOD_ORDER = ["EZ", "HD", "HT", "DT", "HR", "FL"]


# ---------------------------------------------------------------- API
class OsuAPI:
    def __init__(self, client_id, client_secret, delay=0.7):
        self.delay = delay
        self.s = requests.Session()
        self.s.headers["x-api-version"] = "20240529"
        r = requests.post("https://osu.ppy.sh/oauth/token", data={
            "client_id": client_id, "client_secret": client_secret,
            "grant_type": "client_credentials", "scope": "public"}, timeout=30)
        if r.status_code != 200:
            sys.exit(f"Falha no login da API ({r.status_code}). Confere Client ID/Secret no config.json.\n{r.text[:300]}")
        self.s.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        self._last = 0.0

    def get(self, path, **params):
        for tentativa in range(5):
            wait = self.delay - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            r = self.s.get(API + path, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(10 * (tentativa + 1))
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"API recusou muitas vezes: {path}")

    def user(self, name):
        return self.get(f"/users/@{name}/osu") if not str(name).isdigit() else self.get(f"/users/{name}/osu")

    def best(self, user_id, max_age_h=72):
        f = TOPS_CACHE / f"{user_id}.json"
        if f.exists() and time.time() - f.stat().st_mtime < max_age_h * 3600:
            return json.loads(f.read_text(encoding="utf-8"))
        data = self.get(f"/users/{user_id}/scores/best", mode="osu", limit=100) or []
        slim = [slim_score(s) for s in data]
        slim = [s for s in slim if s]
        f.write_text(json.dumps(slim), encoding="utf-8")
        return slim

    def ranking_page(self, page, country=None):
        params = {"cursor[page]": page}
        if country:
            params["country"] = country
        d = self.get("/rankings/osu/performance", **params)
        return (d or {}).get("ranking", [])


def norm_mods(mods):
    out = set()
    for m in mods or []:
        a = m.get("acronym") if isinstance(m, dict) else m
        if isinstance(m, dict) and a in ("DT", "NC", "HT", "DC") and m.get("settings", {}).get("speed_change") not in (None, 1.5, 0.75):
            return None  # velocidade customizada do lazer: pp não comparável
        a = MOD_ALIAS.get(a, a)
        if a in KEEP_MODS:
            out.add(a)
    return "".join(m for m in MOD_ORDER if m in out) or "NM"


def slim_score(s):
    b, bs = s.get("beatmap") or {}, s.get("beatmapset") or {}
    mods = norm_mods(s.get("mods"))
    if not b or mods is None or not s.get("pp"):
        return None
    return {
        "bid": b["id"], "sid": b.get("beatmapset_id") or bs.get("id"),
        "mods": mods, "pp": float(s["pp"]), "acc": float(s.get("accuracy", 0)),
        "stars": b.get("difficulty_rating"), "bpm": b.get("bpm"), "len": b.get("total_length"),
        "ar": b.get("ar"), "cs": b.get("cs"), "od": b.get("accuracy"),
        "version": b.get("version"), "artist": bs.get("artist"), "title": bs.get("title"),
        "creator": bs.get("creator"),
    }


# ---------------------------------------------------------------- pp
def osu_file(bid):
    f = OSU_CACHE / f"{bid}.osu"
    if not f.exists():
        r = requests.get(f"https://osu.ppy.sh/osu/{bid}", timeout=30)
        r.raise_for_status()
        if not r.content:
            raise RuntimeError("arquivo vazio")
        f.write_bytes(r.content)
        time.sleep(0.3)
    return f


def calc(bid, mods, accs):
    bm = rosu.Beatmap(path=str(osu_file(bid)))
    m = "" if mods == "NM" else mods
    diff = rosu.Difficulty(mods=m).calculate(bm)
    pps = {a: rosu.Performance(mods=m, accuracy=a * 100).calculate(diff).pp for a in accs}
    return diff.stars, pps


def weighted(pps):
    return sum(p * 0.95 ** i for i, p in enumerate(sorted(pps, reverse=True)[:100]))


# ---------------------------------------------------------------- peers
def find_peers(api, user, n_peers, log):
    st = user["statistics"]
    grank, crank, country = st.get("global_rank"), st.get("country_rank"), user.get("country_code")
    if not grank:
        sys.exit("Esse jogador não tem rank global no standard (inativo?).")
    # jogadores um pouco melhores que você: ranks de ~0.65x até ~1.05x o seu
    if grank <= 10000:
        lo, hi, ctry, label = grank * 0.65, grank * 1.05, None, f"global #{grank}"
    elif crank and crank <= 10000:
        lo, hi, ctry, label = crank * 0.65, crank * 1.05, country, f"{country} #{crank} (global acima de 10k, usando ranking do país)"
    else:
        return peers_from_recent(api, user, n_peers, log)
    log(f"Buscando jogadores parecidos: {label}")
    pages = sorted({max(1, min(200, math.ceil(r / 50))) for r in range(int(lo), int(hi) + 1, 50)})
    # pega páginas espalhadas pela faixa até ter jogadores suficientes
    if len(pages) * 50 > n_peers * 3:
        step = max(1, len(pages) // max(1, math.ceil(n_peers / 25)))
        pages = pages[::step]
    peers = []
    for p in pages:
        for row in api.ranking_page(p, ctry):
            u = row.get("user", {})
            if u.get("id") and u["id"] != user["id"]:
                peers.append((u["id"], u.get("username"), row.get("pp")))
    my_pp = st.get("pp", 0)
    # prioriza quem tem pp mais perto (e um pouco acima) do seu
    peers.sort(key=lambda x: abs((x[2] or 0) - my_pp * 1.08))
    return peers[:n_peers]


def peers_from_recent(api, user, n_peers, log, rounds=6):
    """Rank fora do top 10k (o ranking da API só vai até lá): pega quem jogou
    agora há pouco no servidor e filtra os que têm pp parecido."""
    my_pp = user["statistics"].get("pp", 0)
    lo, hi, target = my_pp * 0.9, my_pp * 1.6, my_pp * 1.15
    log(f"Rank fora do top 10k: procurando jogadores ativos com {lo:.0f}-{hi:.0f}pp...")
    seen, peers = set(), []
    for rnd in range(rounds):
        d = api.get("/scores", ruleset="osu") or {}
        ids = [i for i in {s["user_id"] for s in d.get("scores", [])} if i not in seen and i != user["id"]]
        seen.update(ids)
        for k in range(0, len(ids), 50):
            for u in (api.get("/users", **{"ids[]": ids[k:k + 50]}) or {}).get("users", []):
                pp = ((u.get("statistics_rulesets") or {}).get("osu") or {}).get("pp") or 0
                if lo <= pp <= hi:
                    peers.append((u["id"], u.get("username"), pp))
        print(f"\r  {len(seen)} jogadores olhados, {len(peers)} no seu nível", end="", flush=True)
        if len(peers) >= n_peers:
            break
        time.sleep(15)  # espera entrar scores novos
    print()
    peers.sort(key=lambda x: abs(x[2] - target))
    return peers[:n_peers]


# ---------------------------------------------------------------- main
def run(args):
    for d in (CACHE, OSU_CACHE, TOPS_CACHE):
        d.mkdir(parents=True, exist_ok=True)
    if not CONFIG.exists():
        CONFIG.write_text(json.dumps({"client_id": "", "client_secret": "", "username": ""}, indent=2), encoding="utf-8")
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    username = args.user or cfg.get("username")
    if not (cfg.get("client_id") and cfg.get("client_secret") and username):
        sys.exit(f"Preencha client_id, client_secret e username em {CONFIG}")

    log = lambda m: print(m, flush=True)
    api = OsuAPI(cfg["client_id"], cfg["client_secret"])
    user = api.user(username)
    if not user:
        sys.exit(f"Jogador '{username}' não encontrado.")
    st = user["statistics"]
    log(f"Jogador: {user['username']}  |  {st.get('pp', 0):.0f}pp  |  #{st.get('global_rank')}  |  {user.get('country_code')} #{st.get('country_rank')}")

    my_top = api.best(user["id"], max_age_h=1)
    if len(my_top) < 10:
        sys.exit("Seu top tem poucos plays pra calibrar (menos de 10).")
    my_pps = [s["pp"] for s in my_top]
    floor_pp = min(my_pps) if len(my_pps) >= 100 else 0
    acc = args.acc / 100 if args.acc else statistics.median(s["acc"] for s in my_top[:50])
    my_bids = {s["bid"] for s in my_top}

    # nível real: estrelas COM mods dos seus top 50
    log("Calibrando seu nível (estrelas com mods do seu top)...")
    my_stars = []
    for s in my_top[:50]:
        try:
            my_stars.append(calc(s["bid"], s["mods"], [acc])[0])
        except Exception:
            pass
    my_stars.sort()
    comfort = my_stars[int(len(my_stars) * 0.9) - 1] if my_stars else 6
    max_stars = comfort + args.margin
    mod_use = defaultdict(int)
    for s in my_top:
        mod_use[s["mods"]] += 1
    log(f"Acc típica {acc*100:.2f}%  |  seu limite confortável ~{comfort:.2f}★ (busca até {max_stars:.2f}★)  |  piso do top {floor_pp:.0f}pp")

    peers = find_peers(api, user, args.peers, log)
    log(f"Lendo o top 100 de {len(peers)} jogadores (com cache)...")
    agg = {}
    for i, (pid, pname, _) in enumerate(peers, 1):
        print(f"\r  {i}/{len(peers)} {pname:<20}", end="", flush=True)
        try:
            top = api.best(pid)
        except Exception as e:
            continue
        for s in top:
            k = (s["bid"], s["mods"])
            a = agg.setdefault(k, {"n": 0, "pp": [], "acc": [], "who": [], "s": s})
            a["n"] += 1
            a["pp"].append(s["pp"])
            a["acc"].append(s["acc"])
            a["who"].append(pname)
    print()

    if args.mods:
        allowed = {m.strip().upper() for m in args.mods.split(",")}
    else:
        allowed = None
    cands = [(k, a) for k, a in agg.items()
             if a["n"] >= args.min_count and k[0] not in my_bids
             and (allowed is None or k[1] in allowed)]
    cands.sort(key=lambda x: -x[1]["n"])
    cands = cands[:args.max_calc]
    log(f"{len(agg)} combinações mapa+mod vistas; calculando pp de {len(cands)} candidatos...")

    base_total = weighted(my_pps)
    results = []
    for i, ((bid, mods), a) in enumerate(cands, 1):
        print(f"\r  {i}/{len(cands)}", end="", flush=True)
        try:
            stars, pps = calc(bid, mods, sorted({0.95, 0.97, 0.99, round(acc, 4)}))
        except Exception:
            continue
        mine = pps[round(acc, 4)]
        if stars > max_stars or mine <= floor_pp:
            continue
        gain = weighted(my_pps + [mine]) - base_total
        # quanto mais gente do seu nível usa, mais "farm" é
        farm = a["n"] / len(peers) * 100
        results.append({**a["s"], "mods": mods, "stars_mod": stars, "pp_me": mine, "acc_me": acc, "pp95": pps[0.95],
                        "pp97": pps[0.97], "pp99": pps[0.99], "gain": gain, "count": a["n"], "farm": farm,
                        "peer_pp": statistics.median(a["pp"]), "peer_acc": statistics.median(a["acc"]),
                        "who": a["who"][:5]})
    print()
    results.sort(key=lambda r: -(r["farm"] ** 0.7 * r["gain"]))
    results = results[:args.top]
    log(f"{len(results)} recomendações.")

    out = BASE / "resultado.html"
    out.write_text(render(user, acc, comfort, max_stars, floor_pp, len(peers), results), encoding="utf-8")
    (BASE / "resultado.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    for r in results[:15]:
        log(f"  +{r['gain']:5.1f}pp  {r['pp_me']:4.0f}pp @ {r['acc_me']*100:.1f}%  {r['stars_mod']:.2f}★ {r['mods']:<6} {r['farm']:4.0f}%  {r['artist']} - {r['title']} [{r['version']}]")
    log(f"\nRelatório: {out}")
    meta = {"username": user["username"], "pp": st.get("pp", 0), "rank": st.get("global_rank"),
            "country": user.get("country_code"), "country_rank": st.get("country_rank"), "acc": acc,
            "comfort": comfort, "max_stars": max_stars, "floor_pp": floor_pp, "peers": len(peers),
            "date": datetime.now().strftime("%d/%m/%Y %H:%M")}
    (BASE / "resultado_meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    if not args.no_open:
        webbrowser.open(out.as_uri())
    return meta


STAR_STOPS = [(0.1, "#4290fb"), (1.25, "#4fc0ff"), (2.0, "#4fffd5"), (2.5, "#7cff4f"), (3.3, "#f6f05c"),
              (4.2, "#ff8068"), (4.9, "#ff4e6f"), (5.8, "#c645b8"), (6.7, "#6563de"), (7.7, "#18158e"), (9.0, "#000000")]


def star_color(s):
    """Mesma escala de cores das estrelas do site do osu!."""
    if s <= STAR_STOPS[0][0]:
        return STAR_STOPS[0][1]
    for (a, ca), (b, cb) in zip(STAR_STOPS, STAR_STOPS[1:]):
        if s <= b:
            t = (s - a) / (b - a)
            x = [int(ca[i:i + 2], 16) + (int(cb[i:i + 2], 16) - int(ca[i:i + 2], 16)) * t for i in (1, 3, 5)]
            return "#" + "".join(f"{round(v):02x}" for v in x)
    return STAR_STOPS[-1][1]


def render(user, acc, comfort, max_stars, floor_pp, n_peers, results):
    e = html.escape
    st = user["statistics"]
    max_gain = max((r["gain"] for r in results), default=1) or 1
    mods_found = sorted({r["mods"] for r in results}, key=lambda m: (m != "NM", len(m), m))
    cards = []
    for i, r in enumerate(results, 1):
        secs = int(r["len"] or 0)
        mins = f"{secs // 60}:{secs % 60:02d}"
        sc = star_color(r["stars_mod"])
        star_fg = "#ffd966" if r["stars_mod"] >= 6.5 else "#16131c"
        mods_html = "".join(f'<span class="mod m-{m}">{m}</span>' for m in
                            ([r["mods"]] if r["mods"] == "NM" else [r["mods"][k:k + 2] for k in range(0, len(r["mods"]), 2)]))
        search = f"{r['artist']} {r['title']} {r['version']} {r['creator']}".lower()
        cards.append(f"""<article class=card data-mods="{r['mods']}" data-q="{e(search)}" data-gain="{r['gain']:.2f}" data-pp="{r['pp_me']:.2f}" data-stars="{r['stars_mod']:.3f}" data-farm="{r['farm']:.2f}" data-len="{secs}" data-i="{i}">
<div class=cover style="background-image:url('https://assets.ppy.sh/beatmaps/{r['sid']}/covers/list@2x.jpg')"><span class=rank>#{i}</span></div>
<div class=info>
 <a class=title href="https://osu.ppy.sh/b/{r['bid']}" target=_blank title="Abrir no site do osu!">{e(r['title'] or '')}</a>
 <div class=artist>{e(r['artist'] or '')}</div>
 <div class=meta><span class=diff>[{e(r['version'] or '')}]</span> · mapeado por {e(r['creator'] or '')}</div>
 <div class=chips><span class=star style="background:{sc};color:{star_fg}">★ {r['stars_mod']:.2f}</span>{mods_html}
  <span class=chip>{r['bpm'] or 0:.0f} BPM</span><span class=chip>{mins}</span><span class=chip>AR {r['ar']}</span><span class=chip>CS {r['cs']}</span></div>
</div>
<div class=nums>
 <div class=pp><b>{r['pp_me']:.0f}</b><small>pp</small></div>
 <div class=need title="acc necessária (com FC) pra fazer esse pp">com <b>{r.get('acc_me', acc) * 100:.2f}%</b> de acc</div>
 <div class=accs><span>95% → {r['pp95']:.0f}</span><span>97% → {r['pp97']:.0f}</span><span>99% → {r['pp99']:.0f}</span></div>
</div>
<div class=gainbox>
 <div class=gain>+{r['gain']:.1f}<small>pp</small></div>
 <div class=bar><i style="width:{max(4, r['gain'] / max_gain * 100):.0f}%"></i></div>
 <div class=farm title="{r['count']} de {n_peers} jogadores do seu nível têm esse mapa no top"><b>{r['farm']:.0f}%</b> farm · {r['count']} jog.</div>
</div>
<div class=acts><a class="btn pri" href="osu://b/{r['bid']}" title="Abre direto no jogo (precisa de supporter)">osu!direct</a><a class=btn href="https://catboy.best/d/{r['sid']}">Baixar</a></div>
</article>""")
    chips = "".join(f'<button class="fchip" data-m="{m}">{m}</button>' for m in mods_found)
    avatar = e(user.get("avatar_url") or "")
    grank = st.get("global_rank")
    crank = st.get("country_rank")
    return f"""<!doctype html><html lang=pt-br><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>osu! Farm Finder · {e(user['username'])}</title>
<style>
:root{{--bg:#151517;--bg2:#1b1b1e;--card:#1d1d21;--card2:#26262b;--fg:#e6e6e9;--dim:#8e8e98;--pink:#e0609a;--pink2:#ec78ab;--line:#2c2c31;--green:#7cc48c}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 "Segoe UI",system-ui,sans-serif;padding:28px 16px 60px}}
.wrap{{max-width:1120px;margin:auto}}
a{{color:inherit;text-decoration:none}}
header{{display:flex;gap:18px;align-items:center;flex-wrap:wrap}}
.av{{width:64px;height:64px;border-radius:8px;background:var(--card) center/cover;border:1px solid var(--line)}}
.brand{{font-size:13px;color:var(--dim)}}
h1{{margin:0;font-size:24px;font-weight:700;line-height:1.2}}
.sub{{color:var(--dim);font-size:13px}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px;margin:22px 0 14px}}
.stat{{background:var(--card);border:1px solid var(--line);padding:10px 14px;border-radius:6px}}
.stat span{{color:var(--dim);font-size:12px}}
.stat b{{display:block;font-size:18px;font-weight:600;margin-top:1px}}
.stat.hl b{{color:var(--pink)}}
.help{{background:var(--bg2);border:1px solid var(--line);border-radius:6px;padding:12px 16px;color:var(--dim);font-size:13px;margin-bottom:18px}}
.help b{{color:var(--fg)}}
.tools{{position:sticky;top:0;z-index:5;display:flex;gap:10px;flex-wrap:wrap;align-items:center;padding:10px 0;background:linear-gradient(var(--bg) 70%,transparent)}}
.tools input,.tools select{{background:var(--card);border:1px solid var(--line);color:var(--fg);border-radius:4px;padding:8px 12px;font:inherit;outline:none}}
.tools input{{flex:1;min-width:0;width:100%}} .tools select{{max-width:100%}} .tools input:focus,.tools select:focus{{border-color:var(--pink)}}
.fchips{{display:flex;gap:6px;flex-wrap:wrap}}
.fchip{{background:var(--card);border:1px solid var(--line);color:var(--dim);border-radius:4px;padding:6px 12px;font:600 12px "Segoe UI",sans-serif;cursor:pointer}}
.fchip.on{{background:var(--pink);color:#1a1020;border-color:var(--pink)}}
.count{{color:var(--dim);font-size:12px;margin-left:auto}}
.list{{display:flex;flex-direction:column;gap:10px}}
.card{{display:grid;grid-template-columns:150px 1fr 120px 150px 112px;align-items:center;gap:16px;background:var(--card);border:1px solid var(--line);border-radius:6px;overflow:hidden;padding-right:14px}}
.card:hover{{border-color:var(--line2,#3a3a41);background:#202024}}
.cover{{align-self:stretch;min-height:96px;background:#2a2438 center/cover;position:relative}}
.cover:after{{content:"";position:absolute;inset:0;background:linear-gradient(90deg,transparent 40%,var(--card))}}
.rank{{position:absolute;left:6px;top:6px;z-index:1;background:#000b;font-weight:600;font-size:12px;padding:1px 7px;border-radius:4px}}
.info{{min-width:0;padding:10px 0}}
.title{{font-weight:600;font-size:15px;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}} .title:hover{{color:var(--pink)}}
.artist{{color:var(--fg);opacity:.8;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.meta{{color:var(--dim);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}} .diff{{color:var(--pink2);font-weight:700}}
.chips{{display:flex;gap:5px;flex-wrap:wrap;margin-top:7px}}
.chip,.star,.mod{{font-size:11px;font-weight:600;padding:1px 7px;border-radius:4px;white-space:nowrap}}
.chip{{background:var(--card2);color:var(--dim)}}
.mod{{background:#555;color:#fff}} .m-NM{{background:#4a4458}} .m-HD{{background:#f2c84b;color:#2a2000}} .m-HR{{background:#e45b5b}} .m-DT{{background:#8b6cf0}} .m-EZ{{background:#5bc77a;color:#082010}} .m-HT{{background:#6c8b99}} .m-FL{{background:#222;color:#fff;box-shadow:inset 0 0 0 1px #777}}
.nums{{text-align:right}}
.pp b{{font-size:22px;font-weight:600}} .pp small,.gain small{{font-size:12px;color:var(--dim);margin-left:2px;font-weight:700}}
.need{{font-size:11px;color:var(--dim);margin-top:-2px}} .need b{{color:var(--pink2)}}
.accs{{color:var(--dim);font-size:10.5px;display:flex;flex-direction:column;margin-top:3px;line-height:1.35}}
.gain{{color:var(--green);font-weight:600;font-size:18px}}
.bar{{height:4px;background:var(--card2);border-radius:9px;margin:4px 0 5px;overflow:hidden}} .bar i{{display:block;height:100%;background:var(--green)}}
.farm{{font-size:11px;color:var(--dim)}} .farm b{{color:var(--fg)}}
.acts{{display:flex;flex-direction:column;gap:6px}}
.btn{{text-align:center;background:var(--card2);border:1px solid var(--line);padding:5px 10px;border-radius:4px;font-weight:600;font-size:12px}}
.btn:hover{{border-color:var(--pink);color:var(--pink)}}
.btn.pri{{background:var(--pink);border-color:var(--pink);color:#1a1020}} .btn.pri:hover{{background:var(--pink2);color:#1a1020}}
.empty{{text-align:center;color:var(--dim);padding:40px;background:var(--card);border-radius:6px}}
footer{{color:var(--dim);font-size:12px;text-align:center;margin-top:28px}}
@media (max-width:860px){{
 .tools input{{flex-basis:100%}} .stat b{{font-size:18px}} h1{{font-size:24px}}
 .card{{grid-template-columns:minmax(0,1fr) auto;padding:0 0 12px;gap:8px 12px}}
 .cover{{grid-column:1/-1;min-height:110px}} .cover:after{{background:linear-gradient(transparent 30%,var(--card))}}
 .info{{grid-column:1/-1;padding:0 14px}} .nums{{text-align:left;padding-left:14px}} .gainbox{{padding-right:14px}}
 .acts{{grid-column:1/-1;flex-direction:row;padding:0 14px}} .acts .btn{{flex:1}}
}}
</style></head><body><div class=wrap>
<header><div class=av style="background-image:url('{avatar}')"></div>
<div><h1>{e(user['username'])}</h1><div class=brand>osu! Farm Finder · mapas de farm recomendados</div>
<div class=sub>gerado em {datetime.now():%d/%m/%Y às %H:%M} · comparado com {n_peers} jogadores do seu nível</div></div></header>
<div class=stats>
<div class="stat hl"><span>pp atual</span><b>{st.get('pp', 0):,.0f}</b></div>
<div class=stat><span>rank global</span><b>{'#' + format(grank, ',') if grank else '—'}</b></div>
<div class=stat><span>rank {e(user.get('country_code') or '')}</span><b>{'#' + format(crank, ',') if crank else '—'}</b></div>
<div class=stat><span>acc usada</span><b>{acc * 100:.2f}%</b></div>
<div class=stat><span>nível confortável</span><b>{comfort:.2f}★</b></div>
<div class=stat><span>busca até</span><b>{max_stars:.2f}★</b></div>
<div class=stat><span>piso do top</span><b>{floor_pp:.0f}pp</b></div>
</div>
<div class=help><b>Como ler:</b> <b>pp</b> é quanto o mapa dá fazendo FC com a <b>acc necessária</b> que aparece embaixo (a sua acc típica); dá pra ver também quanto daria com 95, 97 e 99%. <b>Ganho</b> é quanto o seu pp total sobe se você fizer o mapa com FC. <b>Farm %</b> é quantos jogadores do seu nível têm esse mapa+mod no top 100 — quanto maior, mais "farm" ele é.</div>
<div class=tools>
<input id=q type=search placeholder="Buscar música, artista, mapper...">
<select id=sort><option value=i>Melhor recomendação</option><option value=gain>Maior ganho</option><option value=pp>Mais pp</option><option value=farm>Mais farm %</option><option value=stars>Mais fácil (★)</option><option value=len>Mais curto</option></select>
<div class=fchips><button class="fchip on" data-m="*">Todos</button>{chips}</div>
<span class=count id=count></span>
</div>
<div class=list id=list>{''.join(cards) or '<div class=empty>Nada encontrado — tente aumentar "estrelas a mais" ou tirar o filtro de mods.</div>'}</div>
<div class=empty id=none style="display:none">Nenhum mapa com esse filtro.</div>
<footer>Dados da API do osu! · pp calculado com rosu-pp · as capas vêm do site do osu!</footer>
</div>
<script>
const list=document.getElementById('list'),cards=[...list.querySelectorAll('.card')],q=document.getElementById('q'),sort=document.getElementById('sort'),cnt=document.getElementById('count'),none=document.getElementById('none');
let mod='*';
function apply(){{
 const t=q.value.trim().toLowerCase(),k=sort.value,asc=(k==='i'||k==='stars'||k==='len');
 const v=c=>parseFloat(c.dataset[k]);
 cards.sort((a,b)=>asc?v(a)-v(b):v(b)-v(a)).forEach(c=>list.appendChild(c));
 let n=0;cards.forEach(c=>{{const ok=(mod==='*'||c.dataset.mods===mod)&&(!t||c.dataset.q.includes(t));c.style.display=ok?'':'none';n+=ok}});
 cnt.textContent=n+' de '+cards.length+' mapas';none.style.display=(cards.length&&!n)?'':'none';
}}
document.querySelectorAll('.fchip').forEach(b=>b.onclick=()=>{{document.querySelectorAll('.fchip').forEach(x=>x.classList.remove('on'));b.classList.add('on');mod=b.dataset.m;apply()}});
q.oninput=apply;sort.onchange=apply;apply();
</script></body></html>"""


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Acha mapas de farm no osu! standard pro seu nível.")
    ap.add_argument("-u", "--user", help="nick ou id (padrão: o do config.json)")
    ap.add_argument("--peers", type=int, default=60, help="quantos jogadores comparar (padrão 60)")
    ap.add_argument("--acc", type=float, help="acc pra calcular o pp, ex 97.5 (padrão: sua acc típica)")
    ap.add_argument("--margin", type=float, default=0.3, help="quantas estrelas acima do seu conforto aceitar (padrão 0.3)")
    ap.add_argument("--mods", help="só esses combos, ex: NM,HD,HDDT,HDHR")
    ap.add_argument("--min-count", type=int, default=2, help="mínimo de jogadores com o mapa (padrão 2)")
    ap.add_argument("--max-calc", type=int, default=400, help="máximo de candidatos pra calcular pp")
    ap.add_argument("--top", type=int, default=50, help="quantas recomendações mostrar")
    ap.add_argument("--no-open", action="store_true", help="não abrir o navegador")
    run(ap.parse_args())
