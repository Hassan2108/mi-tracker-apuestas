import streamlit as st
import datetime
import pandas as pd
import requests
import re
import plotly.express as px
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

# --- MEMORIA DE LA APLICACIÓN ---
if "partidos_parlay" not in st.session_state: st.session_state.partidos_parlay = []
if "api_resultados" not in st.session_state: st.session_state.api_resultados = None
if "fecha_busqueda" not in st.session_state: st.session_state.fecha_busqueda = None
if "bankroll_inicial" not in st.session_state: st.session_state.bankroll_inicial = 5000.0

def agregar_al_parlay(nombre_partido, nombre_liga):
    if not any(p['partido'] == nombre_partido for p in st.session_state.partidos_parlay):
        st.session_state.partidos_parlay.append({"partido": nombre_partido, "liga": nombre_liga})

def remover_del_parlay(nombre_partido):
    st.session_state.partidos_parlay = [p for p in st.session_state.partidos_parlay if p['partido'] != nombre_partido]

def limpiar_parlay():
    st.session_state.partidos_parlay = []

# --- 2. FUNCIONES DE BASE DE DATOS Y MATEMÁTICAS ---
def guardar_apuesta(partido, liga, fecha, pronostico, momio, stake, tipo, motivo, etiquetas):
    datos = {
        "partido": partido, "liga": liga, "fecha_partido": fecha,
        "pronostico": pronostico, "momio": momio, "stake": stake,
        "tipo_apuesta": tipo, "motivo_descarte": motivo, "etiquetas": etiquetas
    }
    supabase.table('apuestas').insert(datos).execute()

def obtener_apuestas():
    response = supabase.table('apuestas').select("*").execute()
    return pd.DataFrame(response.data)

def actualizar_resultado(id_apuesta, estado, resultado_real, profit, momio_nuevo, momio_cierre):
    datos = {
        "estado": estado, "resultado_real": resultado_real,
        "profit": profit, "momio": momio_nuevo, "momio_cierre": momio_cierre
    }
    supabase.table('apuestas').update(datos).eq('id', id_apuesta).execute()

def evaluar_doble_oportunidad(goles_local, goles_visita, pronostico):
    if goles_local > goles_visita: resultado_real = "1" 
    elif goles_local < goles_visita: resultado_real = "2" 
    else: resultado_real = "X" 
    if "1X" in pronostico: return resultado_real in ["1", "X"]
    elif "X2" in pronostico: return resultado_real in ["X", "2"]
    elif "12" in pronostico: return resultado_real in ["1", "2"]
    return False

def americano_a_decimal(americano):
    try:
        am = float(americano)
        if am > 0: return (am / 100.0) + 1
        elif am < 0: return (100.0 / abs(am)) + 1
        else: return 1.0
    except: return 1.0

def calcular_fraccion_kelly(probabilidad_real, momio_americano):
    decimal = americano_a_decimal(momio_americano)
    prob_decimal = probabilidad_real / 100.0
    b = decimal - 1
    if b <= 0: return 0
    f_star = (prob_decimal * b - (1 - prob_decimal)) / b
    return max(0, f_star) 

def calcular_rendimiento_equipos(df):
    rendimiento = {}
    if df.empty: return rendimiento
    resueltas = df[(df['estado'] != 'Pendiente') & (df['tipo_apuesta'] == 'Apuesta Real') & (df['estado'] != 'Cash Out')]
    for _, row in resueltas.iterrows():
        if not isinstance(row['resultado_real'], str) or row['resultado_real'] == '-': continue
        partidos = str(row['partido']).split('\n')
        pronosticos = str(row['pronostico']).split('\n')
        resultados = str(row['resultado_real']).split('\n')
        if len(partidos) == len(pronosticos) == len(resultados):
            for part, pron, res in zip(partidos, pronosticos, resultados):
                equipos = part.split(" vs ")
                if len(equipos) == 2:
                    loc, vis = equipos[0].strip(), equipos[1].strip()
                    if "(ANULADO)" in res: continue
                    match = re.search(r'\((\d+)-(\d+)\)', res)
                    if match:
                        g_loc, g_vis = int(match.group(1)), int(match.group(2))
                        win = evaluar_doble_oportunidad(g_loc, g_vis, pron)
                        val = 1 if win else -1
                        rendimiento[loc] = rendimiento.get(loc, 0) + val
                        rendimiento[vis] = rendimiento.get(vis, 0) + val
    return rendimiento

