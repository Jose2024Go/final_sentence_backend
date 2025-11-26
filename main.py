#final_sentence\backend\main.py
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from models import Jugador, TipoSala, MensajeWebSocket
from game_manager import AdministradorJuego
import asyncio

app = FastAPI(title="Final Silencio API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

administrador_juego = AdministradorJuego()

@app.post("/jugador/nuevo")
async def crear_jugador(nombre: str, avatar: str = "default"):
    jugador_id = f"jugador_{id(nombre)}"
    jugador = Jugador(id=jugador_id, nombre=nombre, avatar=avatar)
    administrador_juego.base_datos.guardar_jugador(jugador)
    return jugador.dict()

@app.get("/jugador/{jugador_id}/estadisticas")
async def obtener_estadisticas(jugador_id: str):
    stats = administrador_juego.base_datos.obtener_estadisticas_jugador(jugador_id)
    return stats.dict()

@app.post("/sala/crear")
async def crear_sala(jugador_id: str, tipo: TipoSala, max_jugadores: int = 10):
    jugador = administrador_juego.base_datos.obtener_jugador(jugador_id)
    if not jugador:
        raise HTTPException(status_code=404, detail="Jugador no encontrado")
    sala = administrador_juego.crear_sala(jugador, tipo, max_jugadores)
    return sala.dict()

@app.post("/sala/unir")
async def unir_sala(jugador_id: str, codigo_sala: str):
    jugador = administrador_juego.base_datos.obtener_jugador(jugador_id)
    if not jugador:
        raise HTTPException(status_code=404, detail="Jugador no encontrado")
    sala = administrador_juego.unir_sala(jugador, codigo_sala)
    if not sala:
        raise HTTPException(status_code=404, detail="Sala no encontrada o llena")
    return sala.dict()

@app.post("/sala/{sala_id}/iniciar")
async def iniciar_partida(sala_id: str):
    await administrador_juego.iniciar_partida(sala_id)
    return {"mensaje": "Partida iniciada"}

# backend/main.py (fragmento WebSocket actualizado)
@app.websocket("/ws/{sala_id}/{jugador_id}")
async def websocket_sala(websocket: WebSocket, sala_id: str, jugador_id: str):
    await websocket.accept()
    if sala_id not in administrador_juego.conexiones:
        administrador_juego.conexiones[sala_id] = []
    administrador_juego.conexiones[sala_id].append(websocket)
    try:
        while True:
            datos = await websocket.receive_json()
            mensaje = MensajeWebSocket(**datos)
            if mensaje.tipo == "escritura":
                await administrador_juego.procesar_escritura(
                    mensaje.jugador_id,
                    sala_id,
                    mensaje.datos["texto"],
                    mensaje.datos["tiempo_tomado"]
                )
            elif mensaje.tipo == "abandonar":
                administrador_juego.abandonar_sala(mensaje.jugador_id, sala_id)
                break
    except WebSocketDisconnect:
        administrador_juego.abandonar_sala(jugador_id, sala_id)
        if sala_id in administrador_juego.conexiones:
            try:
                administrador_juego.conexiones[sala_id].remove(websocket)
            except ValueError:
                pass
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)