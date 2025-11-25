#backend\game_manager.py
import asyncio
import random
import string
from datetime import datetime
from models import Sala, Jugador, Frase, EstadoJugador, TipoSala
from database import BaseDatos
from typing import Dict, List, Optional

class AdministradorJuego:
    def __init__(self):
        self.base_datos = BaseDatos()
        self.salas_activas: Dict[str, Sala] = {}
        self.conexiones: Dict[str, List] = {}  # sala_id -> [websockets]
        self.frases_terror = self._cargar_frases_terror()

    def _cargar_frases_terror(self) -> List[Frase]:
        """Carga frases de terror desde la base de datos"""
        frases_db = self.base_datos.obtener_frases_terror(20)
        return [
            Frase(
                id=str(i),
                texto=frase["texto"],
                dificultad=frase.get("dificultad", "media"),
                categoria=frase.get("categoria", "terror")
            )
            for i, frase in enumerate(frases_db)
        ]

    def _generar_codigo_sala(self) -> str:
        """Genera un código único de 6 caracteres para la sala"""
        while True:
            codigo = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            if not self.base_datos.obtener_sala_por_codigo(codigo):
                return codigo

    def crear_sala(self, jugador_anfitrion: Jugador, tipo: TipoSala, max_jugadores: int = 10) -> Sala:
        sala_id = f"sala_{datetime.now().timestamp()}"
        codigo = self._generar_codigo_sala()
        sala = Sala(
            id=sala_id,
            codigo=codigo,
            tipo=tipo,
            jugadores=[jugador_anfitrion],
            jugador_anfitrion=jugador_anfitrion.id,
            max_jugadores=max_jugadores,
            estado="esperando"
        )
        self.salas_activas[sala_id] = sala
        self.conexiones[sala_id] = []
        self.base_datos.crear_sala(sala)
        return sala

    def unir_sala(self, jugador: Jugador, codigo_sala: str) -> Optional[Sala]:
        sala = self.base_datos.obtener_sala_por_codigo(codigo_sala)
        if not sala or sala.id not in self.salas_activas:
            return None
        sala_activa = self.salas_activas[sala.id]
        if len(sala_activa.jugadores) >= sala_activa.max_jugadores:
            return None
        if any(j.id == jugador.id for j in sala_activa.jugadores):
            return sala_activa
        sala_activa.jugadores.append(jugador)
        self.base_datos.actualizar_sala(sala_activa)
        return sala_activa

    def abandonar_sala(self, jugador_id: str, sala_id: str):
        if sala_id in self.salas_activas:
            sala = self.salas_activas[sala_id]
            sala.jugadores = [j for j in sala.jugadores if j.id != jugador_id]
            if not sala.jugadores:
                self.eliminar_sala(sala_id)
            else:
                self.base_datos.actualizar_sala(sala)

    def eliminar_sala(self, sala_id: str):
        if sala_id in self.salas_activas:
            del self.salas_activas[sala_id]
        if sala_id in self.conexiones:
            del self.conexiones[sala_id]
        self.base_datos.eliminar_sala(sala_id)

    async def iniciar_partida(self, sala_id: str):
        if sala_id not in self.salas_activas:
            return
        sala = self.salas_activas[sala_id]
        sala.estado = "jugando"
        sala.ronda_actual = 1
        sala.tiempo_inicio = datetime.now()
        sala.frase_actual = random.choice(self.frases_terror)
        self.base_datos.actualizar_sala(sala)
        await self.transmitir_a_sala(sala_id, {
            "tipo": "partida_iniciada",
            "frase": sala.frase_actual.texto,
            "tiempo_limite": sala.tiempo_limite
        })

    async def procesar_escritura(self, jugador_id: str, sala_id: str, texto: str, tiempo_tomado: float):
        if sala_id not in self.salas_activas:
            return
        sala = self.salas_activas[sala_id]
        jugador = next((j for j in sala.jugadores if j.id == jugador_id), None)
        if not jugador or jugador.estado != EstadoJugador.JUGANDO:
            return

        frase_correcta = sala.frase_actual.texto
        es_correcto = texto.strip() == frase_correcta.strip()
        palabras = len(frase_correcta.split())
        ppm = (palabras / tiempo_tomado) * 60 if tiempo_tomado > 0 else 0

        if es_correcto:
            jugador.ppm = ppm
            jugador.progreso = 100
            await self.transmitir_a_sala(sala_id, {
                "tipo": "jugador_completo",
                "jugador_id": jugador_id,
                "ppm": ppm,
                "tiempo_tomado": tiempo_tomado
            })
        else:
            jugador.errores += 1
            jugador.progreso = max(0, jugador.progreso - 10)
            await self.transmitir_a_sala(sala_id, {
                "tipo": "jugador_error",
                "jugador_id": jugador_id,
                "errores_actuales": jugador.errores
            })
            if jugador.errores >= 3:
                await self.eliminar_jugador(jugador_id, sala_id)

        self.base_datos.actualizar_sala(sala)

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
            jugadores_vivos = [j for j in sala.jugadores if j.estado == EstadoJugador.JUGANDO]
            if len(jugadores_vivos) == 1:
                await self.finalizar_partida(sala_id, jugadores_vivos[0].id)
            elif len(jugadores_vivos) == 0:
                await self.finalizar_partida(sala_id, None)

    async def finalizar_partida(self, sala_id: str, ganador_id: Optional[str]):
        if sala_id not in self.salas_activas:
            return
        sala = self.salas_activas[sala_id]
        sala.estado = "finalizada"
        from models import Partida
        partida = Partida(
            id=f"partida_{datetime.now().timestamp()}",
            sala_id=sala_id,
            jugadores=sala.jugadores,
            frases_usadas=[sala.frase_actual] if sala.frase_actual else [],
            ganador=ganador_id,
            duracion=(datetime.now() - sala.tiempo_inicio).seconds if sala.tiempo_inicio else 0,
            fecha=datetime.now()
        )
        self.base_datos.guardar_partida(partida)
        await self.transmitir_a_sala(sala_id, {
            "tipo": "partida_finalizada",
            "ganador_id": ganador_id,
            "estadisticas": [j.dict() for j in sala.jugadores]
        })
        asyncio.create_task(self._eliminar_sala_despues(sala_id, 30))

    async def _eliminar_sala_despues(self, sala_id: str, segundos: int):
        await asyncio.sleep(segundos)
        self.eliminar_sala(sala_id)

    async def transmitir_a_sala(self, sala_id: str, mensaje: dict):
        if sala_id in self.conexiones:
            conexiones_validas = []
            for websocket in self.conexiones[sala_id]:
                try:
                    await websocket.send_json(mensaje)
                    conexiones_validas.append(websocket)
                except:
                    pass
            self.conexiones[sala_id] = conexiones_validas