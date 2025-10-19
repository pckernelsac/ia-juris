import sqlite3
import requests
from flask import Flask, render_template, jsonify, request, send_file, Response
from datetime import datetime, timedelta
import json
import csv
import io
import threading
import time
from collections import Counter
import re
from functools import lru_cache
import logging
from config import config
from utils import TextAnalyzer, ReportGenerator, FavoritesManager, ComparisonTool, clean_legal_text
import tempfile
import os

# Configuración de logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(config.LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = config.SECRET_KEY

# Variables globales
last_update = None
update_thread = None
cache = {}
text_analyzer = TextAnalyzer()
report_generator = ReportGenerator()
favorites_manager = FavoritesManager()

def init_db():
    """Inicializa la base de datos con tablas mejoradas."""
    conn = sqlite3.connect(config.DATABASE_NAME)
    cursor = conn.cursor()
    
    # Tabla principal de sentencias
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sentencias (
            id INTEGER PRIMARY KEY,
            numero_sentencia TEXT UNIQUE,
            fecha_publicacion TEXT,
            nombre_demandante TEXT,
            nombre_demandado TEXT,
            numero_expediente TEXT,
            fundamentos TEXT,
            url_archivo TEXT,
            fecha_scraping TEXT,
            palabras_clave TEXT,
            resumen TEXT
        )
    ''')
    
    # Tabla de estadísticas
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS estadisticas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT,
            total_sentencias INTEGER,
            nuevas_sentencias INTEGER,
            ultima_actualizacion TEXT
        )
    ''')
    
    # Tabla de búsquedas frecuentes
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS busquedas_frecuentes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            termino TEXT UNIQUE,
            frecuencia INTEGER DEFAULT 1,
            ultima_busqueda TEXT
        )
    ''')
    
    # Índices para mejorar rendimiento
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_fecha ON sentencias(fecha_publicacion)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_demandante ON sentencias(nombre_demandante)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_demandado ON sentencias(nombre_demandado)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_expediente ON sentencias(numero_expediente)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_palabras ON sentencias(palabras_clave)')
    
    conn.commit()
    conn.close()
    logger.info("Base de datos inicializada correctamente")

def extract_keywords(fundamentos):
    """Extrae palabras clave de los fundamentos."""
    if isinstance(fundamentos, list):
        texto = ' '.join(fundamentos)
    else:
        texto = fundamentos
    
    # Extraer palabras relevantes
    palabras = re.findall(r'\b[a-záéíóúñ]{' + str(config.MIN_WORD_LENGTH) + ',}\b', texto.lower())
    palabras_filtradas = [p for p in palabras if p not in config.STOPWORDS]
    
    # Contar frecuencia
    contador = Counter(palabras_filtradas)
    
    # Retornar las palabras más comunes
    palabras_clave = [palabra for palabra, _ in contador.most_common(config.MAX_KEYWORDS)]
    return ', '.join(palabras_clave)

def generate_summary(fundamentos):
    """Genera un resumen de los fundamentos."""
    if isinstance(fundamentos, list):
        texto = ' '.join(fundamentos[:3])  # Primeros 3 fundamentos
    else:
        texto = fundamentos
    
    # Limpiar texto
    texto = re.sub(r'\s+', ' ', texto).strip()
    
    # Limitar longitud
    if len(texto) > config.SUMMARY_LENGTH:
        texto = texto[:config.SUMMARY_LENGTH-3] + '...'
    
    return texto

def save_to_db(data):
    """Guarda las sentencias en la base de datos con información adicional."""
    conn = sqlite3.connect(config.DATABASE_NAME)
    cursor = conn.cursor()
    
    nuevas = 0
    actualizadas = 0
    fecha_actual = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    for item in data:
        fundamentos_texto = '\n'.join(item['fundamentos']) if isinstance(item['fundamentos'], list) else item['fundamentos']
        palabras_clave = extract_keywords(item['fundamentos'])
        resumen = generate_summary(item['fundamentos'])
        
        try:
            cursor.execute('''
                INSERT INTO sentencias (
                    id, numero_sentencia, fecha_publicacion, nombre_demandante,
                    nombre_demandado, numero_expediente, fundamentos, url_archivo,
                    fecha_scraping, palabras_clave, resumen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                item['id'], item['numero_sentencia'], item['fecha_publicacion'],
                item['nombre_demandante'], item['nombre_demandado'],
                item['numero_expediente'], fundamentos_texto,
                item['url_archivo'], fecha_actual, palabras_clave, resumen
            ))
            nuevas += 1
        except sqlite3.IntegrityError:
            # Actualizar si ya existe
            cursor.execute('''
                UPDATE sentencias 
                SET fecha_publicacion = ?, nombre_demandante = ?, nombre_demandado = ?,
                    numero_expediente = ?, fundamentos = ?, url_archivo = ?,
                    fecha_scraping = ?, palabras_clave = ?, resumen = ?
                WHERE numero_sentencia = ?
            ''', (
                item['fecha_publicacion'], item['nombre_demandante'], 
                item['nombre_demandado'], item['numero_expediente'],
                fundamentos_texto, item['url_archivo'], fecha_actual,
                palabras_clave, resumen, item['numero_sentencia']
            ))
            if cursor.rowcount > 0:
                actualizadas += 1
    
    # Actualizar estadísticas
    cursor.execute('SELECT COUNT(*) FROM sentencias')
    total = cursor.fetchone()[0]
    
    cursor.execute('''
        INSERT INTO estadisticas (fecha, total_sentencias, nuevas_sentencias, ultima_actualizacion)
        VALUES (?, ?, ?, ?)
    ''', (fecha_actual, total, nuevas, fecha_actual))
    
    conn.commit()
    conn.close()
    
    logger.info(f"Guardadas {nuevas} nuevas sentencias, {actualizadas} actualizadas")
    return nuevas

