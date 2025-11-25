#final_sentence\backend\models.py
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum

class EstadoJugador(str, Enum):
    CONECTADO = "conectado"
    ESPERANDO = "esperando"
    JUGANDO = "jugando"
    ELIMINADO = "eliminado"
    GANADOR = "ganador"

class TipoSala(str, Enum):
    PUBLICA = "publica"
    PRIVADA = "privada"

class Jugador(BaseModel):
    id: str
    nombre: str
    avatar: str
    estado: EstadoJugador = EstadoJugador.CONECTADO
    errores: int = 0
    ppm: float = 0.0
    progreso: float = 0.0
    conectado: bool = True

class Frase(BaseModel):
    id: str
    texto: str
    dificultad: str
    categoria: str

class Sala(BaseModel):
    id: str
    codigo: str
    tipo: TipoSala
    jugadores: List[Jugador]
    jugador_anfitrion: str
    max_jugadores: int
    estado: str = "esperando"
    ronda_actual: int = 0
    frase_actual: Optional[Frase] = None
    tiempo_inicio: Optional[datetime] = None
    tiempo_limite: int = 45

class Partida(BaseModel):
    id: str
    sala_id: str
    jugadores: List[Jugador]
    frases_usadas: List[Frase]
    ganador: Optional[str] = None
    duracion: int = 0
    fecha: datetime

class EstadisticasJugador(BaseModel):
    jugador_id: str
    nombre: str
    partidas_jugadas: int = 0
    partidas_ganadas: int = 0
    ppm_promedio: float = 0.0
    mejor_ppm: float = 0.0
    total_errores: int = 0
    racha_victorias: int = 0

class MensajeWebSocket(BaseModel):
    tipo: str
    datos: Dict[str, Any]
    jugador_id: str