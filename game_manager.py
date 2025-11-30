# backend/game_manager.py
import asyncio
import random
import string
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import WebSocket

from models import Sala, Jugador, Frase, EstadoJugador, TipoSala
from database import BaseDatos


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
            frases_db = self.base_datos.obtener_frases_terror(200)
        except Exception:
            frases_db = []

        frases = []

        # Si Mongo sí tiene frases
        if frases_db:
            for i, frase in enumerate(frases_db):
                frases.append(Frase(
                    id=str(frase.get("_id", i)),
                    texto=frase.get("texto", ""),
                    dificultad=frase.get("dificultad", "media"),
                    categoria=frase.get("categoria", "terror")
                ))
            print(f"✔ {len(frases)} frases cargadas desde MongoDB")
            return frases

        # Si no, usar fallback
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

        print("⚠ Advertencia: MongoDB no respondió, usando frases HARDCODEADAS")

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
        except Exception:
            pass

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

        return sala_activa

    # ======================================================
    # ================   WEBSOCKET FLOW   ==================
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

        return sala

    # ======================================================
    # ================   INICIO PARTIDA   ==================
    # ======================================================
    async def iniciar_partida(self, sala_id: str):
        if sala_id not in self.salas_activas:
            return

        sala = self.salas_activas[sala_id]

        if sala.estado == "jugando":
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

        # Seleccionar frase
        sala.frase_actual = random.choice(self.frases_terror)

        # Resetear stats de jugadores
        for j in sala.jugadores:
            j.estado = EstadoJugador.JUGANDO
            j.errores = 0
            j.progreso = 0
            j.ppm = 0

        try:
            self.base_datos.actualizar_sala(sala)
        except Exception:
            pass

        # Enviar inicio
        await self.transmitir_a_sala(sala_id, {
            "tipo": "partida_iniciada",
            "frase": sala.frase_actual.texto,
            "tiempo_limite": sala.tiempo_limite,
            "ronda_actual": sala.ronda_actual
        })

        # Lanzar monitor
        if sala_id in self._monitores_tiempo:
            old = self._monitores_tiempo.pop(sala_id)
            try:
                old.cancel()
            except:
                pass

        tarea = asyncio.create_task(self._monitor_tiempo_ronda(sala_id, sala.tiempo_limite))
        self._monitores_tiempo[sala_id] = tarea

    # ======================================================
    # ================   MONITOR TIEMPO   ==================
    # ======================================================
    async def _monitor_tiempo_ronda(self, sala_id: str, tiempo_limite: int):
        try:
            await asyncio.sleep(tiempo_limite)
        except asyncio.CancelledError:
            return

        if sala_id not in self.salas_activas:
            return

        sala = self.salas_activas[sala_id]

        if sala.estado != "jugando":
            return

        # Marcar eliminados por tiempo
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
            # Nadie completó: mejor PPM / progreso
            mejor = max(sala.jugadores,
                        key=lambda x: (x.progreso, x.ppm),
                        default=None)

            ganador = mejor.id if mejor else None
            await self.finalizar_partida(sala_id, ganador)

    # ======================================================
    # ================   PROCESAR TEXTO   ==================
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

        from models import Partida
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
    # ================   ENVÍO DE ESTADO   =================
    # ======================================================
    def _serializar_jugador(self, jugador: Jugador) -> dict:
        return {
            "id": jugador.id,
            "nombre": jugador.nombre,
            "avatar": jugador.avatar,
            "ppm": jugador.ppm,
            "progreso": jugador.progreso,
            "errores": jugador.errores,
            "estado": jugador.estado
        }

    def _estado_sala_para_envio(self, sala: Sala) -> dict:
        return {
            "tipo": "estado_sala",
            "sala_id": sala.id,
            "codigo": sala.codigo,
            "estado": sala.estado,
            "jugador_anfitrion": sala.jugador_anfitrion,
            "max_jugadores": sala.max_jugadores,
            "ronda_actual": sala.ronda_actual,
            "jugadores": [self._serializar_jugador(j) for j in sala.jugadores],
            "frase_actual_presentada": bool(sala.frase_actual)
        }

    async def enviar_estado_sala(self, sala_id: str):
        if sala_id not in self.salas_activas:
            return

        sala = self.salas_activas[sala_id]
        estado = self._estado_sala_para_envio(sala)

        await self.transmitir_a_sala(sala_id, estado)

    async def transmitir_a_sala(self, sala_id: str, mensaje: dict):
        if sala_id not in self.conexiones:
            return

        conexiones_validas = []

        for ws in list(self.conexiones[sala_id]):
            try:
                await ws.send_json(mensaje)
                conexiones_validas.append(ws)
            except Exception as e:
                try:
                    await ws.close()
                except:
                    pass
                print(f"[WS ERROR] {e}")

        self.conexiones[sala_id] = conexiones_validas

    