def fetch_data(api_url=None, start_page=1, max_pages_fetch=None, stop_date_str=None):
    """
    Realiza solicitud GET a la API con manejo mejorado de errores y reintentos.
    
    :param api_url: URL base de la API.
    :param start_page: Página desde la cual comenzar a obtener datos.
    :param max_pages_fetch: Número máximo de páginas a obtener en esta ejecución.
    :param stop_date_str: Opcional. Fecha de corte en formato 'YYYY-MM-DD'.
    """
    if api_url is None:
        api_url = config.API_URL

    all_data = []
    page = start_page
    pages_processed = 0
    
    stop_date = None
    if stop_date_str:
        try:
            stop_date = datetime.strptime(stop_date_str, '%Y-%m-%d').date()
        except ValueError:
            logger.error(f"Formato de fecha inválido para stop_date_str: {stop_date_str}. Debe ser YYYY-MM-DD.")
            stop_date = None

    while True:
        if max_pages_fetch is not None and pages_processed >= max_pages_fetch:
            logger.info(f"Alcanzado límite de páginas para esta ejecución: {max_pages_fetch}")
            break

        # --- INICIO DE LA LÓGICA DE REINTENTOS ---
        retries = 3
        delay = 5  # Empezar con una espera de 5 segundos
        for i in range(retries):
            try:
                logger.info(f"Obteniendo página {page} (Intento {i+1}/{retries})")
                response = requests.get(
                    f"{api_url}?page={page}", 
                    headers=config.API_HEADERS, 
                    timeout=config.API_TIMEOUT
                )
                
                # Si el servidor nos pide que esperemos (Error 429)
                if response.status_code == 429:
                    logger.warning(f"Error 429: Límite de solicitudes alcanzado. Esperando {delay} segundos para reintentar.")
                    time.sleep(delay)
                    delay *= 2  # Duplicar la espera para el siguiente reintento
                    continue # Vuelve a intentar en el bucle 'for'

                # Si el servidor tiene otros problemas temporales
                if response.status_code >= 500:
                    logger.warning(f"Error del servidor ({response.status_code}). Esperando {delay} segundos para reintentar.")
                    time.sleep(delay)
                    delay *= 2
                    continue

                # Si hay otro error del cliente o el intento fue exitoso, salimos del bucle de reintentos
                break

            except requests.exceptions.RequestException as e:
                logger.error(f"Error de red al obtener página {page}: {e}. Reintentando en {delay} segundos...")
                time.sleep(delay)
                delay *= 2
        else:
            # Si el bucle 'for' de reintentos termina sin éxito
            logger.error(f"No se pudo obtener la página {page} después de {retries} intentos. Saltando a la siguiente página.")
            page += 1
            pages_processed += 1
            continue # Pasa a la siguiente página en el bucle 'while'
        # --- FIN DE LA LÓGICA DE REINTENTOS ---

        # Procesar la respuesta si fue exitosa
        try:
            if response.status_code == 200:
                data = response.json()
                if not data.get('error') and 'data' in data and data['data']:
                    stop_fetching = False
                    for item in data['data']:
                        source = item.get('_source', {})
                        if stop_date:
                            item_date_str = source.get('fecha_publicacion')
                            if item_date_str and item_date_str != 'N/A':
                                try:
                                    item_date = datetime.strptime(item_date_str, '%Y-%m-%d').date()
                                    if item_date < stop_date:
                                        logger.info(f"Se alcanzó un registro ({item_date_str}) más antiguo que la fecha de corte ({stop_date_str}). Deteniendo la descarga.")
                                        stop_fetching = True
                                        break
                                except ValueError:
                                    pass
                        all_data.append({
                            'id': source.get('id'),
                            'numero_sentencia': source.get('numero_sentencia', 'N/A'),
                            'fecha_publicacion': source.get('fecha_publicacion', 'N/A'),
                            'nombre_demandante': source.get('nombre_demandante', 'N/A'),
                            'nombre_demandado': source.get('nombre_demandado', 'N/A'),
                            'numero_expediente': source.get('numero_expediente', 'N/A'),
                            'fundamentos': source.get('fundamentos', []),
                            'url_archivo': source.get('url_archivo', 'N/A'),
                        })
                    if stop_fetching:
                        break
                    pagination = data.get('pagination', {})
                    if page >= pagination.get('num_pages', page):
                        logger.info("Se obtuvieron todas las páginas disponibles de la API.")
                        break
                else:
                    logger.warning(f"Respuesta de API sin datos o con error en página {page}: {data.get('message', 'Sin mensaje')}")
                    break
            else:
                logger.error(f"Error HTTP {response.status_code} no manejado al obtener la página {page}. Contenido: {response.text}")
                break
        except Exception as e:
            logger.error(f"Error inesperado al procesar la página {page}: {e}")
            break
        
        page += 1
        pages_processed += 1
        time.sleep(config.API_DELAY)
    
    logger.info(f"Total de registros obtenidos en esta ejecución: {len(all_data)}")
    return all_data

