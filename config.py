# Configuración del Sistema de Jurisprudencia

class Config:
    # Configuración de la base de datos
    DATABASE_NAME = "data.db"
    
    # Configuración de actualización automática
    UPDATE_INTERVAL = 3600  # segundos (1 hora) - Antes era 10 segundos
    MAX_PAGES_AUTO_UPDATE = 50  # páginas máximas en actualización automática - Aumentado de 15
    MAX_PAGES_MANUAL_UPDATE = 500  # páginas máximas en actualización manual - Aumentado de 100
    
    # Configuración de paginación
    ITEMS_PER_PAGE = 82
    
    # Configuración de caché
    CACHE_TIMEOUT = 300  # segundos (5 minutos)
    
    # Configuración de API externa
    API_URL = "https://jurisbackend.sedetc.gob.pe/api/visitor/sentencia/busqueda"
    API_TIMEOUT = 30  # segundos
    API_DELAY = 1.0  # segundos entre solicitudes - Aumentado de 0.5 para ser más respetuoso
    
    # Headers para las solicitudes
    API_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://jurisprudencia.sedetc.gob.pe/"
    }
    
    # Configuración de exportación
    MAX_EXPORT_RECORDS = 10000
    
    # Palabras clave para excluir del análisis
    STOPWORDS = {
        'el', 'la', 'de', 'en', 'a', 'que', 'y', 'los', 'las', 'del', 
        'al', 'es', 'un', 'una', 'por', 'para', 'con', 'se', 'su', 'le', 
        'lo', 'como', 'más', 'o', 'pero', 'sus', 'les', 'ya', 'este',
        'ese', 'esto', 'eso', 'estos', 'esos', 'esta', 'esa', 'estas',
        'esas', 'aqui', 'ahi', 'alli', 'nos', 'ante', 'sobre', 'todo',
        'también', 'tras', 'otro', 'otra', 'otros', 'otras', 'él', 'ella',
        'ellos', 'ellas', 'si', 'no', 'ni', 'cuando', 'donde', 'quien',
        'cual', 'cuales', 'cuyo', 'cuya', 'cuyos', 'cuyas'
    }
    
    # Configuración de seguridad
    SECRET_KEY = "tu-clave-secreta-aqui-cambiar-en-produccion"
    
    # Configuración de logging
    LOG_LEVEL = "INFO"
    LOG_FILE = "jurisprudencia.log"
    
    # Configuración de desarrollo
    DEBUG = True
    THREADED = True
    
    # Límites de análisis de texto
    MIN_WORD_LENGTH = 4  # longitud mínima de palabras para análisis
    MAX_KEYWORDS = 10  # máximo de palabras clave a extraer
    SUMMARY_LENGTH = 200  # caracteres máximos del resumen
    
    # Configuración de notificaciones
    NOTIFICATION_DURATION = 3000  # milisegundos
    
    # Configuración de interfaz
    DARK_MODE_DEFAULT = False
    ANIMATION_DURATION = 500  # milisegundos

class ProductionConfig(Config):
    DEBUG = False
    UPDATE_INTERVAL = 7200  # 2 horas en producción
    API_DELAY = 1.5  # Más conservador en producción
    
class DevelopmentConfig(Config):
    DEBUG = True
    UPDATE_INTERVAL = 3600  # 1 hora en desarrollo

# Configuración activa
config = DevelopmentConfig()
