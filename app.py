import streamlit as st
import datetime
import pandas as pd
import requests
from supabase import create_client, Client

# 1. ESTO DEBE SER LO PRIMERO
st.set_page_config(page_title="Proyecto Apuestas Futbol", layout="wide")

# --- CANDADO DE SEGURIDAD ---
if "acceso_concedido" not in st.session_state:
    st.session_state.acceso_concedido = False

if not st.session_state.acceso_concedido:
    st.title("🔒 Acceso Restringido")
    st.write("Esta es una herramienta privada. Ingresa el PIN para continuar.")
    
    pin_usuario = st.text_input("PIN de seguridad:", type="password")
    
    if st.button("Entrar"):
        if pin_usuario == str(st.secrets["APP_PIN"]):
            st.session_state.acceso_concedido = True
            st.rerun() 
        else:
            st.error("❌ PIN incorrecto.")
            
    st.stop() 

# --- CONEXIÓN A SUPABASE (NUBE) ---
url = st.secrets["SUPABASE_URL"]
key = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(url, key)

# --- 1. MEMORIA DE LA APLICACIÓN ---
if "partidos_parlay" not in st.session_state:
    st.session_state.partidos_parlay = []
if "api_resultados" not in st.session_state:
    st.session_state.api_resultados = None
if "fecha_busqueda" not in st.session_state:
    st.session_state.fecha_busqueda = None

def agregar_al_parlay(nombre_partido):
    if nombre_partido not in st.session_state.partidos_parlay:
        st.session_state.partidos_parlay.append(nombre_partido)

def limpiar_parlay():
    st.session_state.partidos_parlay = []

# --- 2. FUNCIONES DE BASE DE DATOS (NUBE) ---
def guardar_apuesta(partido, fecha, pronostico, momio, stake, tipo, motivo):
    datos = {
        "partido": partido,
        "fecha_partido": fecha,
        "pronostico": pronostico,
        "momio": momio,
        "stake": stake,
        "tipo_apuesta": tipo,
        "motivo_descarte": motivo
    }
    supabase.table('apuestas').insert(datos).execute()

def obtener_apuestas():
    response = supabase.table('apuestas').select("*").execute()
    df = pd.DataFrame(response.data)
    return df

def actualizar_resultado(id_apuesta, estado, resultado_real, profit):
    datos = {
        "estado": estado,
        "resultado_real": resultado_real,
        "profit": profit
    }
    supabase.table('apuestas').update(datos).eq('id', id_apuesta).execute()

def calcular_profit(stake, momio, estado):
    if estado == "Perdida":
        return -stake
    elif estado == "Anulada (Push)":
        return 0.0
    elif estado == "Ganada":
        if momio > 0:
            return stake * (momio / 100)
        elif momio < 0:
            return stake / (abs(momio) / 100)
    return 0.0

# --- 3. FUNCIONES DE MOMIOS Y API ---
def decimal_a_americano(decimal_str):
    try:
        dec = float(decimal_str)
        if dec >= 2.0:
            return f"+{int(round((dec - 1) * 100))}"
        elif dec > 1.0:
            return f"{int(round(-100 / (dec - 1)))}"
        else:
            return "N/A"
    except:
        return "N/A"

def obtener_partidos_y_momios(api_key, fecha_elegida):
    headers = {"x-apisports-key": api_key}
    querystring = {"date": fecha_elegida.strftime("%Y-%m-%d"), "timezone": "America/Mexico_City"}
    
    url_fix = "https://v3.football.api-sports.io/fixtures"
    res_fix = requests.get(url_fix, headers=headers, params=querystring)
    
    query_odds = {"date": fecha_elegida.strftime("%Y-%m-%d"), "bookmaker": "8", "timezone": "America/Mexico_City"}
    url_odds = "https://v3.football.api-sports.io/odds"
    res_odds = requests.get(url_odds, headers=headers, params=query_odds)
    
    if res_fix.status_code == 200:
        fixtures_data = res_fix.json().get("response", [])
        if res_odds.status_code == 200:
            odds_data = res_odds.json().get("response", [])
            diccionario_momios = {}
            for o in odds_data:
                fix_id = o["fixture"]["id"]
                bets = o.get("bookmakers", [{}])[0].get("bets", [])
                ganador_bet = next((b for b in bets if b["name"] == "Match Winner" or b["id"] == 1), None)
                if ganador_bet:
                    diccionario_momios[fix_id] = ganador_bet["values"]
            for f in fixtures_data:
                f_id = f["fixture"]["id"]
                f["momios_1x2"] = diccionario_momios.get(f_id, None)
        return {"response": fixtures_data}
    return None

