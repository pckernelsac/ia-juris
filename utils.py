import re
import sqlite3
from datetime import datetime
from collections import Counter
import difflib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from config import config
import logging

logger = logging.getLogger(__name__)

class TextAnalyzer:
    """Clase para análisis avanzado de texto legal."""
    
    def __init__(self):
        self.vectorizer = TfidfVectorizer(
            max_features=1000,
            ngram_range=(1, 3),
            stop_words=list(config.STOPWORDS)
        )
        self.vectors = None
        self.sentencias_ids = []
    
    def build_index(self, sentencias):
        """Construye índice de vectores TF-IDF para búsqueda de similitud."""
        if not sentencias:
            return
            
        texts = []
        self.sentencias_ids = []
        
        for sentencia in sentencias:
            # Combinar todos los campos relevantes
            text = f"{sentencia.get('numero_sentencia', '')} {sentencia.get('fundamentos', '')} {sentencia.get('palabras_clave', '')}"
            texts.append(text)
            self.sentencias_ids.append(sentencia['id'])
        
        try:
            self.vectors = self.vectorizer.fit_transform(texts)
            logger.info(f"Índice construido con {len(texts)} sentencias")
        except Exception as e:
            logger.error(f"Error al construir índice: {e}")
    
    def find_similar(self, sentencia_id, threshold=0.3, limit=5):
        """Encuentra sentencias similares basándose en contenido."""
        if self.vectors is None:
            return []
        
        # Verificar si sentencia_id existe en la lista
        if sentencia_id not in self.sentencias_ids:
            return []
        
        try:
            idx = self.sentencias_ids.index(sentencia_id)
            vector = self.vectors[idx:idx+1]  # Mantener como matriz 2D
            
            # Calcular similitudes
            similarities = cosine_similarity(vector, self.vectors).flatten()
            
            # Crear lista de índices con sus similitudes
            similarity_pairs = [(i, sim) for i, sim in enumerate(similarities)]
            
            # Ordenar por similitud (excluyendo la misma sentencia)
            similarity_pairs.sort(key=lambda x: x[1], reverse=True)
            
            results = []
            for i, sim_score in similarity_pairs:
                if i != idx and sim_score >= threshold:
                    results.append({
                        'id': self.sentencias_ids[i],
                        'similarity': float(sim_score)
                    })
                    if len(results) >= limit:
                        break
            
            return results
        except Exception as e:
            logger.error(f"Error al buscar similares: {e}")
            return []
    
    def extract_entities(self, text):
        """Extrae entidades nombradas del texto (versión simplificada)."""
        entities = {
            'personas': [],
            'organizaciones': [],
            'fechas': [],
            'montos': []
        }
        
        # Patrones regex simplificados
        patterns = {
            'personas': r'\b[A-Z][a-z]+ [A-Z][a-z]+(?:\s[A-Z][a-z]+)*\b',
            'organizaciones': r'\b(?:S\.A\.|S\.R\.L\.|E\.I\.R\.L\.|SAC|EIRL|SRL|SA)\b',
            'fechas': r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b',
            'montos': r'S/\.?\s*[\d,]+(?:\.\d{2})?|\$\s*[\d,]+(?:\.\d{2})?'
        }
        
        for entity_type, pattern in patterns.items():
            matches = re.findall(pattern, text)
            entities[entity_type] = list(set(matches))
        
        return entities

