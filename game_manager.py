# backend/game_manager.py
import asyncio
import random
import string
from datetime import datetime
from typing import Dict, List, Optional, Any

from fastapi import WebSocket

from models import Sala, Jugador, Frase, EstadoJugador, TipoSala
from database import BaseDatos


class AdministradorJuego:
    def __init__(self):
        self.base_datos = BaseDatos()
        self.salas_activas: Dict[str, Sala] = {}
        # conexiones: sala_id -> list of WebSocket
        self.conexiones: Dict[str, List[WebSocket]] = {}
        self.frases_terror = self._cargar_frases_terror()
        # trackear monitores de tiempo por sala (para poder cancelarlos si se finaliza antes)
        self._monitores_tiempo: Dict[str, asyncio.Task] = {}

    def _cargar_frases_terror(self) -> List[Frase]:
        """Carga frases de terror desde la base de datos"""
        frases_db = self.base_datos.obtener_frases_terror(100)
        frases = []
        for i, frase in enumerate(frases_db):
            texto = frase.get("texto") or frase.get("text") or ""
            frases.append(Frase(
                id=str(frase.get("_id", i)),
                texto=texto,
                dificultad=frase.get("dificultad", "media"),
                categoria=frase.get("categoria", "terror")
            ))
        return frases

    def _generar_codigo_sala(self) -> str:
        """Genera un código único de 6 caracteres para la sala"""
        while True:
            codigo = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            if not self.base_datos.obtener_sala_por_codigo(codigo):
                return codigo

    # --- Persistencia en memoria / helpers ---
    def obtener_sala(self, sala_id: str) -> Optional[Sala]:
        return self.salas_activas.get(sala_id)

    def cargar_sala_desde_bd(self, sala_id: str) -> Optional[Sala]:
        sala_bd = self.base_datos.obtener_sala(sala_id)
        if sala_bd:
            self.salas_activas[sala_id] = sala_bd
            self.conexiones.setdefault(sala_id, [])
            return sala_bd
        return None

    def crear_sala(self, jugador_anfitrion: Jugador, tipo: TipoSala, max_jugadores: int = 10) -> Sala:
        sala_id = f"sala_{int(datetime.now().timestamp() * 1000)}"
        codigo = self._generar_codigo_sala()
        sala = Sala(
            id=sala_id,
            codigo=codigo,
            tipo=tipo,
            jugadores=[jugador_anfitrion],
            jugador_anfitrion=jugador_anfitrion.id,
            max_jugadores=max_jugadores,
            estado="esperando",
            ronda_actual=0,
            tiempo_limite=getattr(jugador_anfitrion, "tiempo_limite", 45)
        )
        self.salas_activas[sala_id] = sala
        self.conexiones[sala_id] = []
        # guarda en la BDD
        try:
            self.base_datos.crear_sala(sala)
        except Exception:
            # fallback: si falla la persistencia, mantenemos en memoria
            pass
        return sala

    def unir_sala(self, jugador: Jugador, codigo_sala: str) -> Optional[Sala]:
        """Unión vía HTTP: carga sala por código y la actualiza."""
        sala = self.base_datos.obtener_sala_por_codigo(codigo_sala)
        if not sala:
            return None

        # si no está en memoria, cargar
        if sala.id not in self.salas_activas:
            self.salas_activas[sala.id] = sala
            self.conexiones.setdefault(sala.id, [])

        sala_activa = self.salas_activas[sala.id]
        if len(sala_activa.jugadores) >= sala_activa.max_jugadores:
            return None
        if any(j.id == jugador.id for j in sala_activa.jugadores):
            return sala_activa
        sala_activa.jugadores.append(jugador)
        self.base_datos.actualizar_sala(sala_activa)

        # Notificar a través de WebSocket (si hay conexiones)
        asyncio.create_task(self.transmitir_a_sala(sala_activa.id, {
            "tipo": "jugador_unido",
            "jugador": self._serializar_jugador(jugador)
        }))

        # Auto iniciar si está llena
        if len(sala_activa.jugadores) == sala_activa.max_jugadores:
            asyncio.create_task(self.iniciar_partida(sala_activa.id))

        return sala_activa

    # ---------- WebSocket flow ----------
    def unir_sala_ws(self, jugador_id: str, sala_id: str) -> Optional[Sala]:
        """
        Manejo de unión vía WebSocket: busca el jugador en BDD y lo inserta en sala activa.
        Devuelve la sala actualizada o None.
        """
        jugador = self.base_datos.obtener_jugador(jugador_id)
        if not jugador:
            return None
        if sala_id not in self.salas_activas:
            sala_bd = self.base_datos.obtener_sala(sala_id)
            if sala_bd:
                self.salas_activas[sala_id] = sala_bd
                self.conexiones.setdefault(sala_id, [])
            else:
                return None

        sala = self.salas_activas[sala_id]
        if any(j.id == jugador.id for j in sala.jugadores):
            return sala

        if len(sala.jugadores) >= sala.max_jugadores:
            return None

        sala.jugadores.append(jugador)
        self.base_datos.actualizar_sala(sala)

        # Notificar a todos
        asyncio.create_task(self.transmitir_a_sala(sala_id, {
            "tipo": "jugador_unido",
            "jugador": self._serializar_jugador(jugador)
        }))
        return sala

    def reconectar_jugador(self, jugador_id: str, sala_id: str) -> bool:
        """
        Reconexión: si el jugador ya existe en la sala, mantenemos su presencia.
        Si no existe, intentamos añadir desde BDD.
        """
        if sala_id not in self.salas_activas:
            return False
        sala = self.salas_activas[sala_id]
        existe = any(j.id == jugador_id for j in sala.jugadores)
        if not existe:
            jugador = self.base_datos.obtener_jugador(jugador_id)
            if jugador:
                sala.jugadores.append(jugador)
                self.base_datos.actualizar_sala(sala)
                asyncio.create_task(self.transmitir_a_sala(sala_id, {
                    "tipo": "jugador_unido",
                    "jugador": self._serializar_jugador(jugador)
                }))
                return True
            return False
        return True

    def _asegurar_host(self, sala: Sala):
        """
        Asegura que exista un host válido. Si el host actual no está en la lista,
        reasigna el primer jugador como host.
        """
        if not sala.jugadores:
            sala.jugador_anfitrion = None
            return
        if not sala.jugador_anfitrion or not any(j.id == sala.jugador_anfitrion for j in sala.jugadores):
            sala.jugador_anfitrion = sala.jugadores[0].id
            try:
                self.base_datos.actualizar_sala(sala)
            except Exception:
                pass

    # ---------- Inicio de partida ----------
    async def iniciar_partida(self, sala_id: str):
        if sala_id not in self.salas_activas:
            return

        sala = self.salas_activas[sala_id]

        # Evitar relanzar la partida
        if sala.estado == "jugando":
            return

        # Deben haber al menos 2 jugadores
        if len(sala.jugadores) < 2:
            await self.transmitir_a_sala(sala_id, {
                "tipo": "error",
                "mensaje": "No hay suficientes jugadores para iniciar."
            })
            return

        # Marcar sala como activa
        sala.estado = "jugando"
        sala.ronda_actual = (sala.ronda_actual or 0) + 1
        sala.tiempo_inicio = datetime.now()

        # Seleccionar frase de la ronda (fallback si no hay frases)
        sala.frase_actual = random.choice(self.frases_terror) if self.frases_terror else None

        # 🔥 **SOLUCIÓN CRÍTICA**
        # Habilitar correctamente a todos los jugadores para que el backend sí procese sus teclas
        for jugador in sala.jugadores:
            jugador.estado = EstadoJugador.JUGANDO   # <--- ESSENCIAL
            jugador.errores = 0
            jugador.progreso = 0
            jugador.ppm = 0

        # Guardar en DB si corresponde
        try:
            self.base_datos.actualizar_sala(sala)
        except Exception:
            pass

        # Avisar a todos los jugadores que la partida comenzó
        await self.transmitir_a_sala(sala_id, {
            "tipo": "partida_iniciada",
            "frase": sala.frase_actual.texto if sala.frase_actual else "",
            "tiempo_limite": getattr(sala, "tiempo_limite", 45),
            "ronda_actual": sala.ronda_actual
        })

        # Lanzar monitor de tiempo para la ronda (se reemplaza si ya existía uno)
        tiempo_limite = getattr(sala, "tiempo_limite", 45)
        if sala_id in self._monitores_tiempo:
            # cancelar monitor previo si existiera
            tarea_prev = self._monitores_tiempo.pop(sala_id)
            try:
                tarea_prev.cancel()
            except Exception:
                pass
        tarea = asyncio.create_task(self._monitor_tiempo_ronda(sala_id, tiempo_limite))
        self._monitores_tiempo[sala_id] = tarea

    async def _monitor_tiempo_ronda(self, sala_id: str, tiempo_limite: int):
        """
        Espera tiempo_limite segundos y procesa jugadores que no completaron.
        Si la partida ya finalizó antes de tiempo, la tarea saldrá.
        """
        try:
            await asyncio.sleep(tiempo_limite)
        except asyncio.CancelledError:
            return

        # Si la sala ya fue finalizada, no hacer nada
        if sala_id not in self.salas_activas:
            return
        sala = self.salas_activas[sala_id]
        if sala.estado != "jugando":
            return

        # Marcar jugadores que no completaron como eliminados (o procesar según reglas)
        jugadores_no_completaron = [j for j in sala.jugadores if getattr(j, "progreso", 0) < 100 and getattr(j, "estado", None) == EstadoJugador.JUGANDO]

        for j in jugadores_no_completaron:
            j.estado = EstadoJugador.ELIMINADO
            # Notificamos su eliminación por tiempo agotado
            await self.transmitir_a_sala(sala_id, {
                "tipo": "jugador_eliminado",
                "jugador_id": j.id,
                "razon": "tiempo_agotado"
            })

        # Determinar ganador si hay uno vivo
        jugadores_vivos = [j for j in sala.jugadores if getattr(j, "estado", None) == EstadoJugador.JUGANDO]
        if len(jugadores_vivos) == 1:
            ganador = jugadores_vivos[0].id
            await self.finalizar_partida(sala_id, ganador)
        elif len(jugadores_vivos) == 0:
            # Si ninguno quedó, elegir el que tenga mejor progreso/ppm como fallback
            mejor = max(sala.jugadores, key=lambda x: (getattr(x, "progreso", 0), getattr(x, "ppm", 0)), default=None)
            ganador = mejor.id if mejor else None
            await self.finalizar_partida(sala_id, ganador)
        else:
            # Si quedaron varios vivos (nadie completó), finalizamos y mandamos estadísticas
            await self.finalizar_partida(sala_id, None)

    # ---------- Escritura / eliminación ----------
    async def procesar_escritura(self, jugador_id: str, sala_id: str, texto: str, tiempo_tomado: float):
        if sala_id not in self.salas_activas:
            return
        sala = self.salas_activas[sala_id]
        jugador = next((j for j in sala.jugadores if j.id == jugador_id), None)
        if not jugador or getattr(jugador, "estado", None) != EstadoJugador.JUGANDO:
            return

        frase_correcta = sala.frase_actual.texto if sala.frase_actual else ""
        es_correcto = texto.strip() == frase_correcta.strip()
        palabras = len(frase_correcta.split()) if frase_correcta else 0
        ppm = (palabras / tiempo_tomado) * 60 if tiempo_tomado > 0 and palabras > 0 else 0

        if es_correcto:
            jugador.ppm = ppm
            jugador.progreso = 100
            jugador.estado = EstadoJugador.JUGADO if hasattr(EstadoJugador, "JUGADO") else jugador.estado
            # Notificar completado
            await self.transmitir_a_sala(sala_id, {
                "tipo": "jugador_completo",
                "jugador_id": jugador_id,
                "ppm": ppm,
                "tiempo_tomado": tiempo_tomado
            })
        else:
            jugador.errores = getattr(jugador, "errores", 0) + 1
            jugador.progreso = max(0, getattr(jugador, "progreso", 0) - 10)
            await self.transmitir_a_sala(sala_id, {
                "tipo": "jugador_error",
                "jugador_id": jugador_id,
                "errores_actuales": jugador.errores
            })
            if jugador.errores >= 3:
                await self.eliminar_jugador(jugador_id, sala_id)

        # Guardar estado de sala
        try:
            self.base_datos.actualizar_sala(sala)
        except Exception:
            pass

        # Si todos completaron o sólo queda uno jugando, finalizar partida
        jugadores_vivos = [j for j in sala.jugadores if getattr(j, "estado", None) == EstadoJugador.JUGANDO]
        completados = [j for j in sala.jugadores if getattr(j, "progreso", 0) == 100]
        if len(completados) == len(sala.jugadores):
            # todos completaron: elegir mejor ppm como ganador
            mejor = max(sala.jugadores, key=lambda x: getattr(x, "ppm", 0), default=None)
            ganador = mejor.id if mejor else None
            await self.finalizar_partida(sala_id, ganador)
        elif len(jugadores_vivos) == 1 and len(sala.jugadores) > 1:
            # un único vivo -> ganador por supervivencia
            ganador = jugadores_vivos[0].id
            await self.finalizar_partida(sala_id, ganador)

    async def eliminar_jugador(self, jugador_id: str, sala_id: str):
        if sala_id not in self.salas_activas:
            return
        sala = self.salas_activas[sala_id]
        jugador = next((j for j in sala.jugadores if j.id == jugador_id), None)
        if jugador:
            jugador.estado = EstadoJugador.ELIMINADO
            await self.transmitir_a_sala(sala_id, {
                "tipo": "jugador_eliminado",
                "jugador_id": jugador_id,
                "razon": "demasiados_errores"
            })
            jugadores_vivos = [j for j in sala.jugadores if getattr(j, "estado", None) == EstadoJugador.JUGANDO]
            if len(jugadores_vivos) == 1:
                await self.finalizar_partida(sala_id, jugadores_vivos[0].id)
            elif len(jugadores_vivos) == 0:
                await self.finalizar_partida(sala_id, None)

    async def finalizar_partida(self, sala_id: str, ganador_id: Optional[str]):
        if sala_id not in self.salas_activas:
            return
        sala = self.salas_activas[sala_id]
        # cancelar monitor de tiempo si existe
        if sala_id in self._monitores_tiempo:
            tarea = self._monitores_tiempo.pop(sala_id)
            try:
                tarea.cancel()
            except Exception:
                pass

        sala.estado = "finalizada"
        from models import Partida
        partida = Partida(
            id=f"partida_{int(datetime.now().timestamp() * 1000)}",
            sala_id=sala_id,
            jugadores=sala.jugadores,
            frases_usadas=[sala.frase_actual] if sala.frase_actual else [],
            ganador=ganador_id,
            duracion=(datetime.now() - sala.tiempo_inicio).seconds if sala.tiempo_inicio else 0,
            fecha=datetime.now()
        )
        try:
            self.base_datos.guardar_partida(partida)
        except Exception:
            pass

        await self.transmitir_a_sala(sala_id, {
            "tipo": "partida_finalizada",
            "ganador_id": ganador_id,
            "estadisticas": [self._serializar_jugador(j) for j in sala.jugadores]
        })
        # eliminar sala después de un tiempo para limpieza (30s)
        asyncio.create_task(self._eliminar_sala_despues(sala_id, 30))

    async def _eliminar_sala_despues(self, sala_id: str, segundos: int):
        await asyncio.sleep(segundos)
        self.eliminar_sala(sala_id)

    # ---------- Transmisión y estado de sala ----------
    def _serializar_jugador(self, jugador: Jugador) -> dict:
        """Serializa un jugador para enviar por WS (evitar campos no serializables)."""
        return {
            "id": jugador.id,
            "nombre": getattr(jugador, "nombre", ""),
            "avatar": getattr(jugador, "avatar", "default"),
            "ppm": getattr(jugador, "ppm", 0),
            "progreso": getattr(jugador, "progreso", 0),
            "errores": getattr(jugador, "errores", 0),
            "estado": getattr(jugador, "estado", "esperando")
        }

    def _estado_sala_para_envio(self, sala: Sala) -> dict:
        """Construye el objeto estado de sala que se envía por WS."""
        self._asegurar_host(sala)
        return {
            "tipo": "estado_sala",
            "sala_id": sala.id,
            "codigo": sala.codigo,
            "estado": sala.estado,
            "jugador_anfitrion": sala.jugador_anfitrion,
            "max_jugadores": sala.max_jugadores,
            "ronda_actual": getattr(sala, "ronda_actual", 0),
            "jugadores": [self._serializar_jugador(j) for j in sala.jugadores],
            "frase_actual_presentada": bool(getattr(sala, "frase_actual", None))
        }

    async def enviar_estado_sala(self, sala_id: str):
        """Envía el estado actual de la sala a todas las conexiones válidas."""
        if sala_id not in self.salas_activas:
            return
        sala = self.salas_activas[sala_id]
        estado = self._estado_sala_para_envio(sala)
        await self.transmitir_a_sala(sala_id, estado)

    async def transmitir_a_sala(self, sala_id: str, mensaje: dict):
        """
        Envía un mensaje JSON a todos los websockets de la sala y limpia websockets muertos.
        """
        if sala_id not in self.conexiones:
            return
        conexiones_validas: List[WebSocket] = []
        for websocket in list(self.conexiones[sala_id]):
            try:
                await websocket.send_json(mensaje)
                conexiones_validas.append(websocket)
            except Exception as e:
                # eliminar websockets inválidos
                try:
                    await websocket.close()
                except Exception:
                    pass
                print(f"[transmitir_a_sala] error enviando mensaje a socket: {e}")
        self.conexiones[sala_id] = conexiones_validas

    # ---------- Abandonar / eliminar sala ----------
    def abandonar_sala(self, jugador_id: str, sala_id: str):
        if sala_id not in self.salas_activas:
            return

        sala = self.salas_activas[sala_id]
        sala.jugadores = [j for j in sala.jugadores if j.id != jugador_id]
        # reasignar host si fue el anfitrión
        self._asegurar_host(sala)
        try:
            self.base_datos.actualizar_sala(sala)
        except Exception:
            pass

        asyncio.create_task(self.transmitir_a_sala(sala_id, {
            "tipo": "jugador_abandono",
            "jugador_id": jugador_id
        }))

        if len(sala.jugadores) == 0:
            self.eliminar_sala(sala_id)

    def eliminar_sala(self, sala_id: str):
        if sala_id in self.salas_activas:
            asyncio.create_task(self.transmitir_a_sala(sala_id, {
                "tipo": "sala_eliminada",
                "sala_id": sala_id
            }))
            del self.salas_activas[sala_id]
        if sala_id in self.conexiones:
            for ws in list(self.conexiones[sala_id]):
                try:
                    asyncio.create_task(ws.close())
                except Exception:
                    pass
            del self.conexiones[sala_id]
        try:
            self.base_datos.eliminar_sala(sala_id)
        except Exception:
            pass
