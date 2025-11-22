import streamlit as st
import plotly.graph_objects as go
import networkx as nx
import opendssdirect as dss
import pandas as pd
import os
import re
from itertools import combinations

# =========================================================
#               CONFIGURAÇÃO STREAMLIT
# =========================================================
st.set_page_config(page_title="Isolamento IEEE-123", layout="wide")

st.title("⚡ Plataforma Interativa – Isolamento Real IEEE-123")

st.sidebar.header("⚙️ Status do Modelo")

# =========================================================
#             CAMINHOS (RELATIVOS AO REPOSITÓRIO)
# =========================================================
BASE = "123Bus/"

MASTER = BASE + "IEEE123Master.dss"
COORDS = BASE + "BusCoords.dat"
LOADS  = BASE + "IEEE123Loads.dss"

# =========================================================
#                FUNÇÕES DE SUPORTE
# =========================================================
def normalize(bus):
    return bus.split(".")[0]

def load_coordinates():
    coords = {}
    try:
        with open(COORDS, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                p = line.split()
                if len(p) >= 3:
                    try:
                        coords[p[0]] = (float(p[1]), float(p[2]))
                    except:
                        pass
    except FileNotFoundError:
        st.error("❌ Arquivo BusCoords.dat não encontrado.")
    return coords

def load_loads():
    loads = {}
    try:
        with open(LOADS, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "new load" in line.lower():
                    m_bus = re.search(r"bus1=([\w\.]+)", line.lower())
                    m_kw  = re.search(r"kw=([\d\.]+)", line.lower())
                    if m_bus and m_kw:
                        bus = normalize(m_bus.group(1))
                        kw = float(m_kw.group(1))
                        loads[bus] = loads.get(bus, 0) + kw
    except FileNotFoundError:
        st.error("❌ Arquivo Loads não encontrado.")
    return loads

def build_graph():
    G = nx.Graph()
    for name in dss.Lines.AllNames():
        dss.Lines.Name(name)
        b1 = normalize(dss.Lines.Bus1())
        b2 = normalize(dss.Lines.Bus2())
        is_sw = name.lower().startswith("sw")

        # Estado nominal
        closed = True
        if is_sw and name.lower() in ("sw7", "sw8"):
            closed = False

        if closed:
            G.add_edge(b1, b2, element=name, is_switch=is_sw)
    return G

def solve_isolated_buses():
    dss.Solution.Solve()
    isolated = []
    for b in dss.Circuit.AllBusNames():
        dss.Circuit.SetActiveBus(b)
        mags = dss.Bus.VMagAngle()[0::2]
        if max(mags) < 1.0:
            isolated.append(normalize(b))
    return isolated

def open_switch(sw):
    dss.Circuit.SetActiveElement(f"Line.{sw}")
    ncond = dss.CktElement.NumConductors()
    for t in (1, 2):
        for c in range(1, ncond+1):
            dss.CktElement.Open(t, c)

def close_switch(sw):
    dss.Circuit.SetActiveElement(f"Line.{sw}")
    ncond = dss.CktElement.NumConductors()
    for t in (1, 2):
        for c in range(1, ncond+1):
            dss.CktElement.Close(t, c)

def simulate_nf(sw, target_u, target_v, loads):
    dss.Text.Command(f"compile {MASTER}")

    # Fecha todas NF
    for s in ["sw1","sw2","sw3","sw4","sw5","sw6"]:
        close_switch(s)

    # Mantém NA abertas
    open_switch("sw7")
    open_switch("sw8")

    # Abre NF de teste
    open_switch(sw)

    dss.Solution.Solve()

    isol = solve_isolated_buses()
    vao_ok = (target_u in isol) and (target_v in isol)
    kw = sum(loads.get(b, 0) for b in isol)

    return vao_ok, kw, isol


# =========================================================
#               CARREGAR OPEN-DSS
# =========================================================
try:
    dss.Text.Command(f"compile {MASTER}")
    dss.Solution.Solve()
    st.sidebar.success("Modelo IEEE-123 carregado ✔")
except Exception as e:
    st.sidebar.error(f"Erro ao carregar modelo: {e}")

coords = load_coordinates()
loads = load_loads()

# =========================================================
#           GERAÇÃO DO MAPA INTERATIVO
# =========================================================
st.subheader("📡 Mapa Interativo da Rede")

G = build_graph()

edge_x, edge_y = [], []
for u, v in G.edges():
    if u in coords and v in coords:
        x0, y0 = coords[u]
        x1, y1 = coords[v]
        edge_x += [x0, x1, None]
        edge_y += [y0, y1, None]

node_x = [coords[n][0] for n in G.nodes() if n in coords]
node_y = [coords[n][1] for n in G.nodes() if n in coords]
node_text = [n for n in G.nodes() if n in coords]

fig = go.Figure()

fig.add_trace(go.Scatter(x=edge_x, y=edge_y, mode="lines",
                         line=dict(color="#999", width=1)))

fig.add_trace(go.Scatter(x=node_x, y=node_y,
                         mode="markers+text",
                         text=node_text,
                         textposition="top center",
                         marker=dict(size=6, color="#1f77b4")))

fig.update_layout(height=600, clickmode="event+select")

st.plotly_chart(fig, use_container_width=True)

# =========================================================
#                 ENTRADA DO USUÁRIO
# =========================================================
st.sidebar.markdown("### 🔧 Selecione o vão")

u = st.sidebar.text_input("Barra U")
v = st.sidebar.text_input("Barra V")

if st.sidebar.button("📌 Confirmar vão"):
    st.session_state.vao = (u, v)

if "vao" in st.session_state:
    u, v = st.session_state.vao

    st.subheader(f"🔍 Analisando vão {u} — {v}")

    NF = ["sw1","sw2","sw3","sw4","sw5","sw6"]

    resultados = []
    for nf in NF:
        ok, kw, isol = simulate_nf(nf, u, v, loads)
        resultados.append((nf, ok, kw, len(isol)))

    df = pd.DataFrame([{
        "NF": nf, "Isolou": ok, "kW": kw, "Barras isoladas": n
    } for nf, ok, kw, n in resultados])

    st.write(df)

    validos = df[df["Isolou"] == True]

    if len(validos) == 0:
        st.error("Nenhuma NF isolou totalmente o vão.")
    else:
        melhor = validos.sort_values(["kW", "Barras isoladas"]).iloc[0]
        st.success(f"### ✅ Melhor NF: **{melhor['NF']}**\n"
                   f"⚡ Carga interrompida: **{melhor['kW']} kW**\n"
                   f"🔻 Barras isoladas: **{melhor['Barras isoladas']}**")