class ReportGenerator:
    """Generador de reportes PDF."""
    
    def __init__(self):
        self.styles = getSampleStyleSheet()
        self.custom_styles = {
            'CustomTitle': ParagraphStyle(
                'CustomTitle',
                parent=self.styles['Heading1'],
                fontSize=24,
                textColor=colors.HexColor('#764ba2'),
                spaceAfter=30,
                alignment=TA_CENTER
            ),
            'CustomHeading': ParagraphStyle(
                'CustomHeading',
                parent=self.styles['Heading2'],
                fontSize=16,
                textColor=colors.HexColor('#667eea'),
                spaceAfter=12
            ),
            'CustomBody': ParagraphStyle(
                'CustomBody',
                parent=self.styles['BodyText'],
                fontSize=12,
                alignment=TA_JUSTIFY,
                spaceAfter=12
            )
        }
    
    def generate_sentencia_report(self, sentencia, filename):
        """Genera reporte PDF de una sentencia."""
        doc = SimpleDocTemplate(filename, pagesize=A4)
        story = []
        
        # Título
        story.append(Paragraph("REPORTE DE SENTENCIA", self.custom_styles['CustomTitle']))
        story.append(Spacer(1, 0.5*inch))
        
        # Información básica
        data = [
            ['Número de Sentencia:', sentencia['numero_sentencia']],
            ['Fecha de Publicación:', sentencia['fecha_publicacion']],
            ['Demandante:', sentencia['nombre_demandante']],
            ['Demandado:', sentencia['nombre_demandado']],
            ['Expediente:', sentencia['numero_expediente']]
        ]
        
        table = Table(data, colWidths=[2.5*inch, 4*inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f3f4f6')),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 12),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey)
        ]))
        story.append(table)
        story.append(Spacer(1, 0.5*inch))
        
        # Resumen
        if sentencia.get('resumen'):
            story.append(Paragraph("Resumen", self.custom_styles['CustomHeading']))
            story.append(Paragraph(sentencia['resumen'], self.custom_styles['CustomBody']))
            story.append(Spacer(1, 0.3*inch))
        
        # Palabras clave
        if sentencia.get('palabras_clave'):
            story.append(Paragraph("Palabras Clave", self.custom_styles['CustomHeading']))
            story.append(Paragraph(sentencia['palabras_clave'], self.custom_styles['CustomBody']))
            story.append(Spacer(1, 0.3*inch))
        
        # Fundamentos
        story.append(Paragraph("Fundamentos", self.custom_styles['CustomHeading']))
        if isinstance(sentencia['fundamentos'], list):
            for i, fundamento in enumerate(sentencia['fundamentos'], 1):
                story.append(Paragraph(f"{i}. {fundamento}", self.custom_styles['CustomBody']))
        else:
            story.append(Paragraph(sentencia['fundamentos'], self.custom_styles['CustomBody']))
        
        # Generar PDF
        try:
            doc.build(story)
            return True
        except Exception as e:
            logger.error(f"Error al generar PDF: {e}")
            return False
    
    def generate_comparison_report(self, sentencias, filename):
        """Genera reporte comparativo de múltiples sentencias."""
        doc = SimpleDocTemplate(filename, pagesize=letter)
        story = []
        
        # Título
        story.append(Paragraph("ANÁLISIS COMPARATIVO DE SENTENCIAS", self.custom_styles['CustomTitle']))
        story.append(Spacer(1, 0.5*inch))
        
        # Tabla comparativa
        headers = ['Aspecto', 'Sentencia 1', 'Sentencia 2']
        data = [headers]
        
        aspects = [
            ('Número', 'numero_sentencia'),
            ('Fecha', 'fecha_publicacion'),
            ('Demandante', 'nombre_demandante'),
            ('Demandado', 'nombre_demandado'),
            ('Expediente', 'numero_expediente')
        ]
        
        for aspect_name, aspect_key in aspects:
            row = [aspect_name]
            for sentencia in sentencias[:2]:
                row.append(str(sentencia.get(aspect_key, 'N/A')))
            data.append(row)
        
        table = Table(data, colWidths=[2*inch, 2.5*inch, 2.5*inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#764ba2')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 12),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        story.append(table)
        story.append(PageBreak())
        
        # Análisis de similitudes y diferencias
        story.append(Paragraph("Análisis de Contenido", self.custom_styles['CustomHeading']))
        
        # Comparar fundamentos
        if len(sentencias) >= 2:
            fundamentos1 = ' '.join(sentencias[0].get('fundamentos', []) if isinstance(sentencias[0].get('fundamentos'), list) else [sentencias[0].get('fundamentos', '')])
            fundamentos2 = ' '.join(sentencias[1].get('fundamentos', []) if isinstance(sentencias[1].get('fundamentos'), list) else [sentencias[1].get('fundamentos', '')])
            
            similarity = difflib.SequenceMatcher(None, fundamentos1, fundamentos2).ratio()
            story.append(Paragraph(f"Similitud de contenido: {similarity*100:.1f}%", self.custom_styles['CustomBody']))
        
        # Generar PDF
        try:
            doc.build(story)
            return True
        except Exception as e:
            logger.error(f"Error al generar reporte comparativo: {e}")
            return False

class FavoritesManager:
    """Gestor de sentencias favoritas."""
    
    def __init__(self, db_name=None):
        self.db_name = db_name or config.DATABASE_NAME
        self.init_favorites_table()
    
    def init_favorites_table(self):
        """Crea la tabla de favoritos si no existe."""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS favoritos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sentencia_id INTEGER,
                fecha_agregado TEXT,
                notas TEXT,
                etiquetas TEXT,
                FOREIGN KEY (sentencia_id) REFERENCES sentencias(id),
                UNIQUE(sentencia_id)
            )
        ''')
        conn.commit()
        conn.close()
    
    def add_favorite(self, sentencia_id, notas='', etiquetas=''):
        """Agrega una sentencia a favoritos."""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT INTO favoritos (sentencia_id, fecha_agregado, notas, etiquetas)
                VALUES (?, ?, ?, ?)
            ''', (sentencia_id, datetime.now().isoformat(), notas, etiquetas))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()
    
    def remove_favorite(self, sentencia_id):
        """Elimina una sentencia de favoritos."""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM favoritos WHERE sentencia_id = ?', (sentencia_id,))
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return affected > 0
    
    def get_favorites(self):
        """Obtiene todas las sentencias favoritas con sus datos."""
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT s.*, f.fecha_agregado, f.notas, f.etiquetas
            FROM favoritos f
            JOIN sentencias s ON f.sentencia_id = s.id
            ORDER BY f.fecha_agregado DESC
        ''')
        
        favorites = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return favorites
    
    def is_favorite(self, sentencia_id):
        """Verifica si una sentencia está en favoritos."""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM favoritos WHERE sentencia_id = ?', (sentencia_id,))
        result = cursor.fetchone() is not None
        conn.close()
        return result
    
    def update_notes(self, sentencia_id, notas):
        """Actualiza las notas de una sentencia favorita."""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE favoritos SET notas = ? WHERE sentencia_id = ?
        ''', (notas, sentencia_id))
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return affected > 0

