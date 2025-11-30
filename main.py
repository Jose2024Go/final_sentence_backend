from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from models import Jugador, TipoSala
from game_controller import juego  # Instancia única
import asyncio

app = FastAPI(title="Final Sentence API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------ HTTP ENDPOINTS ------------------------

@app.post("/jugador/nuevo")
async def crear_jugador(nombre: str, avatar: str = "default"):
    jugador_id = f"jugador_{abs(hash(nombre)) % (10**8)}"
    jugador = Jugador(id=jugador_id, nombre=nombre, avatar=avatar)
    juego.base_datos.guardar_jugador(jugador)
    return jugador.dict()

@app.get("/jugador/{jugador_id}/estadisticas")
async def obtener_estadisticas(jugador_id: str):
    stats = juego.base_datos.obtener_estadisticas_jugador(jugador_id)
    return stats.dict()

@app.post("/sala/crear")
async def crear_sala(jugador_id: str, tipo: TipoSala, max_jugadores: int = 10):
    jugador = juego.base_datos.obtener_jugador(jugador_id)
    sala = juego.crear_sala(jugador, tipo, max_jugadores)
    return sala.dict()

@app.post("/sala/unir")
async def unir_sala(jugador_id: str, codigo_sala: str):
    jugador = juego.base_datos.obtener_jugador(jugador_id)
    sala = juego.unir_sala(jugador, codigo_sala)
    return sala.dict() if sala else {"error": "Sala no encontrada"}

@app.post("/sala/{sala_id}/iniciar")
async def iniciar_partida(sala_id: str):
    await juego.iniciar_partida(sala_id)
    return {"mensaje": "Partida iniciada"}

