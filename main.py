# backend/main.py
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
    jugador_id = f"jugador_{abs(hash(nombre)) % (10**8)}"
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

@app.websocket("/ws/{sala_id}/{jugador_id}")
async def websocket_sala(websocket: WebSocket, sala_id: str, jugador_id: str):
    await websocket.accept()

    # Asegurarnos de tener la sala en memoria (si existe en BDD)
    if sala_id not in administrador_juego.salas_activas:
        administrador_juego.cargar_sala_desde_bd(sala_id)

    # Registrar lista de conexiones para la sala
    administrador_juego.conexiones.setdefault(sala_id, [])
    administrador_juego.conexiones[sala_id].append(websocket)

    # Enviar estado inicial de sala (si existe)
    await administrador_juego.enviar_estado_sala(sala_id)

    try:
        while True:
            datos = await websocket.receive_json()
            # Normalizar: MensajeWebSocket requiere tipo, datos, jugador_id
            try:
                mensaje = MensajeWebSocket(**datos)
            except Exception:
                # si la estructura no coincide, intentar normalizar mínimamente
                tipo = datos.get("tipo")
                datos_payload = datos.get("datos", {})
                jugador_id_payload = datos.get("jugador_id", jugador_id)
                mensaje = MensajeWebSocket(tipo=tipo, datos=datos_payload, jugador_id=jugador_id_payload)

            # Eventos
            if mensaje.tipo == "join":
                administrador_juego.unir_sala_ws(mensaje.jugador_id, sala_id)
                await administrador_juego.enviar_estado_sala(sala_id)

            elif mensaje.tipo == "reconnect":
                administrador_juego.reconectar_jugador(mensaje.jugador_id, sala_id)
                await administrador_juego.enviar_estado_sala(sala_id)

            elif mensaje.tipo == "escritura":
                texto = mensaje.datos.get("texto", "")
                tiempo = mensaje.datos.get("tiempo_tomado", 45)
                await administrador_juego.procesar_escritura(mensaje.jugador_id, sala_id, texto, tiempo)

            elif mensaje.tipo == "start_game":
                await administrador_juego.iniciar_partida(sala_id)

            elif mensaje.tipo == "abandonar":
                administrador_juego.abandonar_sala(mensaje.jugador_id, sala_id)
                await administrador_juego.enviar_estado_sala(sala_id)
                # no hacemos break forzado: cliente puede cerrar

            # Auto-start simple: si la sala está llena y no está jugando
            sala = administrador_juego.obtener_sala(sala_id)
            if sala and sala.max_jugadores and len(sala.jugadores) == sala.max_jugadores:
                await administrador_juego.iniciar_partida(sala_id)

    except WebSocketDisconnect:
        # Al desconectar, intentar eliminar la conexión y notificar
        try:
            administrador_juego.conexiones[sala_id].remove(websocket)
        except Exception:
            pass
        administrador_juego.abandonar_sala(jugador_id, sala_id)
        await administrador_juego.enviar_estado_sala(sala_id)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