def calcular_rendimiento_ligas(df):
    stats = {}
    if df.empty or 'liga' not in df.columns: return pd.DataFrame()
    resueltas = df[(df['estado'] != 'Pendiente') & (df['tipo_apuesta'] == 'Apuesta Real') & (df['estado'] != 'Cash Out')]
    for _, row in resueltas.iterrows():
        if not isinstance(row['resultado_real'], str) or row['resultado_real'] == '-': continue
        partidos = str(row['partido']).split('\n')
        pronosticos = str(row['pronostico']).split('\n')
        resultados = str(row['resultado_real']).split('\n')
        ligas = str(row['liga']).split('\n') if 'liga' in row and pd.notna(row['liga']) and row['liga'] != "" else ["No registrada"] * len(partidos)
        if len(ligas) < len(partidos): ligas += ["No registrada"] * (len(partidos) - len(ligas))
        if len(partidos) == len(pronosticos) == len(resultados):
            for part, pron, res, lig in zip(partidos, pronosticos, resultados, ligas):
                if "(ANULADO)" in res: continue
                match = re.search(r'\((\d+)-(\d+)\)', res)
                if match:
                    g_loc, g_vis = int(match.group(1)), int(match.group(2))
                    win = evaluar_doble_oportunidad(g_loc, g_vis, pron)
                    if lig not in stats: stats[lig] = {"Aciertos": 0, "Fallos": 0}
                    if win: stats[lig]["Aciertos"] += 1
                    else: stats[lig]["Fallos"] += 1
    if not stats: return pd.DataFrame()
    df_stats = pd.DataFrame.from_dict(stats, orient='index')
    df_stats['Total Jugados'] = df_stats['Aciertos'] + df_stats['Fallos']
    df_stats['% Efectividad'] = (df_stats['Aciertos'] / df_stats['Total Jugados'] * 100).round(1).astype(str) + '%'
    return df_stats.sort_values('Aciertos', ascending=False)

def colorizar_equipo(equipo, dict_rendimiento):
    score = dict_rendimiento.get(equipo, 0)
    if score <= -3: return f"<span style='color:#b284be; font-weight:bold;'>{equipo}</span>"
    elif score == -2: return f"<span style='color:#ff4b4b; font-weight:bold;'>{equipo}</span>"
    elif score == -1: return f"<span style='color:#ffa500; font-weight:bold;'>{equipo}</span>"
    elif score == 1: return f"<span style='color:#ffd700; font-weight:bold;'>{equipo}</span>"
    elif score == 2: return f"<span style='color:#3399ff; font-weight:bold;'>{equipo}</span>"
    elif score >= 3: return f"<span style='color:#00ff00; font-weight:bold;'>{equipo}</span>"
    else: return equipo 

df_apuestas = obtener_apuestas()
dict_rendimiento = calcular_rendimiento_equipos(df_apuestas)

# --- CALCULOS DE BANKROLL GLOBAL ---
profit_global = 0.0
if not df_apuestas.empty:
    resueltas_global = df_apuestas[(df_apuestas['tipo_apuesta'] == 'Apuesta Real') & (df_apuestas['estado'] != 'Pendiente')]
    profit_global = resueltas_global['profit'].sum()
bankroll_actual = st.session_state.bankroll_inicial + profit_global

# --- BARRA LATERAL (SIDEBAR) ---
with st.sidebar:
    st.title("💼 Mi Bankroll")
    st.session_state.bankroll_inicial = st.number_input("Capital Inicial ($):", value=st.session_state.bankroll_inicial, step=500.0)
    st.metric(label="Bankroll Actual", value=f"${bankroll_actual:.2f}", delta=f"${profit_global:.2f}")
    
    st.markdown("---")
    st.markdown("### 🧮 Calculadora Kelly")
    st.write("¿Cuánto apostar según el valor real?")
    kelly_momio = st.number_input("Momio del Ticket", value=-110, step=10, key="kelly_m")
    kelly_prob = st.slider("Tu % real de ganarlo", min_value=1, max_value=99, value=55)
    fraccion = calcular_fraccion_kelly(kelly_prob, kelly_momio)
    
    if fraccion > 0:
        stake_recomendado = bankroll_actual * fraccion
        st.success(f"**Valor Positivo (+EV)**\nApostar: {fraccion*100:.1f}% del Banco\n**Sugerido: ${stake_recomendado:.0f}**")
    else:
        st.error("**Valor Negativo (-EV)**\nLas matemáticas sugieren NO hacer esta apuesta.")

