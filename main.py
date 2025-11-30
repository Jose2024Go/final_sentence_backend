from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from game_manager import AdministradorJuego
from models import Jugador, TipoSala, MensajeWebSocket
import asyncio

app = FastAPI(title="Final Sentence API", version="1.0.0")
admin = AdministradorJuego()

# =======================================
# CORS
# =======================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =======================================
# RUTAS HTTP NECESARIAS PARA EL FRONTEND
# =======================================

@app.post("/jugador/nuevo")
async def crear_jugador(nombre: str, avatar: str = "default"):
    jugador_id = f"jugador_{abs(hash(nombre)) % (10**12)}"
    jugador = Jugador(id=jugador_id, nombre=nombre, avatar=avatar)
    admin.base_datos.guardar_jugador(jugador)
    return jugador.dict()

@app.get("/jugador/{jugador_id}/estadisticas")
async def obtener_estadisticas(jugador_id: str):
    stats = admin.base_datos.obtener_estadisticas_jugador(jugador_id)
    return stats.dict()

@app.post("/sala/crear")
async def crear_sala(jugador_id: str, tipo: TipoSala, max_jugadores: int = 10):
    jugador = admin.base_datos.obtener_jugador(jugador_id)
    if not jugador:
        raise HTTPException(status_code=404, detail="Jugador no encontrado")
    sala = admin.crear_sala(jugador, tipo, max_jugadores)
    return sala.dict()

@app.post("/sala/unir")
async def unir_sala(jugador_id: str, codigo_sala: str):
    jugador = admin.base_datos.obtener_jugador(jugador_id)
    if not jugador:
        raise HTTPException(status_code=404, detail="Jugador no encontrado")
    sala = admin.unir_sala(jugador, codigo_sala)
    if not sala:
        raise HTTPException(status_code=404, detail="Sala no encontrada o llena")
    return sala.dict()

@app.post("/sala/{sala_id}/iniciar")
async def iniciar_partida_http(sala_id: str):
    await admin.iniciar_partida(sala_id)
    return {"mensaje": "Partida iniciada"}

# =======================================
# MANEJO WEBSOCKET
# =======================================

@app.websocket("/ws/{sala_id}/{jugador_id}")
async def websocket_endpoint(ws: WebSocket, sala_id: str, jugador_id: str):

    await ws.accept()
    print(f"WS conectado: sala={sala_id} jugador={jugador_id}")

    # Registrar ws
    admin.conexiones.setdefault(sala_id, [])
    admin.conexiones[sala_id].append(ws)

    try:
        admin.unir_sala_ws(jugador_id, sala_id)
        await admin.enviar_estado_sala(sala_id)

        while True:
            data = await ws.receive_json()
            tipo = data.get("tipo")
            payload = data.get("datos", {})

            if tipo == "join":
                await admin.enviar_estado_sala(sala_id)

            elif tipo == "iniciar_partida":
                await admin.iniciar_partida(sala_id)

            elif tipo == "escritura":
                await admin.procesar_escritura(
                    jugador_id,
                    sala_id,
                    payload.get("texto", ""),
                    payload.get("tiempo_tomado", 1)
                )

    except WebSocketDisconnect:
        print(f"Jugador desconectado: {jugador_id}")
        if sala_id in admin.conexiones:
            try:
                admin.conexiones[sala_id].remove(ws)
            except:
                pass


# =======================================
# UVICORN LOCAL (IGNORADO EN RENDER)
# =======================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