# --- 4. DISEÑO DE LA PÁGINA ---
st.title("⚽ Proyecto Apuestas Futbol")
st.subheader("Sistema Especializado en Parlays de Doble Oportunidad")

tab1, tab2, tab3 = st.tabs(["📝 Armar Parlay", "📊 Control de Resultados", "📅 Explorador de Partidos"])

with tab1:
    st.header("Construir Parlay (Doble Oportunidad)")
    if len(st.session_state.partidos_parlay) == 0:
        st.info("👈 Ve a la Pestaña 'Explorador de Partidos' y agrega los juegos para armar tu Parlay.")
    else:
        st.button("🗑️ Limpiar selecciones actuales", on_click=limpiar_parlay)
        with st.form("formulario_parlay_doble"):
            st.markdown("### Selecciona tu Doble Oportunidad para cada partido:")
            pronosticos_lista = []
            for partido in st.session_state.partidos_parlay:
                opcion = st.selectbox(f"⚽ {partido}", ["Local o Empate (1X)", "Empate o Visita (X2)", "Local o Visita (12)"], key=f"opt_{partido}")
                pronosticos_lista.append(f"{partido} -> {opcion}")
                
            st.markdown("---")
            col1, col2 = st.columns(2)
            with col1:
                fecha = st.date_input("Fecha del Ticket", datetime.date.today())
                momio = st.number_input("Momio Americano Total", value=-110, step=10, format="%d")
            with col2:
                stake = st.number_input("Stake ($)", min_value=0.0, value=100.0, step=50.0)
                tipo_apuesta = st.selectbox("Tipo", ["Apuesta Real", "Apuesta Descartada"])
                
            motivo_descarte = st.text_area("Motivo de descarte")
            if st.form_submit_button("💾 Guardar Parlay"):
                partidos_str = "\n".join(st.session_state.partidos_parlay)
                pronosticos_str = "\n".join(pronosticos_lista)
                guardar_apuesta(partidos_str, fecha.strftime("%Y-%m-%d"), pronosticos_str, momio, stake, tipo_apuesta, motivo_descarte)
                limpiar_parlay()
                st.success("¡Parlay de Doble Oportunidad guardado correctamente!")

