"""osu! Farm Finder — interface (vira o .exe)."""
import ctypes
import os
import json
import queue
import re
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk
from types import SimpleNamespace

import farm_finder as ff

C = {
    "bg": "#151517", "side": "#1b1b1e", "panel": "#222226", "row": "#18181b", "line": "#2c2c31", "line2": "#3a3a41",
    "fg": "#e6e6e9", "dim": "#8e8e98", "faint": "#5f5f68",
    "acc": "#e0609a", "acc_hi": "#ec78ab", "acc_bg": "#33202a", "ok": "#7cc48c", "err": "#e27474",
}
UI = "Segoe UI"
MOD_CHOICES = ["NM", "HD", "HR", "DT", "HDHR", "HDDT"]
q = queue.Queue()

# texto que aparece no log -> (% da barra, mensagem de status)
STEPS = [
    ("Jogador:", 5, "Perfil carregado"),
    ("Calibrando", 10, "Calibrando o nível do jogador"),
    ("Acc típica", 18, "Procurando jogadores parecidos"),
    ("Lendo o top", 22, "Lendo o top 100 dos jogadores"),
    ("combinações", 60, "Calculando o pp dos mapas"),
    ("recomendações.", 97, "Montando o relatório"),
]


def fmt_int(n):
    return f"{n:,}".replace(",", ".") if n else "—"


def fmt_dec(x, d=2):
    return f"{x:.{d}f}".replace(".", ",")


class Writer:
    def write(self, s):
        if s:
            q.put(s)

    def flush(self):
        pass


def worker(args):
    sys.stdout = Writer()
    try:
        meta = ff.run(args)
        q.put(("ok", meta))
    except SystemExit as e:
        q.put(("err", str(e) if e.code not in (None, 0) else "Busca interrompida."))
    except Exception as e:
        q.put(("err", f"Erro: {e}"))


# ------------------------------------------------------------------ widgets
class Btn(tk.Label):
    def __init__(self, master, text, cmd, primary=False, small=False):
        self.primary, self.cmd, self.enabled = primary, cmd, True
        if primary:
            self.idle, self.hover, fg, border = C["acc"], C["acc_hi"], "#1b1016", C["acc"]
        else:
            self.idle, self.hover, fg, border = C["panel"], C["line"], C["fg"], C["line2"]
        self.fg = fg
        super().__init__(master, text=text, bg=self.idle, fg=fg, cursor="hand2",
                         font=(UI, 9 if small else 10, "bold" if primary else "normal"),
                         padx=10 if small else 14, pady=4 if small else 7,
                         highlightthickness=1, highlightbackground=border, highlightcolor=border)
        self.bind("<Enter>", lambda e: self.enabled and self.configure(bg=self.hover))
        self.bind("<Leave>", lambda e: self.enabled and self.configure(bg=self.idle))
        self.bind("<Button-1>", lambda e: self.enabled and self.cmd())

    def set(self, text=None, enabled=None):
        if text is not None:
            self.configure(text=text)
        if enabled is not None:
            self.enabled = enabled
            self.configure(bg=self.idle if enabled else C["panel"], fg=self.fg if enabled else C["faint"],
                           cursor="hand2" if enabled else "arrow",
                           highlightbackground=(C["acc"] if self.primary else C["line2"]) if enabled else C["line"])


class Toggle(tk.Label):
    def __init__(self, master, text):
        super().__init__(master, text=text, bg=C["panel"], fg=C["dim"], font=(UI, 9, "bold"), pady=5,
                         cursor="hand2", highlightthickness=1, highlightbackground=C["line2"])
        self.on = False
        self.bind("<Button-1>", self.toggle)

    def toggle(self, _=None):
        self.on = not self.on
        self.configure(bg=C["acc_bg"] if self.on else C["panel"], fg=C["acc_hi"] if self.on else C["dim"],
                       highlightbackground=C["acc"] if self.on else C["line2"])


