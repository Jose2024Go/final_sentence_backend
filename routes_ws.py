# backend/routes_ws.py
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import asyncio
from models import MensajeWebSocket
from game_controller import juego  # instancia única

router = APIRouter()

@router.websocket("/ws/{sala_id}/{jugador_id}")
async def ws_sala(websocket: WebSocket, sala_id: str, jugador_id: str):
    await websocket.accept()
    admin = juego  # alias

    # registrar la conexión (lista de websockets por sala)
    admin.conexiones.setdefault(sala_id, [])
    admin.conexiones[sala_id].append(websocket)

    # asegurar sala en memoria (si existe en BD)
    if sala_id not in admin.salas_activas:
        admin.cargar_sala_desde_bd(sala_id)

    # enviar estado inicial (si hay sala)
    await admin.enviar_estado_sala(sala_id)

    try:
        while True:
            datos = await websocket.receive_json()

            # normalizar mensaje
            try:
                mensaje = MensajeWebSocket(**datos)
            except Exception:
                tipo = datos.get("tipo")
                datos_payload = datos.get("datos", {})
                jugador_payload = datos.get("jugador_id", jugador_id)
                mensaje = MensajeWebSocket(tipo=tipo, datos=datos_payload, jugador_id=jugador_payload)

            # manejar tipos básicos
            if mensaje.tipo == "join":
                # agrega/reconecta al jugador en memoria
                admin.unir_sala_ws(mensaje.jugador_id, sala_id)
                await admin.enviar_estado_sala(sala_id)

            elif mensaje.tipo == "reconnect":
                admin.reconectar_jugador(mensaje.jugador_id, sala_id)
                await admin.enviar_estado_sala(sala_id)

            elif mensaje.tipo == "iniciar_partida" or mensaje.tipo == "start_game":
                await admin.iniciar_partida(sala_id)
                # enviar estado actualizado ya lo hace iniciar_partida

            elif mensaje.tipo == "escritura":
                texto = mensaje.datos.get("texto", "")
                tiempo = mensaje.datos.get("tiempo_tomado", 45)
                await admin.procesar_escritura(mensaje.jugador_id, sala_id, texto, tiempo)

            elif mensaje.tipo == "abandonar":
                admin.abandonar_sala(mensaje.jugador_id, sala_id)
                await admin.enviar_estado_sala(sala_id)

            elif mensaje.tipo == "ping":
                # opcional: no hacemos nada, solo mantener conector vivo
                pass

            # auto-start si quedó llena
            sala = admin.obtener_sala(sala_id)
            if sala and sala.max_jugadores and len(sala.jugadores) == sala.max_jugadores and sala.estado != "jugando":
                await admin.iniciar_partida(sala_id)

    except WebSocketDisconnect:
        # quitar socket de la lista y marcar desconexión con ventana de reconexión
        try:
            admin.conexiones[sala_id].remove(websocket)
        except Exception:
            pass

        # marcar jugador desconectado temporalmente (no eliminar inmediatamente)
        sala = admin.obtener_sala(sala_id)
        if sala:
            for j in sala.jugadores:
                if j.id == jugador_id:
                    try:
                        j.conectado = False
                    except Exception:
                        pass
                    break
            try:
                admin.base_datos.actualizar_sala(sala)
            except Exception:
                pass

            await admin.enviar_estado_sala(sala_id)

            # esperar ventana para reconexión (25s)
            async def remover_si_no_reconecta():
                await asyncio.sleep(25)
                sala_now = admin.obtener_sala(sala_id)
                if not sala_now:
                    return
                jugador_presente = next((x for x in sala_now.jugadores if x.id == jugador_id), None)
                if jugador_presente and not getattr(jugador_presente, "conectado", True):
                    admin.abandonar_sala(jugador_id, sala_id)
                    try:
                        await admin.enviar_estado_sala(sala_id)
                    except Exception:
                        pass

            asyncio.create_task(remover_si_no_reconecta())