# --- 3. FUNCIONES DE API ---
def decimal_a_americano(decimal_str):
    try:
        dec = float(decimal_str)
        if dec >= 2.0: return f"+{int(round((dec - 1) * 100))}"
        elif dec > 1.0: return f"{int(round(-100 / (dec - 1)))}"
        else: return "N/A"
    except: return "N/A"

def obtener_partidos_y_momios(api_key, fecha_elegida):
    headers = {"x-apisports-key": api_key}
    querystring = {"date": fecha_elegida.strftime("%Y-%m-%d"), "timezone": "America/Mexico_City"}
    url_fix = "https://v3.football.api-sports.io/fixtures"
    res_fix = requests.get(url_fix, headers=headers, params=querystring)
    if res_fix.status_code != 200: return {"errors": f"Error HTTP: {res_fix.status_code}"}
    datos_fix = res_fix.json()
    if "errors" in datos_fix and datos_fix["errors"]: return {"errors": datos_fix["errors"]}
    fixtures_data = datos_fix.get("response", [])
    if len(fixtures_data) > 0:
        query_odds = {"date": fecha_elegida.strftime("%Y-%m-%d"), "bookmaker": "8", "timezone": "America/Mexico_City"}
        url_odds = "https://v3.football.api-sports.io/odds"
        res_odds = requests.get(url_odds, headers=headers, params=query_odds)
        if res_odds.status_code == 200:
            datos_odds = res_odds.json()
            if not ("errors" in datos_odds and datos_odds["errors"]):
                odds_data = datos_odds.get("response", [])
                diccionario_momios = {}
                for o in odds_data:
                    fix_id = o["fixture"]["id"]
                    bets = o.get("bookmakers", [{}])[0].get("bets", [])
                    ganador_bet = next((b for b in bets if b["name"] == "Match Winner" or b["id"] == 1), None)
                    if ganador_bet: diccionario_momios[fix_id] = ganador_bet["values"]
                for f in fixtures_data:
                    f_id = f["fixture"]["id"]
                    f["momios_1x2"] = diccionario_momios.get(f_id, None)
    return {"response": fixtures_data}

# --- 4. DISEÑO DE LA PÁGINA ---
st.title("⚽ Dashboard Profesional de Apuestas")
tab1, tab2, tab3 = st.tabs(["📝 Armar Parlay", "📊 Panel de Control y Resoluciones", "📅 Explorador de Partidos"])

