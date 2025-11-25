import os
import re
import sqlite3
from typing import Dict, List, Tuple

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import networkx as nx

# =========================================================
#                CONFIGURAÇÃO BÁSICA
# =========================================================
st.set_page_config(page_title="Isolamento Real – IEEE 123 Bus", layout="wide")

st.title("⚡ Plataforma Interativa – Isolamento Real IEEE 123 Bus")
st.markdown(
    """
Ferramenta de apoio à manobra de **desligamento programado** em redes de distribuição,
baseada no alimentador teste IEEE-123.

- A inteligência de isolamento foi calculada previamente no **OpenDSS + Python (Colab)**  
- Os resultados foram gravados em um **banco SQLite (`ieee123_isolamento.db`)**  
- Este app usa apenas o banco + coordenadas de barras para exibir:
  - 🧩 Melhor chave **NF** de manobra para cada vão  
  - ⚡ Carga interrompida e barras isoladas  
  - 🗺️ Mapa colorido da rede com destaque do vão e da NF  
  - 📜 “Linha do tempo” da manobra
"""
)

# =========================================================
#              CAMINHOS (RELATIVOS AO REPO)
# =========================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "123Bus")

DB_CANDIDATES = [
    os.path.join(DATA_DIR, "ieee123_isolamento.db"),
    os.path.join(BASE_DIR, "ieee123_isolamento.db"),
]

COORDS_FILE = os.path.join(DATA_DIR, "BusCoords.dat")


# =========================================================
#                   FUNÇÕES DE SUPORTE
# =========================================================
def normalize_bus(bus: str) -> str:
    return bus.split(".")[0] if bus else ""


def get_db_connection() -> Tuple[sqlite3.Connection, str]:
    """Procura o arquivo ieee123_isolamento.db e abre conexão."""
    for path in DB_CANDIDATES:
        if os.path.exists(path):
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            return conn, path
    st.error("❌ Arquivo `ieee123_isolamento.db` não encontrado.\n"
             "Coloque o banco em `123Bus/ieee123_isolamento.db` ou na raiz do repositório.")
    st.stop()


def load_isolamentos(conn: sqlite3.Connection) -> pd.DataFrame:
    """Carrega tabela de isolamentos (linha, nf, barras_isoladas, kw_interrompida)."""
    # Verifica se tabela existe
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='isolamentos';")
    row = cur.fetchone()
    if not row:
        st.error("❌ Tabela `isolamentos` não encontrada no banco.\n"
                 "Confirme se o script do Colab criou a tabela com esse nome.")
        st.stop()

    # Carrega dados
    try:
        df = pd.read_sql_query(
            """
            SELECT
                linha,
                nf,
                barras_isoladas,
                kw_interrompida
            FROM isolamentos
            """,
            conn,
        )
    except Exception as e:
        st.error(f"Erro ao ler tabela `isolamentos`: {e}")
        st.stop()

    if df.empty:
        st.warning("⚠️ Tabela `isolamentos` está vazia.")
    return df


