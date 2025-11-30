from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from game_manager import AdministradorJuego
from models import Jugador
import asyncio

app = FastAPI()
admin = AdministradorJuego()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===================================
# MANEJO WEBSOCKET
# ===================================

@app.websocket("/ws/{sala_id}/{jugador_id}")
async def websocket_endpoint(ws: WebSocket, sala_id: str, jugador_id: str):
    await ws.accept()

    print(f"WS conectado: sala={sala_id} jugador={jugador_id}")

    # Registrar conexión
    admin.conexiones.setdefault(sala_id, [])
    admin.conexiones[sala_id].append(ws)

    try:
        # Asegurar que el jugador está en la sala
        admin.unir_sala_ws(jugador_id, sala_id)

        # Enviar estado inicial
        await admin.enviar_estado_sala(sala_id)

        while True:
            data = await ws.receive_json()
            tipo = data.get("tipo")
            payload = data.get("datos", {})

            # =======================================================
            # JOIN (el frontend lo envía automáticamente al conectar)
            # =======================================================
            if tipo == "join":
                await admin.enviar_estado_sala(sala_id)

            # =======================================================
            # INICIAR PARTIDA (botón del anfitrión)
            # =======================================================
            elif tipo == "iniciar_partida":
                await admin.iniciar_partida(sala_id)

            # =======================================================
            # ESCRITURA DE FRASE (PantallaJuego.jsx)
            # =======================================================
            elif tipo == "escritura":
                texto = payload.get("texto", "")
                tiempo_tomado = payload.get("tiempo_tomado", 1)

                await admin.procesar_escritura(
                    jugador_id,
                    sala_id,
                    texto,
                    tiempo_tomado
                )

    except WebSocketDisconnect:
        print(f"Jugador desconectado: {jugador_id}")
        if sala_id in admin.conexiones:
            try:
                admin.conexiones[sala_id].remove(ws)
            except:
                pass

