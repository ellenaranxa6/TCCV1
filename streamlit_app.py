###############################################################
# STREAMLIT – ISOLAMENTO REAL – IEEE 123 BUS
# Backend + Frontend juntos (sem ngrok, sem FastAPI)
###############################################################

import streamlit as st
import plotly.graph_objects as go
import networkx as nx
import opendssdirect as dss
import pandas as pd
import numpy as np
import re
import os
from itertools import combinations

st.set_page_config(page_title="Reconfiguração IEEE-123", layout="wide")

st.title("⚡ Plataforma Interativa – Isolamento Real IEEE-123")

###############################################################
# 1) CARREGAR ARQUIVOS
###############################################################

BASE = "./123Bus/"
MASTER = BASE + "IEEE123Master.dss"
COORDS = BASE + "BusCoords.dat"
LOADS  = BASE + "IEEE123Loads.dss"

if not os.path.exists(MASTER):
    st.error("❌ Arquivo Master não encontrado.")
    st.stop()

###############################################################
# 2) FUNÇÕES AUXILIARES
###############################################################

def normalize(bus):
    return bus.split(".")[0]

def load_coordinates():
    coords = {}
    with open(COORDS, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            p = line.split()
            if len(p) >= 3:
                try:
                    coords[p[0]] = (float(p[1]), float(p[2]))
                except:
                    pass
    return coords

def load_loads():
    loads = {}
    with open(LOADS, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            s = line.lower()
            if "new load" not in s: continue
            m_bus = re.search(r"bus1=([\w\.]+)", s)
            m_kw  = re.search(r"kw=([\d\.]+)", s)
            if m_bus and m_kw:
                bus = normalize(m_bus.group(1))
                kw  = float(m_kw.group(1))
                loads[bus] = loads.get(bus,0) + kw
    return loads

def solve_and_get_isolated():
    dss.Solution.Solve()
    isol = []
    for bus in dss.Circuit.AllBusNames():
        b = normalize(bus)
        dss.Circuit.SetActiveBus(b)
        mags = dss.Bus.VMagAngle()[0::2]
        if max(mags) < 5:  # tensão ~zero
            isol.append(b)
    return isol

def open_switch(sw):
    dss.Circuit.SetActiveElement(f"Line.{sw}")
    ncond = dss.CktElement.NumConductors()
    for t in (1,2):
        for c in range(1,ncond+1):
            dss.CktElement.Open(t,c)

def close_switch(sw):
    dss.Circuit.SetActiveElement(f"Line.{sw}")
    ncond = dss.CktElement.NumConductors()
    for t in (1,2):
        for c in range(1,ncond+1):
            dss.CktElement.Close(t,c)

def simulate_single_nf(nf, target_u, target_v, loads):
    """Testa se abrir somente esta NF desenergiza o vão."""
    # Reset modelo
    dss.Text.Command(f"compile {MASTER}")
    # Fecha todas NF
    for sw in ["sw1","sw2","sw3","sw4","sw5","sw6"]:
        close_switch(sw)
    # Abre NA
    open_switch("sw7")
    open_switch("sw8")
    # Abre NF testada
    open_switch(nf)

    dss.Solution.Solve()
    isol = solve_and_get_isolated()

    vao_ok = (target_u in isol and target_v in isol)
    kW = sum(loads.get(b,0) for b in isol)

    return vao_ok, kW, isol

###############################################################
# 3) CARREGAR MODELO DSS UMA VEZ
###############################################################

try:
    dss.Text.Command(f"compile {MASTER}")
    dss.Solution.Solve()
    st.sidebar.success("Modelo IEEE-123 carregado ✓")
except:
    st.sidebar.error("Falha ao carregar modelo DSS.")
    st.stop()

coords = load_coordinates()
loads  = load_loads()

###############################################################
# 4) GERAR GRAFO IEEE-123
###############################################################

def build_graph():
    G = nx.Graph()
    for name in dss.Lines.AllNames():
        dss.Lines.Name(name)
        b1 = normalize(dss.Lines.Bus1())
        b2 = normalize(dss.Lines.Bus2())
        is_sw = name.lower().startswith("sw")

        if is_sw and name.lower() in ("sw7","sw8"):
            continue  # NA estão abertas sempre

        G.add_edge(b1, b2, element=name, is_switch=is_sw)

    return G

G = build_graph()

###############################################################
# 5) PLOT INTERATIVO COM PLOTLY
###############################################################

st.subheader("📡 Mapa Interativo – Clique em dois nós para selecionar o vão")

edge_x, edge_y = [], []
node_x, node_y, node_text = [], [], []

for u,v,data in G.edges(data=True):
    if u in coords and v in coords:
        x0,y0 = coords[u]
        x1,y1 = coords[v]
        edge_x += [x0,x1,None]
        edge_y += [y0,y1,None]

for n in G.nodes():
    if n in coords:
        x,y = coords[n]
        node_x.append(x)
        node_y.append(y)
        node_text.append(n)

fig = go.Figure()

fig.add_trace(go.Scatter(x=edge_x,y=edge_y,mode="lines",
                         line=dict(width=1,color="#999"),
                         hoverinfo="none"
))

fig.add_trace(go.Scatter(
    x=node_x, y=node_y, mode="markers+text",
    text=node_text, textposition="top center",
    marker=dict(size=8,color="#1f77b4"),
    hovertemplate="Barra %{text}<extra></extra>"
))

fig.update_layout(height=600, clickmode="event+select")
event = st.plotly_chart(fig, use_container_width=True)

###############################################################
# 6) SELEÇÃO DO VÃO
###############################################################

st.sidebar.header("🎯 Seleção do Vão")

u = st.sidebar.text_input("Barra U")
v = st.sidebar.text_input("Barra V")

if st.sidebar.button("Confirmar Vão"):
    st.session_state.vao = (u,v)

if "vao" not in st.session_state:
    st.stop()

u, v = st.session_state.vao
st.subheader(f"🔍 Vão Selecionado: **{u} — {v}**")

###############################################################
# 7) SIMULAÇÃO – DESCOBRIR QUAL NF ISOLA O VÃO
###############################################################

NF = ["sw1","sw2","sw3","sw4","sw5","sw6"]

resultados = []
for nf in NF:
    ok, kW, isol = simulate_single_nf(nf, u, v, loads)
    resultados.append((nf, ok, kW, isol))

df = pd.DataFrame([{
    "NF": nf,
    "Isolou": ok,
    "kW": kW,
    "Barras isoladas": len(isol)
} for nf, ok, kW, isol in resultados])

st.write("### 📊 Resultado das NFs:")
st.dataframe(df)

validos = df[df["Isolou"]==True]

if len(validos)==0:
    st.error("❌ Nenhuma chave NF isolou completamente o vão.")
    st.stop()

melhor = validos.sort_values(["kW","Barras isoladas"]).iloc[0]
best_nf = melhor["NF"]

st.success(f"### ✅ Melhor NF para isolamento: **{best_nf.upper()}**")
st.write(f"⚡ Carga interrompida: **{melhor['kW']} kW**")

###############################################################
# FIM
###############################################################
