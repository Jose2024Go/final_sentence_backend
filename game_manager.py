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

        # ---------------------------
        # Cargar frases al iniciar
        # ---------------------------
        self.frases_terror = self._cargar_frases_mezcladas()

        print(f"✔ Total de frases disponibles: {len(self.frases_terror)}\n")

        # Monitores de tiempo
        self._monitores_tiempo: Dict[str, asyncio.Task] = {}

    # ============================================================
    #   CARGA DE FRASES (MongoDB + Hardcodeado)
    # ============================================================

    def _cargar_frases_mezcladas(self) -> List[Frase]:
        """Carga frases desde MongoDB y las combina con hardcodeadas, sin duplicados."""

        frases_finales: List[Frase] = []
        textos_usados = set()

        # 1. Intentar cargar desde MongoDB
        try:
            frases_db = self.base_datos.obtener_frases_terror(100)
        except:
            frases_db = []

        if frases_db:
            print(f"✔ MongoDB devolvió {len(frases_db)} frases.")
            for frase in frases_db:
                txt = frase.get("texto", "").strip()
                if txt and txt not in textos_usados:
                    textos_usados.add(txt)
                    frases_finales.append(Frase(
                        id=str(frase.get("_id")),
                        texto=txt,
                        dificultad=frase.get("dificultad", "media"),
                        categoria=frase.get("categoria", "terror")
                    ))
        else:
            print("⚠ MongoDB no devolvió frases — usando solo frases locales.")

        # 2. Lista hardcodeada
        frases_locales = [
            "La sombra avanzaba silenciosa por el pasillo.",
            "El espejo reflejó una habitación que no era la mía.",
            "Cada vez que parpadeaba, alguien estaba más cerca.",
            "Las luces titilaron y la figura estaba ya detrás de mí.",
            "Encontré una nota que decía: vuelve a dormir.",
            "El susurro decía mi nombre detrás de la puerta.",
            "Al abrir la puerta, nadie respondió al llamado.",
        ]

        # Agregar hardcodeadas si no están repetidas
        for i, t in enumerate(frases_locales):
            txt = t.strip()
            if txt not in textos_usados:
                textos_usados.add(txt)
                frases_finales.append(Frase(
                    id=f"local_{i}",
                    texto=txt,
                    dificultad="media",
                    categoria="terror"
                ))

        print(f"✔ Frases finales (mezcladas + únicas): {len(frases_finales)}")

        return frases_finales

    # ============================================================
    #   CREACIÓN DE SALA
    # ============================================================

    def _generar_codigo_sala(self) -> str:
        while True:
            codigo = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            if not self.base_datos.obtener_sala_por_codigo(codigo):
                return codigo

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
            tiempo_limite=45
        )

        self.salas_activas[sala_id] = sala
        self.conexiones[sala_id] = []

        try:
            self.base_datos.crear_sala(sala)
        except:
            pass

        return sala

    # ============================================================
    #   UNIR JUGADOR A SALA
    # ============================================================

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

        if not any(j.id == jugador.id for j in sala_activa.jugadores):
            sala_activa.jugadores.append(jugador)
            try:
                self.base_datos.actualizar_sala(sala_activa)
            except:
                pass

        asyncio.create_task(self.transmitir_a_sala(sala_activa.id, {
            "tipo": "jugador_unido",
            "jugador": self._serializar_jugador(jugador)
        }))

        return sala_activa

    # ============================================================
    #   INICIAR PARTIDA
    # ============================================================

    async def iniciar_partida(self, sala_id: str):
        if sala_id not in self.salas_activas:
            return

        sala = self.salas_activas[sala_id]

        if len(sala.jugadores) < 2:
            await self.transmitir_a_sala(sala_id, {
                "tipo": "error",
                "mensaje": "Se necesitan al menos 2 jugadores"
            })
            return

        sala.estado = "jugando"
        sala.ronda_actual += 1
        sala.tiempo_inicio = datetime.now()

        # --- SIEMPRE habrá una frase porque ya eliminamos duplicados ---
        sala.frase_actual = random.choice(self.frases_terror)

        print(f"🔥 Frase seleccionada en sala {sala_id}: {sala.frase_actual.texto}")

        # Inicializar jugadores
        for j in sala.jugadores:
            j.estado = EstadoJugador.JUGANDO
            j.progreso = 0
            j.ppm = 0
            j.errores = 0

        await self.transmitir_a_sala(sala_id, {
            "tipo": "partida_iniciada",
            "frase": sala.frase_actual.texto,
            "tiempo_limite": sala.tiempo_limite,
            "ronda_actual": sala.ronda_actual
        })

        # Monitor de tiempo
        if sala_id in self._monitores_tiempo:
            try:
                self._monitores_tiempo[sala_id].cancel()
            except:
                pass

        tarea = asyncio.create_task(self._monitor_tiempo_ronda(sala_id, sala.tiempo_limite))
        self._monitores_tiempo[sala_id] = tarea

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

        jugadores_vivos = [j for j in sala.jugadores if j.progreso < 100]

        ganador = None
        if len(jugadores_vivos) == 1:
            ganador = jugadores_vivos[0].id
        elif len(jugadores_vivos) == 0:
            ganador = sala.jugadores[0].id

        await self.finalizar_partida(sala_id, ganador)

    # ============================================================
    #   PROCESAR ESCRITURA
    # ============================================================

    async def procesar_escritura(self, jugador_id, sala_id, texto, tiempo_tomado):
        if sala_id not in self.salas_activas:
            return
        sala = self.salas_activas[sala_id]
        jugador = next((j for j in sala.jugadores if j.id == jugador_id), None)
        if not jugador or jugador.estado != EstadoJugador.JUGANDO:
            return

        frase_correcta = sala.frase_actual.texto

        if texto.strip() == frase_correcta.strip():
            jugador.progreso = 100
            palabras = len(frase_correcta.split())
            jugador.ppm = (palabras / tiempo_tomado) * 60

            await self.transmitir_a_sala(sala_id, {
                "tipo": "jugador_completo",
                "jugador_id": jugador_id,
                "ppm": jugador.ppm
            })
        else:
            jugador.errores += 1
            await self.transmitir_a_sala(sala_id, {
                "tipo": "jugador_error",
                "jugador_id": jugador_id,
                "errores": jugador.errores
            })

            if jugador.errores >= 3:
                jugador.estado = EstadoJugador.ELIMINADO
                await self.transmitir_a_sala(sala_id, {
                    "tipo": "jugador_eliminado",
                    "jugador_id": jugador_id
                })

        vivos = [j for j in sala.jugadores if j.estado == EstadoJugador.JUGANDO]
        if len(vivos) == 1:
            await self.finalizar_partida(sala_id, vivos[0].id)

    # ============================================================
    #   FINALIZAR PARTIDA
    # ============================================================

    async def finalizar_partida(self, sala_id, ganador_id):
        if sala_id not in self.salas_activas:
            return

        sala = self.salas_activas[sala_id]
        sala.estado = "finalizada"

        if sala_id in self._monitores_tiempo:
            try:
                self._monitores_tiempo[sala_id].cancel()
            except:
                pass

        await self.transmitir_a_sala(sala_id, {
            "tipo": "partida_finalizada",
            "ganador_id": ganador_id,
            "estadisticas": [
                {
                    "id": j.id,
                    "nombre": j.nombre,
                    "ppm": j.ppm,
                    "errores": j.errores,
                    "progreso": j.progreso
                }
                for j in sala.jugadores
            ]
        })

    # ============================================================
    #   ENVÍO DE MENSAJES
    # ============================================================

    async def transmitir_a_sala(self, sala_id: str, mensaje: dict):
        if sala_id not in self.conexiones:
            return

        conexiones_validas = []

        for ws in list(self.conexiones[sala_id]):
            try:
                await ws.send_json(mensaje)
                conexiones_validas.append(ws)
            except:
                try:
                    await ws.close()
                except:
                    pass

        self.conexiones[sala_id] = conexiones_validas

    # ============================================================
    #   SERIALIZAR
    # ============================================================

    def _serializar_jugador(self, jugador: Jugador) -> dict:
        return {
            "id": jugador.id,
            "nombre": jugador.nombre,
            "avatar": getattr(jugador, "avatar", "default"),
            "ppm": jugador.ppm,
            "progreso": jugador.progreso,
            "errores": jugador.errores,
            "estado": jugador.estado
        }

