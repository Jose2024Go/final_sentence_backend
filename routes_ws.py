# backend/routes_ws.py
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from game_manager import AdministradorJuego

router = APIRouter()
juego = AdministradorJuego()

@router.websocket("/ws/{sala_id}/{jugador_id}")
async def websocket_endpoint(websocket: WebSocket, sala_id: str, jugador_id: str):
    print(f"🔌 WS conectado → sala={sala_id}, jugador={jugador_id}")

    await websocket.accept()

    # Registrar la conexión
    if sala_id not in juego.conexiones:
        juego.conexiones[sala_id] = []
    juego.conexiones[sala_id].append(websocket)

    # JOIN automático
    juego.unir_sala_ws(jugador_id, sala_id)

    # Enviar estado inicial
    await juego.enviar_estado_sala(sala_id)

    try:
        while True:
            data = await websocket.receive_json()

            tipo = data.get("tipo")

            # 🔹 Mantener vivo el WebSocket
            if tipo == "ping":
                continue

            # 🔹 Jugador se une vía WS
            if tipo == "join":
                juego.unir_sala_ws(jugador_id, sala_id)
                await juego.enviar_estado_sala(sala_id)

            # 🔹 Iniciar partida
            elif tipo == "start_game":
                await juego.iniciar_partida(sala_id)

            # 🔹 Escritura del jugador
            elif tipo == "escritura":
                await juego.procesar_escritura(
                    jugador_id,
                    sala_id,
                    data.get("texto", ""),
                    data.get("tiempo_tomado", 1)
                )

            # 🔹 Jugador abandona
            elif tipo == "abandonar":
                juego.abandonar_sala(jugador_id, sala_id)
                await juego.enviar_estado_sala(sala_id)

    except WebSocketDisconnect:
        print(f"❌ WS desconectado → sala={sala_id}, jugador={jugador_id}")

        if sala_id in juego.conexiones:
            if websocket in juego.conexiones[sala_id]:
                juego.conexiones[sala_id].remove(websocket)