class ComparisonTool:
    """Herramienta para comparar sentencias."""
    
    @staticmethod
    def compare_sentencias(sentencia1, sentencia2):
        """Compara dos sentencias y retorna las diferencias."""
        comparison = {
            'metadata': {},
            'content_similarity': 0,
            'common_keywords': [],
            'unique_keywords': {'sentencia1': [], 'sentencia2': []},
            'fundamentos_diff': []
        }
        
        # Comparar metadata
        fields = ['numero_sentencia', 'fecha_publicacion', 'nombre_demandante', 
                  'nombre_demandado', 'numero_expediente']
        
        for field in fields:
            val1 = sentencia1.get(field, 'N/A')
            val2 = sentencia2.get(field, 'N/A')
            comparison['metadata'][field] = {
                'sentencia1': val1,
                'sentencia2': val2,
                'equal': val1 == val2
            }
        
        # Comparar palabras clave
        keywords1 = set(sentencia1.get('palabras_clave', '').split(', '))
        keywords2 = set(sentencia2.get('palabras_clave', '').split(', '))
        
        comparison['common_keywords'] = list(keywords1 & keywords2)
        comparison['unique_keywords']['sentencia1'] = list(keywords1 - keywords2)
        comparison['unique_keywords']['sentencia2'] = list(keywords2 - keywords1)
        
        # Calcular similitud de contenido
        fundamentos1 = ' '.join(sentencia1.get('fundamentos', []) if isinstance(sentencia1.get('fundamentos'), list) else [sentencia1.get('fundamentos', '')])
        fundamentos2 = ' '.join(sentencia2.get('fundamentos', []) if isinstance(sentencia2.get('fundamentos'), list) else [sentencia2.get('fundamentos', '')])
        
        comparison['content_similarity'] = difflib.SequenceMatcher(
            None, fundamentos1, fundamentos2
        ).ratio()
        
        # Generar diff de fundamentos
        diff = list(difflib.unified_diff(
            fundamentos1.splitlines(),
            fundamentos2.splitlines(),
            lineterm='',
            n=3
        ))
        comparison['fundamentos_diff'] = diff[:50]  # Limitar a 50 líneas
        
        return comparison

# Función auxiliar para limpiar texto legal
def clean_legal_text(text):
    """Limpia y normaliza texto legal."""
    if not text:
        return ""
    
    # Eliminar múltiples espacios
    text = re.sub(r'\s+', ' ', text)
    
    # Eliminar caracteres especiales manteniendo puntuación básica
    text = re.sub(r'[^\w\s\.\,\;\:\-\(\)áéíóúñÁÉÍÓÚÑ]', '', text)
    
    # Normalizar puntuación
    text = re.sub(r'\s+([.,;:])', r'\1', text)
    
    return text.strip()

# Función para generar slug de URL
def generate_slug(text):
    """Genera un slug amigable para URLs."""
    if not text:
        return ""
    
    # Convertir a minúsculas
    slug = text.lower()
    
    # Reemplazar caracteres especiales
    replacements = {
        'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u', 'ñ': 'n',
        'ä': 'a', 'ë': 'e', 'ï': 'i', 'ö': 'o', 'ü': 'u'
    }
    
    for old, new in replacements.items():
        slug = slug.replace(old, new)
    
    # Eliminar caracteres no alfanuméricos
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    
    # Reemplazar espacios con guiones
    slug = re.sub(r'\s+', '-', slug)
    
    # Eliminar guiones múltiples
    slug = re.sub(r'-+', '-', slug)
    
    # Eliminar guiones al inicio y final
    slug = slug.strip('-')
    
    return slug[:100]  # Limitar longitud