def background_update():
    """Actualización automática en segundo plano."""
    global last_update
    while True:
        try:
            logger.info("Iniciando actualización automática")
            data = fetch_data(max_pages_fetch=config.MAX_PAGES_AUTO_UPDATE)
            if data:
                nuevas = save_to_db(data)
                last_update = datetime.now()
                logger.info(f"Actualización completada: {nuevas} nuevas sentencias")
        except Exception as e:
            logger.error(f"Error en actualización automática: {e}")
        
        time.sleep(config.UPDATE_INTERVAL)

@app.route("/")
def index():
    """Ruta principal con interfaz mejorada."""
    return render_template("index.html")

@app.route("/api/sentencias")
def api_sentencias():
    """API REST para obtener sentencias con filtros y paginación."""
    # Obtener parámetros
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', config.ITEMS_PER_PAGE))
    search = request.args.get('search', '').strip()
    fecha_desde = request.args.get('fecha_desde', '')
    fecha_hasta = request.args.get('fecha_hasta', '')
    ordenar = request.args.get('ordenar', 'fecha_publicacion DESC')
    
    # Validar orden para evitar SQL injection
    ordenes_validos = {
        'fecha_publicacion DESC': 'fecha_publicacion DESC',
        'fecha_publicacion ASC': 'fecha_publicacion ASC',
        'numero_sentencia ASC': 'numero_sentencia ASC',
        'numero_sentencia DESC': 'numero_sentencia DESC',
        'nombre_demandante ASC': 'nombre_demandante ASC',
        'nombre_demandante DESC': 'nombre_demandante DESC'
    }
    ordenar = ordenes_validos.get(ordenar, 'fecha_publicacion DESC')
    
    conn = sqlite3.connect(config.DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Construir query con filtros
    query = "SELECT * FROM sentencias WHERE 1=1"
    params = []
    
    if search:
        query += """ AND (numero_sentencia LIKE ? OR nombre_demandante LIKE ? 
                    OR nombre_demandado LIKE ? OR numero_expediente LIKE ? 
                    OR fundamentos LIKE ? OR palabras_clave LIKE ?)"""
        search_param = f"%{search}%"
        params.extend([search_param] * 6)
        
        # Registrar búsqueda
        try:
            cursor.execute('''
                INSERT INTO busquedas_frecuentes (termino, ultima_busqueda)
                VALUES (?, ?)
                ON CONFLICT(termino) DO UPDATE SET
                frecuencia = frecuencia + 1,
                ultima_busqueda = ?
            ''', (search, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 
                  datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            conn.commit()
        except Exception as e:
            logger.error(f"Error al registrar búsqueda: {e}")
    
    if fecha_desde:
        query += " AND fecha_publicacion >= ?"
        params.append(fecha_desde)
    
    if fecha_hasta:
        query += " AND fecha_publicacion <= ?"
        params.append(fecha_hasta)
    
    # Contar total
    count_query = query.replace("SELECT *", "SELECT COUNT(*)")
    cursor.execute(count_query, params)
    total = cursor.fetchone()[0]
    
    # Aplicar orden y paginación
    query += f" ORDER BY {ordenar} LIMIT ? OFFSET ?"
    params.extend([per_page, (page - 1) * per_page])
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    
    sentencias = []
    for row in rows:
        sentencia = dict(row)
        sentencia['fundamentos'] = sentencia['fundamentos'].split('\n') if sentencia['fundamentos'] else []
        sentencias.append(sentencia)
    
    conn.close()
    
    # Limpiar caché si es necesario
    global cache
    cache_key = f"sentencias_{page}_{per_page}_{search}_{fecha_desde}_{fecha_hasta}_{ordenar}"
    
    return jsonify({
        'sentencias': sentencias,
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': (total + per_page - 1) // per_page if per_page > 0 else 0
    })

@app.route("/api/estadisticas")
def api_estadisticas():
    """API para obtener estadísticas del sistema."""
    conn = sqlite3.connect(config.DATABASE_NAME)
    cursor = conn.cursor()
    
    try:
        # Estadísticas generales
        cursor.execute("SELECT COUNT(*) FROM sentencias")
        total_sentencias = cursor.fetchone()[0]
        
        # Sentencias por fecha (últimos 30 días)
        cursor.execute("""
            SELECT fecha_publicacion, COUNT(*) as cantidad 
            FROM sentencias 
            WHERE fecha_publicacion >= date('now', '-30 days')
            GROUP BY fecha_publicacion 
            ORDER BY fecha_publicacion DESC
        """)
        sentencias_por_fecha = cursor.fetchall()
        
        # Palabras clave más comunes
        cursor.execute("""
            SELECT palabras_clave FROM sentencias 
            WHERE palabras_clave IS NOT NULL AND palabras_clave != ''
            LIMIT 1000
        """)
        todas_palabras = []
        for row in cursor.fetchall():
            if row[0]:
                todas_palabras.extend(row[0].split(', '))
        
        palabras_contador = Counter(todas_palabras)
        top_palabras = palabras_contador.most_common(20)
        
        # Búsquedas frecuentes
        cursor.execute("""
            SELECT termino, frecuencia 
            FROM busquedas_frecuentes 
            ORDER BY frecuencia DESC 
            LIMIT 10
        """)
        busquedas_frecuentes = cursor.fetchall()
        
        # Última actualización
        cursor.execute("""
            SELECT ultima_actualizacion, nuevas_sentencias 
            FROM estadisticas 
            ORDER BY id DESC 
            LIMIT 1
        """)
        ultima_actualizacion = cursor.fetchone()
        
        # Estadísticas por mes
        cursor.execute("""
            SELECT strftime('%Y-%m', fecha_publicacion) as mes, COUNT(*) as cantidad
            FROM sentencias
            WHERE fecha_publicacion IS NOT NULL
            GROUP BY mes
            ORDER BY mes DESC
            LIMIT 12
        """)
        sentencias_por_mes = cursor.fetchall()
        
    except Exception as e:
        logger.error(f"Error al obtener estadísticas: {e}")
        return jsonify({'error': 'Error al obtener estadísticas'}), 500
    finally:
        conn.close()
    
    return jsonify({
        'total_sentencias': total_sentencias,
        'sentencias_por_fecha': sentencias_por_fecha,
        'sentencias_por_mes': sentencias_por_mes,
        'top_palabras': top_palabras,
        'busquedas_frecuentes': busquedas_frecuentes,
        'ultima_actualizacion': ultima_actualizacion,
        'estado_sistema': 'activo' if update_thread and update_thread.is_alive() else 'pausado'
    })

@app.route("/api/exportar/<formato>")
def exportar(formato):
    """Exportar datos en diferentes formatos."""
    if formato not in ['csv', 'json']:
        return jsonify({'error': 'Formato no soportado'}), 400
        
    search = request.args.get('search', '')
    
    conn = sqlite3.connect(config.DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    query = "SELECT * FROM sentencias"
    params = []
    
    if search:
        query += """ WHERE numero_sentencia LIKE ? OR nombre_demandante LIKE ? 
                    OR nombre_demandado LIKE ? OR numero_expediente LIKE ?"""
        search_param = f"%{search}%"
        params = [search_param] * 4
    
    query += f" LIMIT {config.MAX_EXPORT_RECORDS}"
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    
    if formato == 'csv':
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Encabezados
        writer.writerow(['ID', 'Número Sentencia', 'Fecha', 'Demandante', 
                        'Demandado', 'Expediente', 'URL', 'Palabras Clave', 'Resumen'])
        
        for row in rows:
            writer.writerow([
                row['id'], row['numero_sentencia'], row['fecha_publicacion'],
                row['nombre_demandante'], row['nombre_demandado'],
                row['numero_expediente'], row['url_archivo'], 
                row['palabras_clave'], row['resumen']
            ])
        
        output.seek(0)
        conn.close()
        
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={
                'Content-Disposition': f'attachment;filename=sentencias_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
            }
        )
    
    elif formato == 'json':
        sentencias = []
        for row in rows:
            sentencia = dict(row)
            sentencia['fundamentos'] = sentencia['fundamentos'].split('\n') if sentencia['fundamentos'] else []
            sentencias.append(sentencia)
        
        conn.close()
        
        return Response(
            json.dumps(sentencias, indent=2, ensure_ascii=False),
            mimetype='application/json',
            headers={
                'Content-Disposition': f'attachment;filename=sentencias_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
            }
        )

@app.route("/api/actualizar", methods=['POST'])
def actualizar_manual():
    """Endpoint para actualización manual de datos."""
    try:
        logger.info("Iniciando actualización manual")
        data = fetch_data(max_pages_fetch=config.MAX_PAGES_MANUAL_UPDATE)
        if data:
            nuevas = save_to_db(data)
            return jsonify({
                'success': True,
                'nuevas_sentencias': nuevas,
                'mensaje': f'Se agregaron {nuevas} nuevas sentencias'
            })
        return jsonify({
            'success': True,
            'nuevas_sentencias': 0,
            'mensaje': 'No se encontraron nuevas sentencias.'
        })
    except Exception as e:
        logger.error(f"Error en actualización manual: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route("/api/detalle/<int:sentencia_id>")
def detalle_sentencia(sentencia_id):
    """Obtener detalles completos de una sentencia."""
    conn = sqlite3.connect(config.DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM sentencias WHERE id = ?", (sentencia_id,))
    row = cursor.fetchone()
    
    if row:
        sentencia = dict(row)
        sentencia['fundamentos'] = sentencia['fundamentos'].split('\n') if sentencia['fundamentos'] else []
        conn.close()
        return jsonify(sentencia)
    
    conn.close()
    return jsonify({'error': 'Sentencia no encontrada'}), 404

@app.route("/api/health")
def health_check():
    """Endpoint para verificar el estado del sistema."""
    try:
        # Verificar base de datos
        conn = sqlite3.connect(config.DATABASE_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sentencias")
        total = cursor.fetchone()[0]
        conn.close()
        
        # Estado del sistema
        status = {
            'status': 'healthy',
            'database': 'connected',
            'total_records': total,
            'update_thread': 'running' if update_thread and update_thread.is_alive() else 'stopped',
            'last_update': last_update.isoformat() if last_update else None,
            'version': '2.0.0'
        }
        
        return jsonify(status)
    except Exception as e:
        logger.error(f"Error en health check: {e}")
        return jsonify({
            'status': 'unhealthy',
            'error': str(e)
        }), 500

@app.route("/api/sentencias/similares/<int:sentencia_id>")
def sentencias_similares(sentencia_id):
    """Encuentra sentencias similares a una dada."""
    try:
        # Reconstruir índice si es necesario
        if not text_analyzer.vectors:
            conn = sqlite3.connect(config.DATABASE_NAME)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM sentencias")
            sentencias = [dict(row) for row in cursor.fetchall()]
            conn.close()
            text_analyzer.build_index(sentencias)
        
        # Buscar similares
        similares = text_analyzer.find_similar(sentencia_id)
        
        # Obtener detalles de las sentencias similares
        if similares:
            conn = sqlite3.connect(config.DATABASE_NAME)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            ids = [s['id'] for s in similares]
            placeholders = ','.join('?' * len(ids))
            cursor.execute(f"SELECT * FROM sentencias WHERE id IN ({placeholders})", ids)
            
            sentencias_similares = []
            for row in cursor.fetchall():
                sentencia = dict(row)
                # Agregar score de similitud
                for s in similares:
                    if s['id'] == sentencia['id']:
                        sentencia['similarity_score'] = s['similarity']
                        break
                sentencias_similares.append(sentencia)
            
            conn.close()
            return jsonify(sentencias_similares)
        
        return jsonify([])
        
    except Exception as e:
        logger.error(f"Error al buscar sentencias similares: {e}")
        return jsonify({'error': str(e)}), 500

@app.route("/api/reporte/sentencia/<int:sentencia_id>")
def generar_reporte_sentencia(sentencia_id):
    """Genera reporte PDF de una sentencia."""
    try:
        # Obtener datos de la sentencia
        conn = sqlite3.connect(config.DATABASE_NAME)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sentencias WHERE id = ?", (sentencia_id,))
        row = cursor.fetchone()
        
        if not row:
            conn.close()
            return jsonify({'error': 'Sentencia no encontrada'}), 404
        
        sentencia = dict(row)
        sentencia['fundamentos'] = sentencia['fundamentos'].split('\n') if sentencia['fundamentos'] else []
        conn.close()
        
        # Generar PDF
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
            if report_generator.generate_sentencia_report(sentencia, tmp_file.name):
                return send_file(
                    tmp_file.name,
                    mimetype='application/pdf',
                    as_attachment=True,
                    download_name=f'sentencia_{sentencia["numero_sentencia"]}.pdf'
                )
        
        return jsonify({'error': 'Error al generar reporte'}), 500
        
    except Exception as e:
        logger.error(f"Error al generar reporte: {e}")
        return jsonify({'error': str(e)}), 500

@app.route("/api/comparar", methods=['POST'])
def comparar_sentencias():
    """Compara dos sentencias."""
    try:
        data = request.get_json()
        ids = data.get('ids', [])
        
        if len(ids) != 2:
            return jsonify({'error': 'Se requieren exactamente 2 IDs de sentencias'}), 400
        
        # Obtener sentencias
        conn = sqlite3.connect(config.DATABASE_NAME)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        sentencias = []
        for id in ids:
            cursor.execute("SELECT * FROM sentencias WHERE id = ?", (id,))
            row = cursor.fetchone()
            if row:
                sentencia = dict(row)
                sentencia['fundamentos'] = sentencia['fundamentos'].split('\n') if sentencia['fundamentos'] else []
                sentencias.append(sentencia)
        
        conn.close()
        
        if len(sentencias) != 2:
            return jsonify({'error': 'Una o más sentencias no encontradas'}), 404
        
        # Realizar comparación
        comparison = ComparisonTool.compare_sentencias(sentencias[0], sentencias[1])
        
        # Si se solicita PDF
        if data.get('format') == 'pdf':
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
                if report_generator.generate_comparison_report(sentencias, tmp_file.name):
                    return send_file(
                        tmp_file.name,
                        mimetype='application/pdf',
                        as_attachment=True,
                        download_name='comparacion_sentencias.pdf'
                    )
        
        return jsonify(comparison)
        
    except Exception as e:
        logger.error(f"Error al comparar sentencias: {e}")
        return jsonify({'error': str(e)}), 500

@app.route("/api/favoritos", methods=['GET', 'POST', 'DELETE'])
def gestionar_favoritos():
    """Gestiona sentencias favoritas."""
    try:
        if request.method == 'GET':
            # Obtener todos los favoritos
            favoritos = favorites_manager.get_favorites()
            return jsonify(favoritos)
        
        elif request.method == 'POST':
            # Agregar a favoritos
            data = request.get_json()
            sentencia_id = data.get('sentencia_id')
            notas = data.get('notas', '')
            etiquetas = data.get('etiquetas', '')
            
            if not sentencia_id:
                return jsonify({'error': 'ID de sentencia requerido'}), 400
            
            if favorites_manager.add_favorite(sentencia_id, notas, etiquetas):
                return jsonify({'success': True, 'message': 'Agregado a favoritos'})
            else:
                return jsonify({'error': 'La sentencia ya está en favoritos'}), 400
        
        elif request.method == 'DELETE':
            # Eliminar de favoritos
            sentencia_id = request.args.get('sentencia_id')
            
            if not sentencia_id:
                return jsonify({'error': 'ID de sentencia requerido'}), 400
            
            if favorites_manager.remove_favorite(int(sentencia_id)):
                return jsonify({'success': True, 'message': 'Eliminado de favoritos'})
            else:
                return jsonify({'error': 'Sentencia no encontrada en favoritos'}), 404
                
    except Exception as e:
        logger.error(f"Error en gestión de favoritos: {e}")
        return jsonify({'error': str(e)}), 500

@app.route("/api/favoritos/<int:sentencia_id>/notas", methods=['PUT'])
def actualizar_notas_favorito(sentencia_id):
    """Actualiza las notas de una sentencia favorita."""
    try:
        data = request.get_json()
        notas = data.get('notas', '')
        
        if favorites_manager.update_notes(sentencia_id, notas):
            return jsonify({'success': True, 'message': 'Notas actualizadas'})
        else:
            return jsonify({'error': 'Sentencia no encontrada en favoritos'}), 404
            
    except Exception as e:
        logger.error(f"Error al actualizar notas: {e}")
        return jsonify({'error': str(e)}), 500

@app.route("/api/favoritos/check/<int:sentencia_id>")
def check_favorito(sentencia_id):
    """Verifica si una sentencia está en favoritos."""
    try:
        is_favorite = favorites_manager.is_favorite(sentencia_id)
        return jsonify({'is_favorite': is_favorite})
    except Exception as e:
        logger.error(f"Error al verificar favorito: {e}")
        return jsonify({'error': str(e)}), 500

@app.route("/api/analisis/entidades", methods=['POST'])
def analizar_entidades():
    """Extrae entidades de un texto."""
    try:
        data = request.get_json()
        texto = data.get('texto', '')
        
        if not texto:
            return jsonify({'error': 'Texto requerido'}), 400
        
        entidades = text_analyzer.extract_entities(texto)
        return jsonify(entidades)
        
    except Exception as e:
        logger.error(f"Error al analizar entidades: {e}")
        return jsonify({'error': str(e)}), 500

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Recurso no encontrado'}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Error interno del servidor: {error}")
    return jsonify({'error': 'Error interno del servidor'}), 500

# El bloque __main__ es crucial. Evita que el servidor se inicie
# cuando este archivo es importado por otros scripts (como bulk_downloader.py).
if __name__ == "__main__":
    init_db()
    
    # Cargar datos iniciales si la base está vacía
    conn = sqlite3.connect(config.DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM sentencias")
    count = cursor.fetchone()[0]
    conn.close()
    
    if count == 0:
        logger.info("Base de datos vacía, cargando datos iniciales...")
        initial_data = fetch_data(max_pages_fetch=3)
        if initial_data:
            save_to_db(initial_data)
    
    # Iniciar actualización automática en segundo plano
    update_thread = threading.Thread(target=background_update, daemon=True)
    update_thread.start()
    logger.info("Thread de actualización automática iniciado")
    
    # Iniciar aplicación
    logger.info("Iniciando aplicación Flask")
    app.run(debug=config.DEBUG, threaded=config.THREADED, host='0.0.0.0', port=5000)