def field(parent, label, default="", hint=None):
    f = tk.Frame(parent, bg=C["side"])
    tk.Label(f, text=label, bg=C["side"], fg=C["dim"], font=(UI, 9)).pack(anchor="w")
    e = tk.Entry(f, bg=C["panel"], fg=C["fg"], insertbackground=C["fg"], relief="flat", font=(UI, 10),
                 highlightthickness=1, highlightbackground=C["line2"], highlightcolor=C["acc"])
    e.pack(fill="x", pady=(3, 0), ipady=5, ipadx=4)
    e.insert(0, default)
    if hint:
        tk.Label(f, text=hint, bg=C["side"], fg=C["faint"], font=(UI, 8)).pack(anchor="w", pady=(3, 0))
    return f, e


def dark_titlebar(root):
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        on = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (Win10 20H1+ / antigo)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on), 4) == 0:
                break
    except Exception:
        pass


# ------------------------------------------------------------------ app
def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # texto nítido em tela com escala
    except Exception:
        pass
    root = tk.Tk()
    sc = root.winfo_fpixels("1i") / 96
    px = lambda v: int(v * sc)
    root.title("osu! Farm Finder")
    root.configure(bg=C["bg"])
    root.geometry(f"{px(1180)}x{px(680)}")
    root.minsize(px(900), px(540))
    try:
        root.iconbitmap(str(ff.Path(getattr(sys, "_MEIPASS", ff.Path(__file__).resolve().parent)) / "icon.ico"))
    except Exception:
        pass

    st = ttk.Style()
    st.theme_use("clam")
    st.configure("Thin.Horizontal.TProgressbar", troughcolor=C["line"], background=C["acc"], bordercolor=C["line"],
                 lightcolor=C["acc"], darkcolor=C["acc"], thickness=px(3))
    st.layout("Thin.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
        ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
    st.configure("Thin.Vertical.TScrollbar", troughcolor=C["bg"], background=C["line2"], bordercolor=C["bg"],
                 lightcolor=C["line2"], darkcolor=C["line2"], gripcount=0, width=px(10))
    st.map("Thin.Vertical.TScrollbar", background=[("active", C["faint"])])
    st.configure("R.Treeview", background=C["bg"], fieldbackground=C["bg"], foreground=C["fg"], borderwidth=0,
                 rowheight=px(30), font=(UI, 10))
    st.map("R.Treeview", background=[("selected", C["acc_bg"])], foreground=[("selected", C["fg"])])
    st.layout("R.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    st.configure("R.Treeview.Heading", background=C["side"], foreground=C["dim"], relief="flat", borderwidth=0,
                 font=(UI, 9, "bold"), padding=(px(8), px(7)))
    st.map("R.Treeview.Heading", background=[("active", C["panel"])], foreground=[("active", C["fg"])])

    root.columnconfigure(1, weight=1)
    root.rowconfigure(0, weight=1)

    # ================= barra lateral
    side = tk.Frame(root, bg=C["side"], width=px(270))
    side.grid(row=0, column=0, sticky="ns")
    side.grid_propagate(False)
    side.pack_propagate(False)
    tk.Frame(root, bg=C["line"], width=1).grid(row=0, column=0, sticky="nse")
    pad = dict(padx=px(20))

    tk.Label(side, text="Farm Finder", bg=C["side"], fg=C["fg"], font=(UI, 15, "bold")).pack(anchor="w", pady=(px(20), 0), **pad)
    tk.Label(side, text="Mapas de farm pro seu nível no osu! standard", bg=C["side"], fg=C["dim"],
             font=(UI, 9), wraplength=px(230), justify="left").pack(anchor="w", **pad)
    tk.Frame(side, bg=C["line"], height=1).pack(fill="x", pady=px(16), **pad)

    f, nick = field(side, "Jogador")
    f.pack(fill="x", **pad)
    try:
        nick.insert(0, json.loads(ff.CONFIG.read_text(encoding="utf-8")).get("username") or "")
    except Exception:
        pass

    row = tk.Frame(side, bg=C["side"])
    row.pack(fill="x", pady=(px(14), 0), **pad)
    row.columnconfigure((0, 1), weight=1, uniform="a")
    f, acc = field(row, "Accuracy (%)")
    f.grid(row=0, column=0, sticky="ew", padx=(0, px(6)))
    f, margin = field(row, "Estrelas a mais", "0,3")
    f.grid(row=0, column=1, sticky="ew", padx=(px(6), 0))
    tk.Label(side, text="Accuracy em branco usa a acc típica do jogador.", bg=C["side"], fg=C["faint"],
             font=(UI, 8), wraplength=px(230), justify="left").pack(anchor="w", pady=(px(4), 0), **pad)

    tk.Label(side, text="Mods", bg=C["side"], fg=C["dim"], font=(UI, 9)).pack(anchor="w", pady=(px(14), px(3)), **pad)
    grid = tk.Frame(side, bg=C["side"])
    grid.pack(fill="x", **pad)
    grid.columnconfigure((0, 1, 2), weight=1, uniform="m")
    toggles = []
    for i, m in enumerate(MOD_CHOICES):
        t = Toggle(grid, m)
        t.grid(row=i // 3, column=i % 3, sticky="ew", padx=(0 if i % 3 == 0 else px(4), 0), pady=(0, px(4)))
        toggles.append(t)
    tk.Label(side, text="Nenhum marcado: todos os mods.", bg=C["side"], fg=C["faint"], font=(UI, 8)).pack(anchor="w", **pad)

    btn = Btn(side, "Buscar mapas", lambda: start(), primary=True)
    btn.pack(fill="x", pady=(px(18), 0), **pad)

    bottom = tk.Frame(side, bg=C["side"])
    bottom.pack(side="bottom", fill="x", pady=px(18), **pad)
    status = tk.Label(bottom, text="Pronto pra buscar.", bg=C["side"], fg=C["dim"], font=(UI, 9),
                      wraplength=px(230), justify="left", anchor="w")
    status.pack(fill="x")
    track = tk.Frame(bottom, bg=C["line"], height=px(3))
    track.pack(fill="x", pady=(px(8), px(14)))
    fill = tk.Frame(track, bg=C["acc"], height=px(3))

    class Prog(dict):
        def __setitem__(self, k, v):
            super().__setitem__(k, v)
            fill.place(x=0, y=0, relheight=1, relwidth=max(0, min(1, v / 100))) if v else fill.place_forget()
    prog = Prog()
    Btn(bottom, "Abrir site com perfil e mapas", lambda: open_report()).pack(fill="x")

    # ================= área principal
    main_ = tk.Frame(root, bg=C["bg"])
    main_.grid(row=0, column=1, sticky="nsew")
    main_.grid_propagate(False)
    main_.columnconfigure(0, weight=1)
    main_.rowconfigure(1, weight=1)

    top = tk.Frame(main_, bg=C["bg"])
    top.grid(row=0, column=0, sticky="ew", padx=px(24), pady=(px(18), px(12)))
    top.columnconfigure(0, weight=1)
    title = tk.Label(top, text="Resultados", bg=C["bg"], fg=C["fg"], font=(UI, 13, "bold"), anchor="w")
    title.grid(row=0, column=0, sticky="w")
    summary = tk.Label(top, text="Nenhuma busca feita ainda.", bg=C["bg"], fg=C["dim"], font=(UI, 9), anchor="w")
    summary.grid(row=1, column=0, sticky="w")

    tabs = tk.Frame(top, bg=C["bg"])
    tabs.grid(row=0, column=1, rowspan=2, sticky="e")
    site_btn = Btn(top, "Abrir site com perfil e mapas", lambda: open_report(), primary=True, small=True)
    site_btn.grid(row=0, column=2, rowspan=2, sticky="e", padx=(px(20), 0))
    tab_btns = {}

    content = tk.Frame(main_, bg=C["bg"])
    content.grid(row=1, column=0, sticky="nsew", padx=px(24))
    content.rowconfigure(0, weight=1)
    content.columnconfigure(0, weight=1)

    # --- tabela
    tbl = tk.Frame(content, bg=C["line"], padx=1, pady=1)
    tbl.grid(row=0, column=0, sticky="nsew")
    inner = tk.Frame(tbl, bg=C["bg"])
    inner.pack(fill="both", expand=True)
    cols = [("n", "#", 40, "e"), ("mapa", "Mapa", 160, "w"), ("mods", "Mods", 64, "center"),
            ("stars", "★", 56, "e"), ("pp", "pp", 56, "e"), ("acc", "Acc necessária", 104, "e"),
            ("gain", "Ganho", 70, "e"), ("farm", "Farm", 60, "e")]
    tree = ttk.Treeview(inner, style="R.Treeview", columns=[c[0] for c in cols], show="headings", selectmode="browse")
    for cid, label, w, anchor in cols:
        tree.heading(cid, text=label, anchor=anchor, command=lambda c=cid: sort_by(c))
        tree.column(cid, width=px(w), minwidth=px(36), anchor=anchor, stretch=(cid == "mapa"))
    tree.tag_configure("odd", background=C["row"])
    vs = ttk.Scrollbar(inner, orient="vertical", command=tree.yview, style="Thin.Vertical.TScrollbar")
    tree.configure(yscrollcommand=vs.set)
    vs.pack(side="right", fill="y")
    tree.pack(fill="both", expand=True)
    empty = tk.Label(inner, text="Digite o nick na esquerda e clique em Buscar mapas.\nA primeira busca demora uns 3 a 5 minutos.",
                     bg=C["bg"], fg=C["faint"], font=(UI, 10), justify="center")

    # --- log
    logf = tk.Frame(content, bg=C["line"], padx=1, pady=1)
    logf.grid(row=0, column=0, sticky="nsew")
    log = tk.Text(logf, bg=C["bg"], fg="#b9b9c2", relief="flat", font=("Consolas", 9), wrap="none", state="disabled",
                  padx=px(12), pady=px(10), highlightthickness=0, selectbackground=C["acc_bg"])
    log.pack(fill="both", expand=True)

    # --- ações da linha selecionada
    actions = tk.Frame(main_, bg=C["bg"])
    actions.grid(row=2, column=0, sticky="ew", padx=px(24), pady=px(14))
    hint = tk.Label(actions, text="Clique no cabeçalho pra ordenar · duplo clique abre o mapa", bg=C["bg"],
                    fg=C["faint"], font=(UI, 9))
    hint.pack(side="left")
    act_btns = [Btn(actions, "Baixar", lambda: act("dl"), small=True),
                Btn(actions, "osu!direct", lambda: act("direct"), small=True),
                Btn(actions, "Ver mapa no osu!", lambda: act("site"), small=True)]
    for b in act_btns:
        b.pack(side="right", padx=(px(6), 0))

    data = {"rows": [], "sort": None, "desc": False}
    state = {"pct": 0, "running": False, "sub": None}

    def show_tab(name):
        (tbl if name == "Mapas" else logf).tkraise()
        for n, (lbl, bar) in tab_btns.items():
            lbl.configure(fg=C["fg"] if n == name else C["dim"])
            bar.configure(bg=C["acc"] if n == name else C["bg"])
        for b in act_btns:
            b.pack_forget() if name != "Mapas" else None
        if name == "Mapas":
            for b in act_btns:
                b.pack(side="right", padx=(px(6), 0))
            hint.configure(text="Clique no cabeçalho pra ordenar · duplo clique abre o mapa")
        else:
            hint.configure(text="Saída detalhada da última busca")

    for name in ("Mapas", "Log"):
        f = tk.Frame(tabs, bg=C["bg"])
        f.pack(side="left", padx=(px(16), 0))
        lbl = tk.Label(f, text=name, bg=C["bg"], fg=C["dim"], font=(UI, 10, "bold"), cursor="hand2")
        lbl.pack()
        bar = tk.Frame(f, bg=C["bg"], height=2)
        bar.pack(fill="x", pady=(px(3), 0))
        lbl.bind("<Button-1>", lambda e, n=name: show_tab(n))
        tab_btns[name] = (lbl, bar)

    def render_rows():
        tree.delete(*tree.get_children())
        for k, (i, r) in enumerate(data["rows"]):
            mapa = f"{r.get('artist') or ''} - {r.get('title') or ''}  [{r.get('version') or ''}]"
            tree.insert("", "end", iid=str(i), tags=("odd",) if k % 2 else (), values=(
                i + 1, mapa, r["mods"], fmt_dec(r["stars_mod"]), f"{r['pp_me']:.0f}",
                fmt_dec(r.get("acc_me", 0) * 100) + "%", "+" + fmt_dec(r["gain"], 1), f"{r['farm']:.0f}%"))
        if data["rows"]:
            empty.place_forget()
        else:
            empty.place(relx=.5, rely=.45, anchor="center")
        for b in act_btns:
            b.set(enabled=bool(data["rows"]))

    keys = {"n": lambda x: x[0], "mapa": lambda x: (x[1].get("title") or "").lower(), "mods": lambda x: x[1]["mods"],
            "stars": lambda x: x[1]["stars_mod"], "pp": lambda x: x[1]["pp_me"], "acc": lambda x: x[1].get("acc_me", 0),
            "gain": lambda x: x[1]["gain"], "farm": lambda x: x[1]["farm"]}

    def sort_by(col):
        desc = not data["desc"] if data["sort"] == col else col in ("pp", "gain", "farm")
        data.update(sort=col, desc=desc)
        data["rows"].sort(key=keys[col], reverse=desc)
        for cid, label, _, _ in cols:
            tree.heading(cid, text=label + ((" ↓" if desc else " ↑") if cid == col else ""))
        render_rows()

    def load_results():
        try:
            rows = json.loads((ff.BASE / "resultado.json").read_text(encoding="utf-8"))
        except Exception:
            rows = []
        try:
            meta = json.loads((ff.BASE / "resultado_meta.json").read_text(encoding="utf-8"))
        except Exception:
            meta = None
        data.update(rows=list(enumerate(rows)), sort=None, desc=False)
        for cid, label, _, _ in cols:
            tree.heading(cid, text=label)
        render_rows()
        if meta:
            title.configure(text=f"Resultados · {meta['username']}")
            summary.configure(text=f"{meta['pp']:.0f}pp  ·  #{fmt_int(meta.get('rank'))}  ·  acc típica "
                                   f"{fmt_dec(meta['acc'] * 100)}%  ·  até {fmt_dec(meta['max_stars'])}★  ·  "
                                   f"{meta['peers']} jogadores  ·  {meta['date'][:5]} {meta['date'][-5:]}")

    def selected():
        sel = tree.selection()
        return data["rows"][[i for i, _ in data["rows"]].index(int(sel[0]))][1] if sel else None

    def act(kind):
        r = selected()
        if not r:
            hint.configure(text="Primeiro clique num mapa da tabela, depois no botão.", fg=C["err"])
            root.after(3500, lambda: hint.configure(text="Clique no cabeçalho pra ordenar · duplo clique abre o mapa", fg=C["faint"]))
            return
        url = {"site": f"https://osu.ppy.sh/b/{r['bid']}", "direct": f"osu://b/{r['bid']}",
               "dl": f"https://catboy.best/d/{r['sid']}"}[kind]
        webbrowser.open(url)

    tree.bind("<Double-1>", lambda e: tree.identify_row(e.y) and act("site"))

    def open_report():
        f = ff.BASE / "resultado.html"
        if not f.exists():
            status.configure(text="O site é criado depois da primeira busca. Clique em Buscar mapas.", fg=C["err"])
            return
        try:
            os.startfile(str(f))
        except Exception:
            webbrowser.open(f.as_uri())
        status.configure(text="Site aberto no navegador.", fg=C["ok"])

    def set_pct(p):
        state["pct"] = max(state["pct"], p)
        prog["value"] = state["pct"]

    def append(s):
        log.configure(state="normal")
        for i, c in enumerate(s.replace("\r\n", "\n").split("\r")):
            if i > 0:  # \r: reescreve a linha atual
                log.delete("end-1c linestart", "end-1c")
            log.insert("end", c)
        log.see("end")
        log.configure(state="disabled")
        for key, pct, txt in STEPS:
            if key in s:
                set_pct(pct)
                status.configure(text=txt + "…", fg=C["fg"])
                state["sub"] = key
        m = re.findall(r"(\d+)/(\d+)", s)
        if m:
            i, n = map(int, m[-1])
            if n and state["sub"] == "Lendo o top":
                set_pct(22 + 38 * i / n)
                status.configure(text=f"Lendo o top 100 dos jogadores ({i}/{n})…")
            elif n and state["sub"] == "combinações":
                set_pct(60 + 37 * i / n)
                status.configure(text=f"Calculando o pp dos mapas ({i}/{n})…")
        m = re.search(r"(\d+) jogadores olhados, (\d+) no seu nível", s)
        if m:
            status.configure(text=f"Procurando jogadores parecidos ({m.group(2)} encontrados)…")

    def poll():
        try:
            while True:
                item = q.get_nowait()
                if isinstance(item, tuple):
                    kind, val = item
                    state["running"] = False
                    btn.set("Buscar mapas", True)
                    if kind == "ok":
                        set_pct(100)
                        load_results()
                        show_tab("Mapas")
                        status.configure(text=f"Concluído: {len(data['rows'])} mapas encontrados.", fg=C["ok"])
                    else:
                        prog["value"] = 0
                        status.configure(text=val, fg=C["err"])
                else:
                    append(item)
        except queue.Empty:
            pass
        root.after(100, poll)

    def start():
        if state["running"]:
            return
        n = nick.get().strip()
        if not n:
            status.configure(text="Digite o nick do jogador.", fg=C["err"])
            nick.focus_set()
            return
        try:
            a = float(acc.get().replace(",", ".").replace("%", "")) if acc.get().strip() else None
            mg = float(margin.get().replace(",", ".") or 0.3)
        except ValueError:
            status.configure(text="Accuracy e estrelas a mais precisam ser números (ex.: 97,5 e 0,3).", fg=C["err"])
            return
        if a is not None and not 50 <= a <= 100:
            status.configure(text="A accuracy precisa estar entre 50 e 100.", fg=C["err"])
            return
        try:  # lembra o nick pra próxima vez
            cfg = json.loads(ff.CONFIG.read_text(encoding="utf-8"))
            cfg["username"] = n
            ff.CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        except Exception:
            pass
        sel = [t.cget("text") for t in toggles if t.on]
        log.configure(state="normal"); log.delete("1.0", "end"); log.configure(state="disabled")
        state.update(pct=0, running=True, sub=None)
        prog["value"] = 0
        status.configure(text="Conectando na API do osu!…", fg=C["fg"])
        btn.set("Buscando…", False)
        args = SimpleNamespace(user=n, peers=60, acc=a, margin=mg, mods=",".join(sel) or None,
                               min_count=2, max_calc=400, top=50, no_open=True)
        threading.Thread(target=worker, args=(args,), daemon=True).start()

    root.bind("<Return>", lambda e: start())
    load_results()
    show_tab("Mapas")
    root.update_idletasks()
    dark_titlebar(root)
    nick.focus_set()
    if "--auto" in sys.argv:  # teste: busca sozinho com o nick passado
        nick.delete(0, "end"); nick.insert(0, sys.argv[sys.argv.index("--auto") + 1])
        root.after(800, start)
    poll()
    root.mainloop()


if __name__ == "__main__":
    main()
