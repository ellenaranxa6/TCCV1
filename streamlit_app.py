import streamlit as st
import plotly.graph_objects as go
import networkx as nx
import opendssdirect as dss
import pandas as pd
import re
from pathlib import Path
from itertools import combinations

# =========================================================
#   TENTATIVA DE IMPORTAR COMPONENTE DE CLIQUE NO PLOTLY
# =========================================================
try:
    from streamlit_plotly_events import plotly_events
    HAS_PLOTLY_EVENTS = True
except Exception:
    HAS_PLOTLY_EVENTS = False

# =========================================================
#                  CONFIG STREAMLIT
# =========================================================
st.set_page_config(page_title="Isolamento IEEE-123", layout="wide")
st.title("⚡ Plataforma Interativa – Isolamento Real IEEE-123")

st.sidebar.header("⚙️ Status do Modelo")

# =========================================================
#             CAMINHOS (BASEADOS NESTE ARQUIVO)
# =========================================================
ROOT = Path(__file__).parent
BUSDIR = ROOT / "123Bus"

MASTER = BUSDIR / "IEEE123Master.dss"
COORDS = BUSDIR / "BusCoords.dat"
LOADS  = BUSDIR / "IEEE123Loads.dss"

# Debug opcional
st.sidebar.write("📂 Arquivos detectados:")
st.sidebar.write(f"MASTER: {MASTER.exists()}")
st.sidebar.write(f"COORDS: {COORDS.exists()}")
st.sidebar.write(f"LOADS:  {LOADS.exists()}")

# =========================================================
#                FUNÇÕES DE SUPORTE
# =========================================================
def normalize(bus: str) -> str:
    return bus.split(".")[0] if bus else bus