with tab1:
    st.header("Construir Ticket (Doble Oportunidad)")
    with st.expander("✍️ Agregar partidos manualmente", expanded=len(st.session_state.partidos_parlay) == 0):
        with st.form("form_manual", clear_on_submit=True):
            col_txt, col_lig, col_btn = st.columns([3, 2, 1])
            with col_txt: partido_manual = st.text_input("Partido (Ej: América vs Chivas)")
            with col_lig: liga_manual = st.text_input("Liga (Ej: Liga MX)")
            with col_btn:
                st.write(""); st.write("")
                if st.form_submit_button("➕ Agregar"):
                    if partido_manual.strip():
                        liga_final = liga_manual.strip() if liga_manual.strip() else "Manual"
                        agregar_al_parlay(partido_manual.strip(), liga_final)
                        st.rerun()
    st.markdown("---")
    if len(st.session_state.partidos_parlay) == 0:
        st.info("👈 Ingresa partidos o búscalos en el Explorador.")
    else:
        st.button("🗑️ Borrar TODO", on_click=limpiar_parlay)
        st.markdown("### Selecciones:")
        for p in st.session_state.partidos_parlay:
            partido_str, liga_str = p['partido'], p['liga']
            equipos = partido_str.split(" vs ")
            if len(equipos) == 2:
                loc_c, vis_c = colorizar_equipo(equipos[0], dict_rendimiento), colorizar_equipo(equipos[1], dict_rendimiento)
                partido_html = f"⚽ <b>{loc_c} vs {vis_c}</b> <span style='font-size:0.8em; color:gray;'>({liga_str})</span>"
            else:
                partido_html = f"⚽ <b>{partido_str}</b> <span style='font-size:0.8em; color:gray;'>({liga_str})</span>"
            col_texto, col_btn = st.columns([10, 1])
            with col_texto: st.markdown(partido_html, unsafe_allow_html=True)
            with col_btn: st.button("❌", key=f"del_{partido_str}", on_click=remover_del_parlay, args=(partido_str,))
                
        st.markdown("---")
        with st.form("formulario_parlay_doble"):
            st.markdown("### Configuración del Ticket:")
            pronosticos_lista = []
            for p in st.session_state.partidos_parlay:
                opcion = st.selectbox(f"Pronóstico para: {p['partido']}", ["Local o Empate (1X)", "Empate o Visita (X2)", "Local o Visita (12)"], key=f"opt_{p['partido']}")
                pronosticos_lista.append(f"{p['partido']} -> {opcion}")
            
            st.markdown("---")
            lista_etiquetas = ["#LocalNoFavorito", "#Lluvia", "#CambioDeEntrenador", "#Clasico/Derby", "#BajasImportantes", "#HándicapAsiático"]
            etiquetas_seleccionadas = st.multiselect("🏷️ Etiquetas de Contexto (Opcional):", lista_etiquetas)
            
            st.markdown("---")
            col1, col2 = st.columns(2)
            with col1:
                fecha = st.date_input("Fecha del Ticket", datetime.date.today())
                momio = st.number_input("Momio Americano Total", value=-110, step=10, format="%d")
            with col2:
                stake = st.number_input(f"Stake ($)", min_value=0.0, value=100.0, step=50.0)
                tipo_apuesta = st.selectbox("Tipo", ["Apuesta Real", "Apuesta Descartada"])
                
            motivo_descarte = st.text_area("Motivo (si es descartada)")
            if st.form_submit_button("💾 Guardar Parlay"):
                partidos_str = "\n".join([p['partido'] for p in st.session_state.partidos_parlay])
                ligas_str = "\n".join([p['liga'] for p in st.session_state.partidos_parlay])
                pronosticos_str = "\n".join(pronosticos_lista)
                etiquetas_str = ", ".join(etiquetas_seleccionadas) 
                
                guardar_apuesta(partidos_str, ligas_str, fecha.strftime("%Y-%m-%d"), pronosticos_str, momio, stake, tipo_apuesta, motivo_descarte, etiquetas_str)
                limpiar_parlay()
                st.success("¡Parlay guardado correctamente!")

        with st.expander("🤖 Consultar a Gemini antes de apostar"):
            prompt_IA = "Hola Gemini, estoy armando un parlay de Doble Oportunidad. Dame tu análisis estadístico y dime cuál es la jugada más segura (1X, X2 o 12) para:\n\n"
            for p in st.session_state.partidos_parlay: prompt_IA += f"- {p['partido']} ({p['liga']})\n"
            st.code(prompt_IA, language="markdown")

