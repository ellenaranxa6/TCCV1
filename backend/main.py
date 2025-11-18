# =============================================================
# BACKEND FASTAPI – RECONFIGURAÇÃO IEEE123 (1 FONTE)
# – Varredura determinística das NFs
# – Seleção da NF ótima (menor kW interrompido)
# – Compatível com execução no COLAB
# =============================================================

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

import os, re, glob
import py_dss_interface
import opendssdirect as dssod

# ===========================
# CONFIGURAÇÕES DE CAMINHOS
# ===========================
BASE = "./../123Bus/"    # mesma pasta usada no seu GitHub
RUN = BASE + "Run_IEEE123Bus.DSS"
MASTER = BASE + "IEEE123Master.dss"
LOADS = BASE + "IEEE123Loads.dss"

app = FastAPI()

# Habilitar acesso do Streamlit
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===========================
# FUNÇÕES DE SUPORTE
# ===========================
def normalize(bus):
    return bus.split('.')[0] if bus else ""

def load_loads():
    """Carrega cargas do arquivo IEEE123Loads.dss"""
    loads = {}
    if not os.path.exists(LOADS):
        return loads
    with open(LOADS,"r",encoding="utf-8",errors="ignore") as f:
        for line in f:
            s=line.strip().lower()
            if "new load" not in s: 
                continue
            m_bus=re.search(r"bus1=([\w\.]+)",s)
            m_kw=re.search(r"kw=([\d\.]+)",s)
            if m_bus and m_kw:
                bus=normalize(m_bus.group(1))
                kw=float(m_kw.group(1))
                loads[bus]=loads.get(bus,0)+kw
    return loads


def barras_por_fluxo(dss, eps_volt=1.0):
    """Retorna barras isoladas pelo critério de tensão."""
    isol, energ = [], []
    try:
        all_buses = dss.circuit.all_bus_names()
        def getv(b): dss.circuit.set_active_bus(b); return dss.bus.vmag_angle()
    except:
        all_buses = dssod.Circuit.AllBusNames()
        def getv(b): dssod.Circuit.SetActiveBus(b); return dssod.Bus.VMagAngle()

    for b in all_buses:
        mags = (getv(b) or [])[0::2]
        if max(mags) < eps_volt:
            isol.append(normalize(b))
        else:
            energ.append(normalize(b))
    return isol, energ


def open_switch(name):
    """Abre os dois terminais de uma chave NF."""
    dssod.Circuit.SetActiveElement(f"Line.{name}")
    ncond = dssod.CktElement.NumConductors()
    for t in (1,2):
        for c in range(1,ncond+1):
            dssod.CktElement.Open(t,c)


def close_switch(name):
    """Fecha os dois terminais."""
    dssod.Circuit.SetActiveElement(f"Line.{name}")
    ncond = dssod.CktElement.NumConductors()
    for t in (1,2):
        for c in range(1,ncond+1):
            dssod.CktElement.Close(t,c)


def simulate_nf(nf, loads):
    """Abre 1 NF e calcula efeito."""
    # recompilar limpo
    dssod.Basic.ClearAll()
    dssod.Text.Command(f'Compile "{MASTER}"')

    # garantir estado nominal
    for sw in ["sw1","sw2","sw3","sw4","sw5","sw6"]:
        close_switch(sw)
    for na in ["sw7","sw8"]:
        open_switch(na)

    # abrir NF
    open_switch(nf)

    dssod.Text.Command("Solve")

    isol, _ = barras_por_fluxo(dss)    
    kW = sum(loads.get(b,0) for b in isol)

    return set(isol), kW


# =======================
# ENDPOINTS FASTAPI
# =======================

@app.get("/")
def root():
    return {"status":"backend ok"}


@app.get("/mapear_nfs")
def mapear_nfs():
    """Retorna efeito individual de cada NF."""
    loads = load_loads()

    # mapa geral
    mapa = {}
    for nf in ["sw1","sw2","sw3","sw4","sw5","sw6"]:
        isol,kW = simulate_nf(nf,loads)
        mapa[nf] = {
            "isoladas": sorted(list(isol)),
            "kw": float(kW)
        }
    return {"nfs":mapa}


@app.get("/isolamento")
def isolamento(vao: str):
    """
    Recebe o nome do vão (ex.: 'l75')
    e retorna a melhor NF para isolá-lo.
    """

    # 1) Carregar modelo nominal para extrair topologia
    dssod.Basic.ClearAll()
    dssod.Text.Command(f'Compile "{MASTER}"')

    # topologia
    topo = {}
    for name in dssod.Lines.AllNames():
        if not name.lower().startswith("l"):  # linha física
            topo[name] = {}
        dssod.Lines.Name(name)
        b1 = normalize(dssod.Lines.Bus1())
        b2 = normalize(dssod.Lines.Bus2())
        topo[name] = {"from":b1, "to":b2}

    if vao not in topo:
        return {"erro":f"Vão {vao} não encontrado."}

    u = topo[vao]["from"]
    v = topo[vao]["to"]

    loads = load_loads()

    # varrer todas as NFs
    candidatos=[]
    for nf in ["sw1","sw2","sw3","sw4","sw5","sw6"]:
        isol, kW = simulate_nf(nf, loads)
        if u in isol and v in isol:
            candidatos.append((nf,kW,len(isol)))

    if not candidatos:
        return {"resultado":"nenhuma_nf_isola_vao"}

    # melhor NF: menor kW, depois menor nº de barras
    candidatos.sort(key=lambda x:(x[1],x[2]))
    best = candidatos[0]
    nf_best = best[0]

    isol_best, kw_best = simulate_nf(nf_best, loads)

    return {
        "vao": vao,
        "from": u,
        "to": v,
        "nf_escolhida": nf_best,
        "barras_isoladas": sorted(list(isol_best)),
        "kw_interrompido": float(kw_best)
    }


# ============================================================
# EXECUTAR
# (No Colab, chamar explicitamente: uvicorn.run)
# ============================================================
if __name__ == "__main__":
    print("🚀 Backend iniciado em http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