with tab2:
    st.header("Resolución y Gráficas de Rendimiento")
    df_apuestas = obtener_apuestas()
    if not df_apuestas.empty:
        apuestas_reales = df_apuestas[df_apuestas['tipo_apuesta'] == 'Apuesta Real']
        resueltas = apuestas_reales[apuestas_reales['estado'] != 'Pendiente']
        pendientes = df_apuestas[df_apuestas['estado'] == 'Pendiente']
        
        st.markdown("### 📈 Tus Números")
        col_m1, col_m2, col_m3 = st.columns(3)
        profit_total = resueltas['profit'].sum()
        stake_total = resueltas['stake'].sum()
        yield_pct = (profit_total / stake_total * 100) if stake_total > 0 else 0.0
        ganadas = len(resueltas[resueltas['estado'] == 'Ganada'])
        total_resueltas = len(resueltas)
        win_rate = (ganadas / total_resueltas * 100) if total_resueltas > 0 else 0.0
        
        col_m1.metric(label="💰 Profit / Loss Neto", value=f"${profit_total:.2f}")
        col_m2.metric(label="📊 Yield (Retorno)", value=f"{yield_pct:.2f}%")
        col_m3.metric(label="🎯 Porcentaje de Acierto", value=f"{win_rate:.1f}%")
        
        if not resueltas.empty:
            st.markdown("### Evolución de Ganancias/Pérdidas")
            grafica_df = resueltas.groupby('fecha_partido')['profit'].sum().reset_index()
            grafica_df.set_index('fecha_partido', inplace=True)
            st.bar_chart(grafica_df['profit'])
        st.markdown("---")
        
        if not pendientes.empty:
            st.subheader("Resolver ticket pendiente")
            with st.form("resolver_apuesta"):
                col_sel, col_res, col_est = st.columns(3)
                with col_sel:
                    opciones = pendientes.apply(lambda x: f"ID {x['id']} | Parlay", axis=1).tolist()
                    seleccion = st.selectbox("Elige la apuesta a resolver", opciones)
                    id_seleccionado = int(seleccion.split(" ")[1])
                with col_res:
                    resultado_input = st.text_input("Marcador final", placeholder="Ej: Se acertaron todos")
                with col_est:
                    estado_input = st.selectbox("Estado", ["Ganada", "Perdida", "Anulada (Push)"])
                
                if st.form_submit_button("✅ Actualizar Resultado"):
                    apuesta_original = pendientes[pendientes['id'] == id_seleccionado].iloc[0]
                    stake_orig = apuesta_original['stake']
                    momio_orig = apuesta_original['momio']
                    tipo_orig = apuesta_original['tipo_apuesta']
                    profit_calculado = 0.0 if tipo_orig == "Apuesta Descartada" else calcular_profit(stake_orig, momio_orig, estado_input)
                    actualizar_resultado(id_seleccionado, estado_input, resultado_input, profit_calculado)
                    st.success(f"Ticket ID {id_seleccionado} actualizado.")
                    st.rerun()
        st.markdown("---")
        
        # --- NUEVO: BOTÓN DE EXPORTACIÓN ---
        st.subheader("Tu Historial Completo")
        
        col_tabla, col_btn = st.columns([4, 1])
        with col_btn:
            # Convertimos a CSV y preparamos el botón de descarga
            csv = df_apuestas.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="📥 Exportar a Excel (CSV)",
                data=csv,
                file_name=f"Mi_Historial_Apuestas_{datetime.date.today()}.csv",
                mime="text/csv",
                use_container_width=True
            )
            
        # Mostramos la tabla debajo
        st.dataframe(df_apuestas[['id', 'fecha_partido', 'partido', 'pronostico', 'momio', 'stake', 'estado', 'resultado_real', 'profit']], use_container_width=True)
    else:
        st.info("No hay apuestas registradas.")

with tab3:
    st.header("Explorador Global de Partidos")
    api_key_usuario = st.text_input("Pega tu API Key aquí:", type="password")
    fecha_buscar = st.date_input("¿Qué día exacto quieres analizar?", datetime.date.today())
    
    if st.button("🔍 Buscar Partidos del Día"):
        if api_key_usuario == "":
            st.warning("⚠️ Pon tu API Key arriba.")
        else:
            with st.spinner(f'Buscando partidos y calculando momios del {fecha_buscar}...'):
                datos = obtener_partidos_y_momios(api_key_usuario, fecha_buscar)
                st.session_state.api_resultados = datos
                st.session_state.fecha_busqueda = fecha_buscar

    if st.session_state.api_resultados is not None and st.session_state.fecha_busqueda == fecha_buscar:
        datos = st.session_state.api_resultados
        if "response" in datos:
            partidos = datos["response"]
            if len(partidos) > 0:
                st.success(f"¡Se encontraron {len(partidos)} partidos para el {fecha_buscar}!")
                for p in partidos:
                    liga = p["league"]["name"]
                    pais = p["league"]["country"]
                    local = p["teams"]["home"]["name"]
                    visita = p["teams"]["away"]["name"]
                    hora = p["fixture"]["date"][11:16]
                    partido_texto = f"{local} vs {visita}"
                    id_partido = p["fixture"]["id"]
                    
                    texto_momios = ""
                    if "momios_1x2" in p and p["momios_1x2"]:
                        vals = p["momios_1x2"]
                        if len(vals) == 3:
                            texto_momios = f" | 💵 **L** {decimal_a_americano(vals[0]['odd'])} | **E** {decimal_a_americano(vals[1]['odd'])} | **V** {decimal_a_americano(vals[2]['odd'])}"
                    else:
                        texto_momios = " | 💵 Momios no disponibles"
                    
                    col_info, col_btn = st.columns([5, 1])
                    with col_info:
                        st.info(f"🌍 {pais} - {liga} | ⏰ {hora} HRS | ⚽ **{partido_texto}** {texto_momios}")
                    with col_btn:
                        st.button("➕ Agregar", key=f"btn_{id_partido}", on_click=agregar_al_parlay, args=(partido_texto,))