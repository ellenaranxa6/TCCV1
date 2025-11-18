import streamlit as st
import requests
import plotly.graph_objects as go

# ====================================
# CONFIGURAÇÃO
# ====================================

st.set_page_config(page_title="Reconfiguração IEEE-123", layout="wide")

st.title("⚡ Plataforma Interativa – IEEE 123 Bus")
st.write("Selecione um vão e deixe o backend decidir a chave ótima.")

# 👉 Ajuste aqui a URL do SEU backend (NGROK)
BACKEND_URL = "https://SEU-NGROK.ngrok-free.app"

# ====================================
# FUNÇÕES AUXILIARES
# ====================================

@st.cache_data
def get_lines():
    """Lista de todos os vãos (linhas) do backend."""
    try:
        r = requests.get(f"{BACKEND_URL}/list-lines")
        return r.json()
    except:
        return []

def get_best_switch(bus_u, bus_v):
    payload = {"bus_u": bus_u, "bus_v": bus_v}
    r = requests.post(f"{BACKEND_URL}/best-switch", json=payload)
    return r.json()

# ====================================
# SIDEBAR – CONTROLES
# ====================================

st.sidebar.header("🔌 Seleção do vão")

all_lines = get_lines()

if not all_lines:
    st.sidebar.error("❌ Não foi possível carregar as linhas do backend.")
    st.stop()

line_names = [f"{l['name']}  ({l['bus1']} — {l['bus2']})" for l in all_lines]
selected_line = st.sidebar.selectbox("Escolha o vão", line_names)

selected_obj = all_lines[line_names.index(selected_line)]
u = selected_obj["bus1"]
v = selected_obj["bus2"]

st.sidebar.success(f"Vão selecionado: {u} — {v}")

# Botão de simulação
if st.sidebar.button("▶ Rodar simulação"):
    result = get_best_switch(u, v)

    st.subheader("🔍 Resultado da Simulação")

    if result["status"] != "ok":
        st.error("Nenhuma NF isolou o vão.")
        st.json(result)
    else:
        nf = result["best_switch"]
        st.success(f"### 🔑 Chave ótima: **{nf.upper()}**")
        st.write(f"⚡ Carga interrompida: **{result['kW_interrupt']} kW**")

        isoladas = result["isolated_buses"]

        # PLOT SIMPLIFICADO
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=[0], y=[0],
            mode="markers",
            marker=dict(size=1),
            showlegend=False
        ))

        fig.update_layout(
            title=f"Vão: {u} — {v} | NF ótima: {nf.upper()}",
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
            height=200
        )

        st.plotly_chart(fig, use_container_width=True)

        st.write("### Barras isoladas")
        st.write(isoladas)

