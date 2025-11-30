from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from game_controller import juego

router = APIRouter()

@router.websocket("/ws/{sala_id}/{jugador_id}")
async def websocket_endpoint(websocket: WebSocket, sala_id: str, jugador_id: str):

    await websocket.accept()

    if sala_id not in juego.conexiones:
        juego.conexiones[sala_id] = []
    juego.conexiones[sala_id].append(websocket)

    # Unión automática al conectar
    juego.unir_sala_ws(jugador_id, sala_id)

    # Enviar estado actual
    await juego.enviar_estado_sala(sala_id)

    try:
        while True:
            data = await websocket.receive_json()
            tipo = data.get("tipo")
            datos = data.get("datos", {})

            if tipo == "ping":
                continue

            if tipo == "join":
                juego.unir_sala_ws(jugador_id, sala_id)
                await juego.enviar_estado_sala(sala_id)

            elif tipo == "start_game":
                await juego.iniciar_partida(sala_id)

            elif tipo == "escritura":
                await juego.procesar_escritura(
                    jugador_id,
                    sala_id,
                    datos.get("texto", ""),
                    datos.get("tiempo_tomado", 1)
                )

            elif tipo == "abandonar":
                juego.abandonar_sala(jugador_id, sala_id)
                await juego.enviar_estado_sala(sala_id)

    except WebSocketDisconnect:
        if sala_id in juego.conexiones:
            if websocket in juego.conexiones[sala_id]:
                juego.conexiones[sala_id].remove(websocket)
