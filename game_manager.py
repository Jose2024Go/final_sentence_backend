# backend/game_manager.py
import asyncio
import random
import string
from datetime import datetime
from typing import Dict, List, Optional
import logging
import time

from fastapi import WebSocket

# Placeholders para tus modelos y base de datos
from models import Sala, Jugador, Frase, EstadoJugador, TipoSala, Partida
from database import BaseDatos

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")


class AdministradorJuego:
    def __init__(self):
        self.base_datos = BaseDatos()
        self.salas_activas: Dict[str, Sala] = {}
        self.conexiones: Dict[str, List[WebSocket]] = {}

        # Cargar frases desde MongoDB (o fallback)
        self.frases_terror = self._cargar_frases_terror()

        # Monitores de tiempo por sala
        self._monitores_tiempo: Dict[str, asyncio.Task] = {}

    # ======================================================
    # ===============   CARGA DE FRASES   ==================
    # ======================================================
    def _cargar_frases_terror(self) -> List[Frase]:
        """
        Carga frases desde MongoDB.
        Si la base está vacía o falla -> usa frases hardcodeadas.
        """
        try:
            start = time.time()
            frases_db = self.base_datos.obtener_frases_terror(200)
            logging.debug(f"Cargando frases desde MongoDB... ({len(frases_db)} encontradas)")
            logging.debug(f"Tiempo carga frases: {time.time() - start:.3f}s")
        except Exception as e:
            logging.error(f"No se pudo cargar frases desde DB: {e}")
            frases_db = []

        frases = []

        if frases_db:
            for i, frase in enumerate(frases_db):
                mongo_id = frase.get("_id")
                frases.append(Frase(
                    id=str(mongo_id) if mongo_id else str(i),
                    texto=frase.get("texto") or "",
                    dificultad=frase.get("dificultad") or "media",
                    categoria=frase.get("categoria") or "terror"
                ))
            logging.info(f"✔ {len(frases)} frases cargadas desde MongoDB")
            return frases

        # fallback hardcodeado
        frases_hardcode = [
            "La sombra avanzaba silenciosa por el pasillo.",
            "Al abrir la puerta, nadie respondió al llamado.",
            "El susurro decía mi nombre al oído sin moverse nadie.",
            "Las luces titilaron y la figura estaba ya detrás de mí.",
            "No había teléfonos en la casa, pero alguien marcó desde adentro.",
            "Encontré una nota en mi almohada que decía: vuelve a dormir.",
            "El espejo reflejó una habitación que no era la mía.",
            "Cada vez que parpadeaba, alguien estaba más cerca.",
            "La casa respiraba y yo no estaba dentro de ella.",
            "Las marcas en la pared formaban mi nombre al revés."
        ]

        frases = [
            Frase(id=str(i), texto=txt, dificultad="media", categoria="terror")
            for i, txt in enumerate(frases_hardcode)
        ]

        logging.warning("⚠ MongoDB no respondió, usando frases HARDCODEADAS")
        return frases

    # ======================================================
    # ===============   SALAS / CREACIÓN   =================
    # ======================================================
    def _generar_codigo_sala(self) -> str:
        while True:
            codigo = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            if not self.base_datos.obtener_sala_por_codigo(codigo):
                return codigo

    def obtener_sala(self, sala_id: str) -> Optional[Sala]:
        return self.salas_activas.get(sala_id)

    def cargar_sala_desde_bd(self, sala_id: str) -> Optional[Sala]:
        sala_bd = self.base_datos.obtener_sala(sala_id)
        if sala_bd:
            self.salas_activas[sala_id] = sala_bd
            self.conexiones.setdefault(sala_id, [])
            logging.debug(f"Sala cargada desde DB: {sala_id}")
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

        try:
            self.base_datos.crear_sala(sala)
            logging.debug(f"Sala creada en DB: {sala_id}")
        except Exception as e:
            logging.error(f"No se pudo crear sala en DB: {e}")

        return sala

    def unir_sala(self, jugador: Jugador, codigo_sala: str) -> Optional[Sala]:
        sala = self.base_datos.obtener_sala_por_codigo(codigo_sala)
        if not sala:
            return None

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

        # Notificar WS
        asyncio.create_task(self.transmitir_a_sala(sala_activa.id, {
            "tipo": "jugador_unido",
            "jugador": self._serializar_jugador(jugador)
        }))

        logging.debug(f"Jugador {jugador.id} unido a sala {sala_activa.id}")
        return sala_activa

    # ======================================================
    # ================   ABANDONAR SALA   =================
    # ======================================================
    async def abandonar_sala(self, jugador_id: str, sala_id: str):
        sala = self.salas_activas.get(sala_id)
        if not sala:
            logging.warning(f"Intento de abandonar sala no existente: {sala_id}")
            return

        jugador = next((x for x in sala.jugadores if x.id == jugador_id), None)
        if not jugador:
            return

        sala.jugadores.remove(jugador)
        self.base_datos.actualizar_sala(sala)

        await self.transmitir_a_sala(sala_id, {
            "tipo": "jugador_abandono",
            "jugador_id": jugador_id
        })

        logging.info(f"Jugador {jugador_id} abandonó sala {sala_id}")

        if len(sala.jugadores) == 0:
            self.eliminar_sala(sala_id)
        elif sala.jugador_anfitrion == jugador_id:
            # reasignar anfitrión
            sala.jugador_anfitrion = sala.jugadores[0].id
            self.base_datos.actualizar_sala(sala)
            await self.transmitir_a_sala(sala_id, {
                "tipo": "nuevo_anfitrion",
                "jugador_id": sala.jugador_anfitrion
            })
            logging.debug(f"Nuevo anfitrión en sala {sala_id}: {sala.jugador_anfitrion}")

    # ======================================================
    # ================   WEBSOCKET FLOW   =================
    # ======================================================
    def unir_sala_ws(self, jugador_id: str, sala_id: str) -> Optional[Sala]:
        jugador = self.base_datos.obtener_jugador(jugador_id)
        if not jugador:
            return None

        if sala_id not in self.salas_activas:
            sala_bd = self.base_datos.obtener_sala(sala_id)
            if not sala_bd:
                return None
            self.salas_activas[sala_id] = sala_bd
            self.conexiones.setdefault(sala_id, [])

        sala = self.salas_activas[sala_id]

        if any(j.id == jugador.id for j in sala.jugadores):
            return sala

        if len(sala.jugadores) >= sala.max_jugadores:
            return None

        sala.jugadores.append(jugador)
        self.base_datos.actualizar_sala(sala)

        asyncio.create_task(self.transmitir_a_sala(sala_id, {
            "tipo": "jugador_unido",
            "jugador": self._serializar_jugador(jugador)
        }))

        logging.debug(f"[WS] Jugador {jugador.id} unido vía WS a sala {sala_id}")
        return sala

    # ======================================================
    # ================   INICIO PARTIDA   =================
    # ======================================================
    async def iniciar_partida(self, sala_id: str):
        if sala_id not in self.salas_activas:
            return

        sala = self.salas_activas[sala_id]

        if sala.estado == "jugando":
            logging.warning(f"Sala {sala_id} ya está jugando")
            return

        if len(sala.jugadores) < 2:
            await self.transmitir_a_sala(sala_id, {
                "tipo": "error",
                "mensaje": "Se necesitan al menos 2 jugadores"
            })
            return

        sala.estado = "jugando"
        sala.ronda_actual += 1
        sala.tiempo_inicio = datetime.now()
        sala.frase_actual = random.choice(self.frases_terror)

        for j in sala.jugadores:
            j.estado = EstadoJugador.JUGANDO
            j.errores = 0
            j.progreso = 0
            j.ppm = 0

        try:
            self.base_datos.actualizar_sala(sala)
        except Exception as e:
            logging.error(f"No se pudo actualizar sala {sala_id}: {e}")

        await self.transmitir_a_sala(sala_id, {
            "tipo": "partida_iniciada",
            "frase": sala.frase_actual.texto,
            "tiempo_limite": sala.tiempo_limite,
            "ronda_actual": sala.ronda_actual
        })

        # monitor de tiempo
        if sala_id in self._monitores_tiempo:
            old = self._monitores_tiempo.pop(sala_id)
            try:
                old.cancel()
            except:
                pass

        tarea = asyncio.create_task(self._monitor_tiempo_ronda(sala_id, sala.tiempo_limite))
        self._monitores_tiempo[sala_id] = tarea

        logging.info(f"Partida iniciada en sala {sala_id}, frase: {sala.frase_actual.texto}")

    # ======================================================
    # ================   MONITOR TIEMPO   =================
    # ======================================================
    async def _monitor_tiempo_ronda(self, sala_id: str, tiempo_limite: int):
        try:
            await asyncio.sleep(tiempo_limite)
        except asyncio.CancelledError:
            logging.debug(f"Monitor de tiempo cancelado para sala {sala_id}")
            return

        if sala_id not in self.salas_activas:
            return

        sala = self.salas_activas[sala_id]

        if sala.estado != "jugando":
            return

        logging.debug(f"Tiempo agotado para sala {sala_id}, procesando jugadores restantes")

        for j in sala.jugadores:
            if j.estado == EstadoJugador.JUGANDO and j.progreso < 100:
                j.estado = EstadoJugador.ELIMINADO
                await self.transmitir_a_sala(sala_id, {
                    "tipo": "jugador_eliminado",
                    "jugador_id": j.id,
                    "razon": "tiempo"
                })

        vivos = [j for j in sala.jugadores if j.estado == EstadoJugador.JUGANDO]

        if len(vivos) == 1:
            await self.finalizar_partida(sala_id, vivos[0].id)
        else:
            mejor = max(sala.jugadores, key=lambda x: (x.progreso, x.ppm), default=None)
            ganador = mejor.id if mejor else None
            await self.finalizar_partida(sala_id, ganador)

    # ======================================================
    # ================   PROCESAR TEXTO   =================
    # ======================================================
    async def procesar_escritura(self, jugador_id: str, sala_id: str, texto: str, tiempo_tomado: float):
        if sala_id not in self.salas_activas:
            return

        sala = self.salas_activas[sala_id]
        jugador = next((x for x in sala.jugadores if x.id == jugador_id), None)

        if not jugador or jugador.estado != EstadoJugador.JUGANDO:
            return

        frase = sala.frase_actual.texto.strip()
        correcto = texto.strip() == frase

        if correcto:
            palabras = len(frase.split())
            jugador.ppm = int((palabras / tiempo_tomado) * 60) if tiempo_tomado > 0 else 0
            jugador.progreso = 100
            jugador.estado = EstadoJugador.JUGADO

            await self.transmitir_a_sala(sala_id, {
                "tipo": "jugador_completo",
                "jugador_id": jugador.id,
                "ppm": jugador.ppm
            })

        else:
            jugador.errores += 1
            jugador.progreso = max(0, jugador.progreso - 10)

            await self.transmitir_a_sala(sala_id, {
                "tipo": "jugador_error",
                "jugador_id": jugador.id,
                "errores_actuales": jugador.errores
            })

            if jugador.errores >= 3:
                await self.eliminar_jugador(jugador_id, sala_id)

        try:
            self.base_datos.actualizar_sala(sala)
        except:
            pass

        vivos = [j for j in sala.jugadores if j.estado == EstadoJugador.JUGANDO]
        completados = [j for j in sala.jugadores if j.progreso == 100]

        if len(completados) == len(sala.jugadores):
            mejor = max(sala.jugadores, key=lambda x: x.ppm)
            await self.finalizar_partida(sala_id, mejor.id)

        elif len(vivos) == 1 and len(sala.jugadores) > 1:
            await self.finalizar_partida(sala_id, vivos[0].id)

    # ======================================================
    # ================   ELIMINAR JUGADOR   ===============
    # ======================================================
    async def eliminar_jugador(self, jugador_id: str, sala_id: str):
        sala = self.salas_activas.get(sala_id)
        if not sala:
            return

        jugador = next((x for x in sala.jugadores if x.id == jugador_id), None)
        if not jugador:
            return

        jugador.estado = EstadoJugador.ELIMINADO

        await self.transmitir_a_sala(sala_id, {
            "tipo": "jugador_eliminado",
            "jugador_id": jugador.id,
            "razon": "errores"
        })

        vivos = [j for j in sala.jugadores if j.estado == EstadoJugador.JUGANDO]

        if len(vivos) == 1:
            await self.finalizar_partida(sala_id, vivos[0].id)
        elif len(vivos) == 0:
            await self.finalizar_partida(sala_id, None)

    # ======================================================
    # ================   FINALIZAR PARTIDA   ===============
    # ======================================================
    async def finalizar_partida(self, sala_id: str, ganador_id: Optional[str]):
        if sala_id not in self.salas_activas:
            return

        sala = self.salas_activas[sala_id]

        if sala_id in self._monitores_tiempo:
            try:
                self._monitores_tiempo[sala_id].cancel()
            except:
                pass
            self._monitores_tiempo.pop(sala_id, None)

        sala.estado = "finalizada"

        partida = Partida(
            id=f"partida_{int(datetime.now().timestamp() * 1000)}",
            sala_id=sala_id,
            jugadores=sala.jugadores,
            frases_usadas=[sala.frase_actual],
            ganador=ganador_id,
            duracion=(datetime.now() - sala.tiempo_inicio).seconds,
            fecha=datetime.now()
        )

        try:
            self.base_datos.guardar_partida(partida)
            logging.info(f"Partida guardada en DB: {partida.id}")
        except:
            pass

        await self.transmitir_a_sala(sala_id, {
            "tipo": "partida_finalizada",
            "ganador_id": ganador_id,
            "estadisticas": [self._serializar_jugador(j) for j in sala.jugadores]
        })

        asyncio.create_task(self._eliminar_sala_despues(sala_id, 30))

    # ======================================================
    # ================   LIMPIEZA   ========================
    # ======================================================
    async def _eliminar_sala_despues(self, sala_id: str, segundos: int):
        await asyncio.sleep(segundos)
        self.eliminar_sala(sala_id)

    def eliminar_sala(self, sala_id: str):
        if sala_id in self.salas_activas:
            asyncio.create_task(self.transmitir_a_sala(sala_id, {
                "tipo": "sala_eliminada",
                "sala_id": sala_id
            }))
            del self.salas_activas[sala_id]
            logging.info(f"Sala eliminada: {sala_id}")

        if sala_id in self.conexiones:
            for ws in list(self.conexiones[sala_id]):
                try:
                    asyncio.create_task(ws.close())
                except:
                    pass
            del self.conexiones[sala_id]

        try:
            self.base_datos.eliminar_sala(sala_id)
        except:
            pass

    # ======================================================
    # ================   ENVÍO WS   =======================
    # ======================================================
    async def transmitir_a_sala(self, sala_id: str, mensaje: dict):
        if sala_id not in self.conexiones:
            return
        for ws in self.conexiones[sala_id]:
            try:
                await ws.send_json(mensaje)
            except Exception as e:
                logging.warning(f"Fallo al enviar WS: {e}")

    # ======================================================
    # ================   SERIALIZACIÓN   ==================
    # ======================================================
    def _serializar_jugador(self, jugador: Jugador) -> dict:
        return {
            "id": jugador.id,
            "nombre": jugador.nombre,
            "estado": jugador.estado,
            "progreso": jugador.progreso,
            "ppm": jugador.ppm,
            "errores": jugador.errores
        }