with tab2:
    st.header("Dashboard Analítico y Resoluciones")
    if not df_apuestas.empty:
        df_apuestas['fecha_partido'] = pd.to_datetime(df_apuestas['fecha_partido'])
        
        resueltas_todas = df_apuestas[(df_apuestas['estado'].isin(['Ganada', 'Perdida'])) & (df_apuestas['tipo_apuesta'] == 'Apuesta Real')]
        clv_positivo_count = 0
        
        for _, row in resueltas_todas.iterrows():
            m_compra = row['momio']
            m_cierre = row.get('momio_cierre', 0)
            if pd.notna(m_cierre) and float(m_cierre) != 0:
                dec_compra = americano_a_decimal(m_compra)
                dec_cierre = americano_a_decimal(m_cierre)
                if dec_compra > dec_cierre: clv_positivo_count += 1
                
        pct_clv_positivo = (clv_positivo_count / len(resueltas_todas) * 100) if len(resueltas_todas) > 0 else 0.0

        st.markdown("### 🗓️ Filtrar Resultados")
        meses_disponibles = df_apuestas['fecha_partido'].dt.to_period("M").unique()
        meses_str = [m.strftime("%Y-%m") for m in meses_disponibles]
        meses_str.insert(0, "Histórico Completo")
        mes_seleccionado = st.selectbox("Selecciona el periodo a analizar:", meses_str)
        
        df_filtrado = df_apuestas.copy()
        if mes_seleccionado != "Histórico Completo":
            df_filtrado = df_filtrado[df_filtrado['fecha_partido'].dt.strftime("%Y-%m") == mes_seleccionado]
            
        apuestas_reales = df_filtrado[df_filtrado['tipo_apuesta'] == 'Apuesta Real']
        resueltas = apuestas_reales[apuestas_reales['estado'] != 'Pendiente']
        pendientes = df_apuestas[df_apuestas['estado'] == 'Pendiente'] 
        
        st.markdown(f"#### 📈 Tus Números ({mes_seleccionado})")
        col_m1, col_m2, col_m3, col_m4 = st.columns(4)
        profit_filtrado = resueltas['profit'].sum()
        stake_filtrado = resueltas['stake'].sum()
        yield_pct = (profit_filtrado / stake_filtrado * 100) if stake_filtrado > 0 else 0.0
        ganadas = len(resueltas[resueltas['estado'] == 'Ganada'])
        total_resueltas = len(resueltas[resueltas['estado'].isin(['Ganada', 'Perdida'])])
        win_rate = (ganadas / total_resueltas * 100) if total_resueltas > 0 else 0.0
        
        col_m1.metric("💰 Profit Neto", f"${profit_filtrado:.2f}")
        col_m2.metric("📊 Yield", f"{yield_pct:.2f}%")
        col_m3.metric("🎯 % Acierto", f"{win_rate:.1f}%")
        col_m4.metric("📈 CLV Positivo", f"{pct_clv_positivo:.1f}%", help="Porcentaje de veces que le ganaste al mercado comprando un mejor momio que el momio de cierre.")
        st.markdown("---")
        
        if not resueltas.empty:
            col_graf1, col_graf2 = st.columns(2)
            with col_graf1:
                grafica_df = resueltas.groupby('fecha_partido')['profit'].sum().reset_index()
                grafica_df = grafica_df.sort_values('fecha_partido')
                grafica_df['profit_acumulado'] = grafica_df['profit'].cumsum()
                fig_line = px.area(grafica_df, x='fecha_partido', y='profit_acumulado', title="Evolución de tu Capital", markers=True)
                st.plotly_chart(fig_line, use_container_width=True)
            with col_graf2:
                conteo_estados = resueltas['estado'].value_counts().reset_index()
                conteo_estados.columns = ['estado', 'cantidad']
                colores_estados = {'Ganada': '#00ff00', 'Perdida': '#ff4b4b', 'Anulada (Push)': '#ffa500', 'Cash Out': '#3399ff'}
                fig_pie = px.pie(conteo_estados, values='cantidad', names='estado', title="Distribución de Resultados", color='estado', color_discrete_map=colores_estados)
                st.plotly_chart(fig_pie, use_container_width=True)
        st.markdown("---")
        
        if not pendientes.empty:
            st.subheader("🛠️ Resolver ticket pendiente")
            opciones = pendientes.apply(lambda x: f"ID {x['id']} | Parlay de {len(x['partido'].split(chr(10)))} juegos", axis=1).tolist()
            seleccion = st.selectbox("Elige el ticket:", opciones)
            id_seleccionado = int(seleccion.split(" ")[1])
            
            apuesta_original = pendientes[pendientes['id'] == id_seleccionado].iloc[0]
            partidos_lista = apuesta_original['partido'].split('\n')
            pronosticos_lista = apuesta_original['pronostico'].split('\n')
            momio_original_ticket = int(apuesta_original['momio'])
            stake_orig = apuesta_original['stake']
            
            with st.form("resolver_apuesta_auto"):
                st.info("💡 **Opción Cash Out:** Si retiraste el dinero antes de terminar, marca la casilla inferior.")
                es_cashout = st.checkbox("💸 Hice Cash Out (Retiro Anticipado)")
                monto_cashout = st.number_input("¿Cuánto dinero TOTAL te devolvió la casa?", min_value=0.0, value=float(stake_orig), step=10.0, disabled=not es_cashout)
                
                st.markdown("---")
                diccionario_goles = {}
                diccionario_anulados = {}
                for i, (part, pron) in enumerate(zip(partidos_lista, pronosticos_lista)):
                    equipos = part.split(" vs ")
                    loc_name = equipos[0] if len(equipos) == 2 else "Local"
                    vis_name = equipos[1] if len(equipos) == 2 else "Visita"
                    st.markdown(f"**🏆 {part}** | Pronóstico: {pron.split('->')[-1].strip()}")
                    fue_anulado = st.checkbox("🚫 Anulado", key=f"anulado_{i}")
                    c1, c2 = st.columns(2)
                    with c1: g_loc = st.number_input(f"Goles {loc_name}", min_value=0, step=1, key=f"loc_{i}")
                    with c2: g_vis = st.number_input(f"Goles {vis_name}", min_value=0, step=1, key=f"vis_{i}")
                    diccionario_goles[i] = (g_loc, g_vis)
                    diccionario_anulados[i] = fue_anulado
                
                st.markdown("---")
                col_fin1, col_fin2 = st.columns(2)
                with col_fin1:
                    momio_ajustado = st.number_input("Momio de Cobro Final (Si hubo anulados)", value=momio_original_ticket, step=10)
                with col_fin2:
                    momio_cierre = st.number_input("Momio de Cierre (CLV)", value=momio_original_ticket, step=10, help="El momio que ofrecía el casino justo en el minuto 1 antes de arrancar el último partido. Sirve para ver si ganaste valor.")
                
                if st.form_submit_button("✅ Guardar Resultado"):
                    def calcular_profit_local(stake, momio, estado):
                        if estado == "Perdida": return -stake
                        elif estado == "Anulada (Push)": return 0.0
                        elif estado == "Ganada":
                            if momio > 0: return stake * (momio / 100)
                            elif momio < 0: return stake / (abs(momio) / 100)
                        return 0.0

                    if es_cashout:
                        estado_final = "Cash Out"
                        resultado_final_str = "Retiro Anticipado (Cash Out)"
                        profit_calculado = monto_cashout - stake_orig
                        actualizar_resultado(id_seleccionado, estado_final, resultado_final_str, profit_calculado, momio_original_ticket, momio_cierre)
                        st.success(f"¡Cash Out registrado! Profit: ${profit_calculado:.2f}")
                        st.rerun()
                    else:
                        todas_ganadas = True
                        resultados_texto = []
                        for i, (part, pron) in enumerate(zip(partidos_lista, pronosticos_lista)):
                            if diccionario_anulados[i]: resultados_texto.append(f"{part} (ANULADO)")
                            else:
                                goles_loc, goles_vis = diccionario_goles[i]
                                resultados_texto.append(f"{part} ({goles_loc}-{goles_vis})")
                                if not evaluar_doble_oportunidad(goles_loc, goles_vis, pron): todas_ganadas = False
                        
                        if all(diccionario_anulados.values()): estado_final = "Anulada (Push)"
                        else: estado_final = "Ganada" if todas_ganadas else "Perdida"
                        resultado_final_str = "\n".join(resultados_texto)
                        tipo_orig = apuesta_original['tipo_apuesta']
                        
                        profit_calculado = 0.0 if tipo_orig == "Apuesta Descartada" else calcular_profit_local(stake_orig, momio_ajustado, estado_final)
                        
                        actualizar_resultado(id_seleccionado, estado_final, resultado_final_str, profit_calculado, momio_ajustado, momio_cierre)
                        st.success(f"Ticket autoevaluado como {estado_final.upper()}. Profit: ${profit_calculado:.2f}")
                        st.rerun()

        st.markdown("---")
        st.subheader("🔍 Buscador de Historial")
        col_f1, col_f2 = st.columns([1, 2])
        with col_f1:
            filtro_estado = st.multiselect("Filtrar por Estado:", df_apuestas['estado'].unique(), default=[])
        with col_f2:
            filtro_texto = st.text_input("Buscar por equipo, liga o #etiqueta:")
            
        df_busqueda = df_apuestas.copy()
        if filtro_estado:
            df_busqueda = df_busqueda[df_busqueda['estado'].isin(filtro_estado)]
        if filtro_texto:
            df_busqueda = df_busqueda[
                df_busqueda['partido'].str.contains(filtro_texto, case=False, na=False) | 
                df_busqueda['liga'].str.contains(filtro_texto, case=False, na=False) |
                df_busqueda['etiquetas'].str.contains(filtro_texto, case=False, na=False)
            ]
            
        col_tabla, col_btn = st.columns([4, 1])
        with col_btn:
            csv = df_busqueda.to_csv(index=False).encode('utf-8-sig')
            st.download_button("📥 Exportar Tabla", data=csv, file_name=f"Historial_Filtrado_{datetime.date.today()}.csv", mime="text/csv", use_container_width=True)
            
        columnas_mostrar = ['id', 'fecha_partido', 'partido', 'liga', 'etiquetas', 'momio', 'momio_cierre', 'stake', 'estado', 'profit']
        columnas_reales = [c for c in columnas_mostrar if c in df_busqueda.columns]
        st.dataframe(df_busqueda[columnas_reales], use_container_width=True)

