import streamlit as st
import plotly.graph_objects as go
import networkx as nx
import opendssdirect as dss
import pandas as pd
import os
import re

# =========================================================
#               CONFIG STREAMLIT
# =========================================================
st.set_page_config(page_title="Isolamento IEEE-123", layout="wide")

st.title("⚡ Plataforma Interativa – Isolamento Real IEEE-123")

st.sidebar.header("⚙️ Status do Modelo")

# =========================================================
#             PATHS ABSOLUTOS (STREAMLIT CLOUD)
# =========================================================
ROOT = os.path.dirname(__file__)
BASE = os.path.join(ROOT, "123Bus")

MASTER = os.path.join(BASE, "IEEE123Master.dss")
COORDS = os.path.join(BASE, "BusCoords.dat")
LOADS  = os.path.join(BASE, "IEEE123Loads.dss")

# =========================================================
#                FUNÇÕES AUXILIARES
# =========================================================
def normalize(bus):
    return bus.split(".")[0]

def load_coordinates():
    coords = {}
    if not os.path.exists(COORDS):
        st.error("❌ Arquivo BusCoords.dat não encontrado.")
        return coords

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
    if not os.path.exists(LOADS):
        st.error("❌ Arquivo de cargas IEEE123Loads.dss não encontrado.")
        return loads

    with open(LOADS, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if "new load" in line.lower():
                m_bus = re.search(r"bus1=([\w\.]+)", line.lower())
                m_kw  = re.search(r"kw=([\d\.]+)", line.lower())
                if m_bus and m_kw:
                    bus = normalize(m_bus.group(1))
                    kw = float(m_kw.group(1))
                    loads[bus] = loads.get(bus, 0) + kw
    return loads

def build_graph():
    G = nx.Graph()
    for name in dss.Lines.AllNames():
        dss.Lines.Name(name)
        b1 = normalize(dss.Lines.Bus1())
        b2 = normalize(dss.Lines.Bus2())

        is_sw = name.lower().startswith("sw")

        # NF = fechada, NA = aberta
        closed = not (is_sw and name.lower() in ("sw7", "sw8"))

        if closed:
            G.add_edge(b1, b2, element=name, is_switch=is_sw)
    return G

def solve_isolated():
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
    for t in (1,2):
        for c in range(1,ncond+1):
            dss.CktElement.Open(t,c)

def close_switch(sw):
    dss.Circuit.SetActiveElement(f"Line.{sw}")
    ncond = dss.CktElement.NumConductors()
    for t in (1,2):
        for c in range(1,ncond+1):
            dss.CktElement.Close(t,c)

def simulate_nf(sw, target_u, target_v, loads):
    dss.Text.Command(f"compile {MASTER}")

    # Fecha todas as NF
    for s in ["sw1","sw2","sw3","sw4","sw5","sw6"]:
        close_switch(s)

    # Garante que as NA estão abertas
    open_switch("sw7")
    open_switch("sw8")

    # Abre a NF testada
    open_switch(sw)

    dss.Solution.Solve()

    isol = solve_isolated()
    vao_ok = (target_u in isol) and (target_v in isol)
    kw = sum(loads.get(b,0) for b in isol)

    return vao_ok, kw, isol


# =========================================================
#          CARREGA OPEN-DSS AO INICIAR
# =========================================================
try:
    dss.Text.Command(f"compile {MASTER}")
    dss.Solution.Solve()
    st.sidebar.success("Modelo IEEE-123 carregado ✔")
except Exception as e:
    st.sidebar.error(f"Erro ao carregar modelo: {e}")

coords = load_coordinates()
loads  = load_loads()

# =========================================================
#      SE ALGUM ARQUIVO FALTAR → NÃO RENDERIZA GRÁFICO
# =========================================================
if len(coords)==0 or len(loads)==0:
    st.stop()

# =========================================================
#        MAPA INTERATIVO PLOTLY (COLORIDO + CLIQUE)
# =========================================================
st.subheader("📡 Mapa Interativo da Rede")
st.markdown("### ➡ Clique em **duas barras** no grafo para definir o vão U-V.")

G = build_graph()

edge_x = []
edge_y = []
edge_colors = []

for u, v, data in G.edges(data=True):
    if u in coords and v in coords:
        x0,y0 = coords[u]
        x1,y1 = coords[v]
        edge_x += [x0, x1, None]
        edge_y += [y0, y1, None]

        if data.get("is_switch"):
            edge_colors.append("orange")
        else:
            edge_colors.append("lightgray")

node_x = [coords[n][0] for n in G.nodes()]
node_y = [coords[n][1] for n in G.nodes()]
node_text = list(G.nodes())

fig = go.Figure()

# Desenha linhas
fig.add_trace(go.Scatter(
    x=edge_x, y=edge_y,
    mode="lines",
    line=dict(width=1.5, color="lightgray"),
    hoverinfo="none"
))

# Desenha nós
fig.add_trace(go.Scatter(
    x=node_x, y=node_y,
    mode="markers+text",
    marker=dict(size=8, color="#1f77b4"),
    text=node_text,
    textposition="top center"
))

fig.update_layout(
    height=650,
    clickmode="event+select",
    dragmode="pan"
)

click = st.plotly_chart(fig, use_container_width=True)

# =========================================================
#        CAPTURA DO VÃO PELO USUÁRIO
# =========================================================
st.sidebar.subheader("Selecione o vão")

u = st.sidebar.text_input("Barra U:")
v = st.sidebar.text_input("Barra V:")

if st.sidebar.button("Confirmar vão"):
    st.session_state.vao = (u, v)

if "vao" not in st.session_state:
    st.stop()

u, v = st.session_state.vao

st.subheader(f"🔍 Analisando vão **{u} — {v}**")

# =========================================================
#      TESTA TODAS AS NFs PARA ESTE VAN
# =========================================================
NF = ["sw1","sw2","sw3","sw4","sw5","sw6"]

resultados = []
for nf in NF:
    ok, kw, isol = simulate_nf(nf, u, v, loads)
    resultados.append((nf, ok, kw, len(isol)))

df = pd.DataFrame([{
    "NF": nf,
    "Isolou": ok,
    "Carga_kW": kw,
    "Barras_isoladas": n
} for nf, ok, kw, n in resultados])

st.write("### Resultados das NF")
st.dataframe(df)

validos = df[df["Isolou"]==True]

if len(validos)==0:
    st.error("❌ Nenhuma NF isolou esse vão.")
    st.stop()

melhor = validos.sort_values(["Carga_kW","Barras_isoladas"]).iloc[0]

melhor_nf = melhor["NF"]

st.success(f"""
### ✅ Melhor NF para isolar o vão: **{melhor_nf.upper()}**

- 🔌 Carga interrompida: **{melhor['Carga_kW']} kW**
- 🧱 Barras isoladas: **{melhor['Barras_isoladas']}**
""")

# =========================================================
#            TIMELINE DE MANOBRA
# =========================================================
st.markdown("---")
st.subheader("⏱️ Timeline da Manobra (Passo a Passo)")

timeline = [
    {"Etapa": "1", "Ação": "Fechar todas as NF (estado inicial)"},
    {"Etapa": "2", "Ação": "Garantir NA abertas (SW7 / SW8)"},
    {"Etapa": "3", "Ação": f"Abrir NF ótima: **{melhor_nf.upper()}**"},
    {"Etapa": "4", "Ação": f"Confirmar que barras {u} e {v} ficaram isoladas"},
    {"Etapa": "5", "Ação": "Manutenção liberada no vão informado"},
]

df_timeline = pd.DataFrame(timeline)
st.table(df_timeline)