def load_coordinates() -> dict:
    """Lê BusCoords.dat e devolve dict {barra: (x, y)}."""
    coords = {}
    try:
        with open(str(COORDS), "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                p = line.split()
                if len(p) >= 3:
                    try:
                        coords[p[0]] = (float(p[1]), float(p[2]))
                    except ValueError:
                        pass
    except FileNotFoundError:
        st.error("❌ Arquivo BusCoords.dat não encontrado.")
    return coords


def load_loads() -> dict:
    """Lê IEEE123Loads.dss e devolve dict {barra: kW_total}."""
    loads = {}
    try:
        with open(str(LOADS), "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "new load" not in line.lower():
                    continue
                m_bus = re.search(r"bus1\s*=\s*([\w\.]+)", line, re.IGNORECASE)
                m_kw  = re.search(r"kw\s*=\s*([\d\.]+)", line, re.IGNORECASE)
                if m_bus and m_kw:
                    bus = normalize(m_bus.group(1))
                    kw  = float(m_kw.group(1))
                    loads[bus] = loads.get(bus, 0.0) + kw
    except FileNotFoundError:
        st.error("❌ Arquivo de cargas (IEEE123Loads.dss) não encontrado.")
    return loads


def reset_and_solve():
    """Recompila o modelo no estado nominal e resolve fluxo."""
    dss.Text.Command(f'compile "{MASTER}"')
    # modo padrão do IEEE-123
    dss.Solution.Solve()


def build_graph():
    """
    Monta grafo NetworkX com as linhas fechadas.
    Retorna:
      G: grafo
      switch_map: { 'sw1': (bus1, bus2), ... }
    """
    G = nx.Graph()
    switch_map = {}

    for name in dss.Lines.AllNames():
        dss.Lines.Name(name)
        b1 = normalize(dss.Lines.Bus1())
        b2 = normalize(dss.Lines.Bus2())
        is_sw = name.lower().startswith("sw")

        # Estado nominal: NF fechadas, NA (sw7, sw8) abertas
        closed = True
        if is_sw and name.lower() in ("sw7", "sw8"):
            closed = False

        if closed:
            G.add_edge(b1, b2, element=name, is_switch=is_sw)

        if is_sw:
            switch_map[name.lower()] = (b1, b2)

    return G, switch_map


def solve_isolated_buses() -> list:
    """
    Resolve fluxo e devolve lista de barras isoladas
    (tensão < 1 V em todas as fases).
    """
    dss.Solution.Solve()
    isolated = []
    for b in dss.Circuit.AllBusNames():
        dss.Circuit.SetActiveBus(b)
        mags = dss.Bus.VMagAngle()[0::2]
        if not mags:
            continue
        if max(mags) < 1.0:
            isolated.append(normalize(b))
    return isolated


def open_switch(sw: str):
    dss.Circuit.SetActiveElement(f"Line.{sw}")
    ncond = dss.CktElement.NumConductors()
    for t in (1, 2):
        for c in range(1, ncond + 1):
            dss.CktElement.Open(t, c)


def close_switch(sw: str):
    dss.Circuit.SetActiveElement(f"Line.{sw}")
    ncond = dss.CktElement.NumConductors()
    for t in (1, 2):
        for c in range(1, ncond + 1):
            dss.CktElement.Close(t, c)


def simulate_nf(nf: str, target_u: str, target_v: str, loads: dict):
    """
    Abre apenas a NF indicada, resolve fluxo e calcula:
      - se o vão (U,V) ficou isolado,
      - kW interrompidos,
      - lista de barras isoladas.
    """
    reset_and_solve()

    # Fecha todas as NF
    for s in ["sw1", "sw2", "sw3", "sw4", "sw5", "sw6"]:
        close_switch(s)

    # Mantém NA abertas
    open_switch("sw7")
    open_switch("sw8")

    # Abre NF de teste
    open_switch(nf)

    dss.Solution.Solve()
    isol = solve_isolated_buses()

    vao_ok = (target_u in isol) and (target_v in isol)
    kw = sum(loads.get(b, 0.0) for b in isol)

    return vao_ok, kw, isol


def best_nf_for_span(u: str, v: str, loads: dict):
    """Varre as NFs e escolhe a que isola o vão com menor kW."""
    NF = ["sw1", "sw2", "sw3", "sw4", "sw5", "sw6"]
    results = []

    for nf in NF:
        ok, kw, isol = simulate_nf(nf, u, v, loads)
        results.append((nf, ok, kw, len(isol), isol))

    df = pd.DataFrame(
        [
            {"NF": nf, "Isolou": ok, "kW": kw, "Barras isoladas": n}
            for nf, ok, kw, n, _ in results
        ]
    )

    validos = [r for r in results if r[1]]
    if not validos:
        return df, None, None

    # menor kW, depois menos barras isoladas
    validos.sort(key=lambda t: (t[2], t[3]))
    melhor = validos[0]  # (nf, ok, kw, n, isol)
    best_nf, _, best_kw, _, best_isol = melhor

    return df, best_nf, (best_kw, best_isol)


def make_base_figure(G, coords, highlight_nf=None, switch_map=None,
                     isolated=None, span=None):
    """
    Cria figura Plotly com:
      - linhas da rede,
      - nós,
      - opcionalmente NF em destaque,
      - opcionalmente barras isoladas / vão.
    """
    isolated = set(isolated or [])
    span = span or (None, None)
    u_span, v_span = span

    edge_x, edge_y = [], []
    for u, v in G.edges():
        if u in coords and v in coords:
            x0, y0 = coords[u]
            x1, y1 = coords[v]
            edge_x += [x0, x1, None]
            edge_y += [y0, y1, None]

    node_x, node_y, node_text, node_color = [], [], [], []

    for n in G.nodes():
        if n not in coords:
            continue
        x, y = coords[n]
        node_x.append(x)
        node_y.append(y)
        node_text.append(n)

        if n in isolated:
            node_color.append("#d62728")  # vermelho
        elif n == u_span or n == v_span:
            node_color.append("#FFA500")  # laranja (vão)
        else:
            node_color.append("#1f77b4")  # azul padrão

    fig = go.Figure()

    # Linhas base
    fig.add_trace(
        go.Scatter(
            x=edge_x,
            y=edge_y,
            mode="lines",
            line=dict(color="#BBBBBB", width=1),
            hoverinfo="none",
            name="Linhas ativas",
        )
    )

    # NF destacada (se houver)
    if highlight_nf and switch_map:
        sw_name = highlight_nf.lower()
        if sw_name in switch_map:
            bu, bv = switch_map[sw_name]
            if bu in coords and bv in coords:
                x0, y0 = coords[bu]
                x1, y1 = coords[bv]
                fig.add_trace(
                    go.Scatter(
                        x=[x0, x1],
                        y=[y0, y1],
                        mode="lines",
                        line=dict(color="#FF4500", width=4, dash="dash"),
                        name=f"NF {highlight_nf.upper()} (aberta)",
                    )
                )

    # Nós
    fig.add_trace(
        go.Scatter(
            x=node_x,
            y=node_y,
            mode="markers+text",
            text=node_text,
            textposition="top center",
            marker=dict(size=7, color=node_color, line=dict(width=0.5, color="#333")),
            customdata=node_text,  # usado pelo clique
            hovertemplate="<b>Barra:</b> %{text}<extra></extra>",
            name="Barras",
        )
    )

    fig.update_layout(
        height=620,
        clickmode="event+select",
        showlegend=True,
        margin=dict(l=10, r=10, t=40, b=10),
    )

    return fig


# =========================================================
#                CARREGAR OPEN-DSS
# =========================================================
try:
    reset_and_solve()
    st.sidebar.success("Modelo IEEE-123 carregado ✔")
except Exception as e:
    st.sidebar.error(f"Erro ao carregar modelo: {e}")

coords = load_coordinates()
loads = load_loads()

# =========================================================
#           MANTER ESTADO DE SELEÇÃO DE BARRAS
# =========================================================
if "selected_buses" not in st.session_state:
    st.session_state.selected_buses = []

if "vao" not in st.session_state:
    st.session_state.vao = None

# =========================================================
#           GERAÇÃO DO MAPA INTERATIVO
# =========================================================
st.subheader("📡 Mapa Interativo da Rede")

G, switch_map = build_graph()

base_fig = make_base_figure(G, coords)

if HAS_PLOTLY_EVENTS:
    st.markdown("➡️ **Clique em duas barras** no grafo para definir o vão U–V.")
    click_result = plotly_events(
        base_fig,
        click_event=True,
        hover_event=False,
        key="graph",
    )

    if click_result:
        # Pega o nome da barra (customdata)
        bus_clicked = click_result[0].get("customdata")
        if bus_clicked:
            if bus_clicked not in st.session_state.selected_buses:
                st.session_state.selected_buses.append(bus_clicked)
            # mantém só as duas últimas
            st.session_state.selected_buses = st.session_state.selected_buses[-2:]
else:
    st.plotly_chart(base_fig, use_container_width=True)
    st.info(
        "Para selecionar o vão clicando no grafo, "
        "adicione `streamlit-plotly-events` ao requirements.txt. "
        "Por enquanto, use os campos da barra lateral."
    )

# =========================================================
#                 ENTRADA DO USUÁRIO
# =========================================================
st.sidebar.markdown("### 🔧 Selecione o vão")

# Pré-preenche com cliques (se houver)
bus_u_pref = st.session_state.selected_buses[0] if len(st.session_state.selected_buses) >= 1 else ""
bus_v_pref = st.session_state.selected_buses[1] if len(st.session_state.selected_buses) >= 2 else ""

u = st.sidebar.text_input("Barra U", value=bus_u_pref)
v = st.sidebar.text_input("Barra V", value=bus_v_pref)

if st.sidebar.button("📌 Confirmar vão"):
    if u and v:
        st.session_state.vao = (u.strip(), v.strip())
        st.sidebar.success(f"Vão selecionado: {u} — {v}")
    else:
        st.sidebar.error("Informe as duas barras U e V.")

# =========================================================
#       APÓS DEFINIR O VÃO → SIMULAR ISOLAMENTO
# =========================================================
if st.session_state.vao:
    u, v = st.session_state.vao
    st.subheader(f"🔍 Analisando vão **{u} — {v}**")

    if not loads:
        st.error("Não há informações de carga para calcular kW interrompidos.")
    else:
        df_results, best_nf, best_data = best_nf_for_span(u, v, loads)
        st.markdown("### 🧪 Resultado das NFs (abertura individual)")
        st.dataframe(df_results, use_container_width=True)

        if best_nf is None:
            st.error("Nenhuma NF conseguiu isolar totalmente o vão informado.")
        else:
            best_kw, best_isol = best_data
            st.success(
                f"### ✅ Chave de manobra ótima: **{best_nf.upper()}**  \n"
                f"⚡ Potência interrompida ≈ **{best_kw:.1f} kW**  \n"
                f"🔻 Barras isoladas: **{len(best_isol)}**"
            )

            # -------------------------------
            #   PLOT FINAL COM NF DESTACADA
            # -------------------------------
            result_fig = make_base_figure(
                G,
                coords,
                highlight_nf=best_nf,
                switch_map=switch_map,
                isolated=best_isol,
                span=(u, v),
            )
            st.markdown("### 🗺️ Topologia após abertura da NF ótima")
            st.plotly_chart(result_fig, use_container_width=True)

            # -------------------------------
            #   TIMELINE TEXTUAL DA MANOBRA
            # -------------------------------
            st.markdown("### ⏱️ Timeline da Manobra")

            steps = [
                {
                    "Passo": 1,
                    "Ação": f"Confirmar vão de manutenção entre {u} e {v}",
                    "Elemento": "Trecho de linha",
                    "Detalhe": f"Definição do vão alvo para isolamento.",
                },
                {
                    "Passo": 2,
                    "Ação": f"Abrir NF {best_nf.upper()}",
                    "Elemento": f"Chave {best_nf.upper()}",
                    "Detalhe": "Interrompe o fluxo entre a fonte e o vão selecionado.",
                },
                {
                    "Passo": 3,
                    "Ação": "Verificar barras isoladas e cargas interrompidas",
                    "Elemento": "Rede de distribuição",
                    "Detalhe": (
                        f"{len(best_isol)} barras desenergizadas, "
                        f"≈ {best_kw:.1f} kW interrompidos."
                    ),
                },
                {
                    "Passo": 4,
                    "Ação": "Executar recomposição (se aplicável)",
                    "Elemento": "NAs / chaves de interligação",
                    "Detalhe": "Avaliar possíveis fechamentos de NAs para recuperar carga.",
                },
            ]
            df_steps = pd.DataFrame(steps)
            st.table(df_steps)
else:
    st.info("Defina o vão (U e V) clicando no grafo ou preenchendo as barras na lateral.")