with tab3:
    st.header("Explorador Global de Partidos")
    api_key_usuario = st.text_input("Pega tu API Key aquí:", type="password")
    fecha_buscar = st.date_input("¿Qué día exacto quieres analizar?", datetime.date.today())
    
    if st.button("🔍 Buscar Partidos"):
        if api_key_usuario == "": st.warning("⚠️ Pon tu API Key arriba.")
        else:
            with st.spinner(f'Buscando partidos...'):
                datos = obtener_partidos_y_momios(api_key_usuario, fecha_buscar)
                st.session_state.api_resultados = datos
                st.session_state.fecha_busqueda = fecha_buscar

    if st.session_state.api_resultados is not None and st.session_state.fecha_busqueda == fecha_buscar:
        datos = st.session_state.api_resultados
        if "errors" in datos and datos["errors"]:
            error_api = datos["errors"]
            if isinstance(error_api, dict): error_api = " | ".join([f"{v}" for k, v in error_api.items()])
            st.error(f"⚠️ **Error de la API:** {error_api}")
        elif "response" in datos:
            partidos = datos["response"]
            if len(partidos) > 0:
                st.success(f"¡Se encontraron {len(partidos)} partidos!")
                
                # --- NUEVO: CICLO CON ENUMERATE PARA NUMERAR LOS PARTIDOS ---
                for i, p in enumerate(partidos, start=1):
                    liga, pais = p["league"]["name"], p["league"]["country"]
                    local, visita = p["teams"]["home"]["name"], p["teams"]["away"]["name"]
                    hora, id_partido = p["fixture"]["date"][11:16], p["fixture"]["id"]
                    partido_texto = f"{local} vs {visita}"
                    
                    texto_momios = " | 💵 Momios no disponibles"
                    if "momios_1x2" in p and p["momios_1x2"] and len(p["momios_1x2"]) == 3:
                        vals = p["momios_1x2"]
                        texto_momios = f" | 💵 **L** {decimal_a_americano(vals[0]['odd'])} | **E** {decimal_a_americano(vals[1]['odd'])} | **V** {decimal_a_americano(vals[2]['odd'])}"
                    
                    loc_c, vis_c = colorizar_equipo(local, dict_rendimiento), colorizar_equipo(visita, dict_rendimiento)
                    col_info, col_btn = st.columns([5, 1])
                    with col_info: 
                        # Aquí agregamos el número de partido antes de la bandera
                        st.markdown(f"🔹 **#{i}** | 🌍 {pais} - {liga} | ⏰ {hora} HRS <br> ⚽ <b>{loc_c} vs {vis_c}</b> {texto_momios}", unsafe_allow_html=True)
                    with col_btn: 
                        st.button("➕ Agregar", key=f"btn_{id_partido}", on_click=agregar_al_parlay, args=(partido_texto, liga))
            else:
                st.warning(f"⚠️ No hay partidos programados para el {fecha_buscar}.")