def load_coordinates(coords_path: str) -> Dict[str, Tuple[float, float]]:
    """Lê BusCoords.dat → {bus: (x, y)}."""
    coords: Dict[str, Tuple[float, float]] = {}

    if not os.path.exists(coords_path):
        st.error(f"❌ Arquivo de coordenadas não encontrado: `{coords_path}`")
        return coords

    with open(coords_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("!") or s.lower().startswith("new "):
                continue
            parts = s.split()
            if len(parts) >= 3:
                bus = parts[0].strip()
                try:
                    x = float(parts[1])
                    y = float(parts[2])
                    coords[bus] = (x, y)
                except ValueError:
                    continue

    if not coords:
        st.warning("⚠️ Nenhuma coordenada válida encontrada em BusCoords.dat.")
    return coords


def parse_lines_from_dss(data_dir: str) -> Dict[str, Tuple[str, str]]:
    """
    Procura arquivos .dss na pasta 123Bus e extrai definições de linhas:
    new line.xxx bus1=BUSA bus2=BUSB ...

    Retorna: {line_name: (bus1, bus2)}
    """
    line_map: Dict[str, Tuple[str, str]] = {}

    dss_files: List[str] = []
    for root, _, files in os.walk(data_dir):
        for f in files:
            if f.lower().endswith(".dss"):
                dss_files.append(os.path.join(root, f))

    pattern = re.compile(
        r"new\s+line\.([\w\d_]+).*?bus1=([\w\d\.]+).*?bus2=([\w\d\.]+)",
        re.IGNORECASE,
    )

    for path in dss_files:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                txt = f.read()
        except Exception:
            continue

        for m in pattern.finditer(txt):
            name = m.group(1).lower()
            bus1 = normalize_bus(m.group(2))
            bus2 = normalize_bus(m.group(3))
            # Se já existe, não sobrescreve (primeira definição ganha)
            if name not in line_map:
                line_map[name] = (bus1, bus2)

    if not line_map:
        st.warning("⚠️ Nenhuma linha encontrada nos arquivos .dss. "
                   "O grafo será limitado.")
    return line_map


def build_graph(line_map: Dict[str, Tuple[str, str]]) -> nx.Graph:
    """Cria grafo NetworkX a partir do mapeamento de linhas → (bus1, bus2)."""
    G = nx.Graph()
    for line_name, (b1, b2) in line_map.items():
        if not b1 or not b2:
            continue
        G.add_edge(b1, b2, element=line_name)
    return G


def edge_trace_for_lines(
    line_names: List[str],
    line_map: Dict[str, Tuple[str, str]],
    coords: Dict[str, Tuple[float, float]],
    color: str,
    width: float,
) -> go.Scatter:
    """Cria um trace de arestas para um conjunto de linhas, usando uma cor/espessura."""
    xs: List[float] = []
    ys: List[float] = []
    for ln in line_names:
        ln_low = ln.lower()
        if ln_low not in line_map:
            continue
        b1, b2 = line_map[ln_low]
        if b1 in coords and b2 in coords:
            x0, y0 = coords[b1]
            x1, y1 = coords[b2]
            xs += [x0, x1, None]
            ys += [y0, y1, None]

    return go.Scatter(
        x=xs,
        y=ys,
        mode="lines",
        line=dict(color=color, width=width),
        hoverinfo="none",
        showlegend=False,
    )


def node_trace_from_graph(
    G: nx.Graph,
    coords: Dict[str, Tuple[float, float]],
    vao_buses: List[str],
    nf_buses: List[str],
    source_bus: str = "150r",
) -> go.Scatter:
    """Cria trace de nós com cores diferentes (fonte, vão, NF, demais)."""
    node_x: List[float] = []
    node_y: List[float] = []
    node_text: List[str] = []
    node_color: List[str] = []

    vao_set = set(vao_buses)
    nf_set = set(nf_buses)

    for n in G.nodes():
        if n not in coords:
            continue
        x, y = coords[n]
        node_x.append(x)
        node_y.append(y)
        node_text.append(n)

        if n == source_bus:
            node_color.append("#ADFF2F")  # verde claro
        elif n in vao_set:
            node_color.append("#FFA500")  # laranja (vão em manutenção)
        elif n in nf_set:
            node_color.append("#FF4500")  # vermelho NF
        else:
            node_color.append("#1f77b4")  # azul padrão

    return go.Scatter(
        x=node_x,
        y=node_y,
        mode="markers+text",
        text=node_text,
        textposition="top center",
        marker=dict(size=8, color=node_color, line=dict(width=0.5, color="#333")),
        hovertemplate="<b>Barra:</b> %{text}<extra></extra>",
        showlegend=False,
    )


# =========================================================
#                CARREGAMENTO DE DADOS
# =========================================================
st.sidebar.subheader("📂 Dados carregados")

conn, db_path = get_db_connection()
st.sidebar.markdown(f"**Banco:** `{os.path.basename(db_path)}`")

df_iso = load_isolamentos(conn)
coords = load_coordinates(COORDS_FILE)
line_map = parse_lines_from_dss(DATA_DIR)
G = build_graph(line_map)

st.sidebar.markdown(f"- Linhas no banco: **{df_iso['linha'].nunique()}**")
st.sidebar.markdown(f"- Registros de isolamento: **{len(df_iso)}**")
st.sidebar.markdown(f"- Barras com coordenadas: **{len(coords)}**")
st.sidebar.markdown(f"- Linhas identificadas nos .dss: **{len(line_map)}**")


# =========================================================
#          SELEÇÃO DO VÃO / LINHA DE MANUTENÇÃO
# =========================================================
st.markdown("---")
st.subheader("🔧 Seleção do Vão para Desligamento")

linhas_disponiveis = sorted(df_iso["linha"].unique())

col_sel1, col_sel2 = st.columns([2, 1])

with col_sel1:
    linha_escolhida = st.selectbox(
        "Escolha o vão (linha) para manutenção:",
        options=linhas_disponiveis,
        index=0 if linhas_disponiveis else None,
    )

with col_sel2:
    st.info(
        "O banco contém **todas as opções** de desligamento por NF para cada vão. "
        "Aqui o app apenas consulta e destaca a opção que isola o vão com **menor carga interrompida** "
        "(e, em empate, menor número de barras isoladas)."
    )

if not linha_escolhida:
    st.stop()

# Filtra registros desse vão
df_vao = df_iso[df_iso["linha"] == linha_escolhida].copy()

if df_vao.empty:
    st.error(f"Nenhum registro de isolamento encontrado para a linha **{linha_escolhida}**.")
    st.stop()

# Ordena candidatos por critério
df_vao.sort_values(["kw_interrompida", "barras_isoladas"], inplace=True)

nf_melhor = df_vao.iloc[0]["nf"]
kw_melhor = df_vao.iloc[0]["kw_interrompida"]
barras_melhor = df_vao.iloc[0]["barras_isoladas"]

col_info1, col_info2 = st.columns([2, 2])

with col_info1:
    st.markdown(f"### 📌 Vão selecionado: **{linha_escolhida}**")
    st.markdown(f"### 🧭 NF de manobra ótima: **{nf_melhor.upper()}**")

with col_info2:
    st.metric("⚡ Carga interrompida (kW)", f"{kw_melhor:.1f}")
    st.metric("🔻 Barras isoladas", int(barras_melhor))

st.markdown("#### 📋 Todas as opções de NF para este vão")
df_vao_view = df_vao.copy()
df_vao_view["nf"] = df_vao_view["nf"].str.upper()
df_vao_view.rename(
    columns={
        "nf": "NF",
        "barras_isoladas": "Barras isoladas",
        "kw_interrompida": "kW interrompida",
    },
    inplace=True,
)
st.dataframe(df_vao_view[["NF", "Barras isoladas", "kW interrompida"]], use_container_width=True)


# =========================================================
#           MAPA COLORIDO COM DESTAQUE DA NF
# =========================================================
st.markdown("---")
st.subheader("🗺️ Mapa da Rede com Destaque do Vão e da NF de Manobra")

# Descobre buses do vão (pelo nome da linha nos .dss)
vao_buses: List[str] = []
linha_low = linha_escolhida.lower()
if linha_low in line_map:
    vao_buses = list(line_map[linha_low])
else:
    st.warning(
        f"⚠️ Linha **{linha_escolhida}** não encontrada nos arquivos .dss. "
        "O vão não será destacado no grafo."
    )

# Descobre buses da NF de manobra (também via .dss)
nf_buses: List[str] = []
nf_low = str(nf_melhor).lower()
if nf_low in line_map:
    nf_buses = list(line_map[nf_low])
else:
    st.warning(
        f"⚠️ NF **{nf_melhor}** não encontrada nos arquivos .dss. "
        "Ela será destacada apenas na timeline textual."
    )

# Se não houver coords, não plota grafo
if not coords or not G.nodes:
    st.error("Não foi possível construir o grafo da rede (faltam coordenadas ou linhas).")
else:
    # Linhas normais (todas as linhas de distribuição)
    todas_linhas = list(line_map.keys())

    # Separar listas para os highlights
    linhas_nf = [nf_low] if nf_low in line_map else []
    linhas_vao = [linha_low] if linha_low in line_map else []

    # Base: todas as linhas em cinza claro
    base_lines = edge_trace_for_lines(
        todas_linhas, line_map, coords, color="#B0B0B0", width=1.0
    )

    # Destaque do vão (laranja)
    vao_lines = edge_trace_for_lines(
        linhas_vao, line_map, coords, color="#FFA500", width=3.0
    )

    # Destaque da NF (vermelho)
    nf_lines = edge_trace_for_lines(
        linhas_nf, line_map, coords, color="#FF4500", width=3.0
    )

    # Nós coloridos
    nodes_trace = node_trace_from_graph(
        G,
        coords,
        vao_buses=vao_buses,
        nf_buses=nf_buses,
        source_bus="150r",  # fonte pós-regulador, como no seu script do Colab
    )

    fig = go.Figure()
    fig.add_trace(base_lines)
    fig.add_trace(vao_lines)
    fig.add_trace(nf_lines)
    fig.add_trace(nodes_trace)

    fig.update_layout(
        height=650,
        showlegend=False,
        margin=dict(l=10, r=10, t=40, b=10),
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        title=f"Rede IEEE-123 – Vão {linha_escolhida} e NF {nf_melhor.upper()} em destaque",
    )

    st.plotly_chart(fig, use_container_width=True)


# =========================================================
#                “TIMELINE” DA MANOBRA
# =========================================================
st.markdown("---")
st.subheader("📜 Timeline da Manobra de Desligamento")

vao_desc = f"{linha_escolhida}"
if vao_buses:
    vao_desc += f" (entre barras {vao_buses[0]} e {vao_buses[1]})"

nf_desc = nf_melhor.upper()
if nf_buses:
    nf_desc += f" (entre barras {nf_buses[0]} e {nf_buses[1]})"

st.markdown(
    f"""
**Passo 1 – Condição inicial**

- Todas as chaves **NF (SW1…SW6)** fechadas  
- Chaves **NA (SW7 / SW8)** abertas  
- Rede radial alimentada pela barra **150r** (pós-regulador)

---

**Passo 2 – Seleção do vão de manutenção**

- Vão escolhido: **{vao_desc}**  
- O banco é consultado para recuperar **todas as NFs** que, quando abertas, desenergizam as duas barras do vão  
- Para cada NF, foram armazenados no banco:
  - 🔻 Número de barras isoladas  
  - ⚡ Potência total interrompida (kW)

---

**Passo 3 – Escolha da NF de manobra ótima**

- Critério adotado:
  1. **Menor potência interrompida (kW)**  
  2. Em empate, **menor número de barras isoladas**

- Para o vão **{linha_escolhida}**, a chave ótima é:  
  👉 **{nf_desc}**  
  - ⚡ Carga interrompida: **{kw_melhor:.1f} kW**  
  - 🔻 Barras isoladas: **{int(barras_melhor)}**

---

**Passo 4 – Execução operacional (campo)**

1. Confirmar permissões, autorizações e condições de segurança (tags, bloqueios, etc.)  
2. Executar a abertura da chave **{nf_melhor.upper()}** conforme procedimento da concessionária  
3. Verificar ausência de tensão no vão **{linha_escolhida}** e nas barras associadas  
4. Liberar o trecho para manutenção

> ℹ️ Toda a lógica de cálculo (OpenDSS + Python) foi executada **offline** e consolidada neste banco.  
> Este app apenas consulta o banco e apresenta, de forma visual, a melhor opção de manobra para cada vão.
"""
)
