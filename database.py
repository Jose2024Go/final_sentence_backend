# backend/database.py

from pymongo import MongoClient
from models import Jugador, Sala, Partida, EstadisticasJugador
import os
from datetime import datetime
from typing import Optional

class BaseDatos:
    def __init__(self):
        self.conexion_string = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
        self.cliente = MongoClient(self.conexion_string)
        self.db = self.cliente.final_silencio
        self._crear_indices()

    def _crear_indices(self):
        """Crea índices para mejor rendimiento"""
        self.db.jugadores.create_index("id", unique=True)
        self.db.salas.create_index("id", unique=True)
        self.db.salas.create_index("codigo", unique=True)
        self.db.partidas.create_index("fecha")

    # --- JUGADORES ---
    def guardar_jugador(self, jugador: Jugador):
        jugador_dict = jugador.dict()
        jugador_dict["ultima_conexion"] = datetime.now()
        self.db.jugadores.update_one(
            {"id": jugador.id},
            {"$set": jugador_dict},
            upsert=True
        )

    def obtener_jugador(self, jugador_id: str) -> Optional[Jugador]:
        datos = self.db.jugadores.find_one({"id": jugador_id})
        return Jugador(**datos) if datos else None

    # --- SALAS ---
    def crear_sala(self, sala: Sala) -> str:
        sala_dict = sala.dict()
        sala_dict["fecha_creacion"] = datetime.now()
        resultado = self.db.salas.insert_one(sala_dict)
        return str(resultado.inserted_id)

    def obtener_sala(self, sala_id: str) -> Optional[Sala]:
        datos = self.db.salas.find_one({"id": sala_id})
        return Sala(**datos) if datos else None

    def obtener_sala_por_codigo(self, codigo: str) -> Optional[Sala]:
        datos = self.db.salas.find_one({"codigo": codigo})
        return Sala(**datos) if datos else None

    def actualizar_sala(self, sala: Sala):
        self.db.salas.update_one(
            {"id": sala.id},
            {"$set": sala.dict()}
        )

    def eliminar_sala(self, sala_id: str):
        self.db.salas.delete_one({"id": sala_id})

    # --- PARTIDAS ---
    def guardar_partida(self, partida: Partida) -> str:
        partida_dict = partida.dict()
        resultado = self.db.partidas.insert_one(partida_dict)
        return str(resultado.inserted_id)

    def obtener_estadisticas_jugador(self, jugador_id: str) -> EstadisticasJugador:
        pipeline = [
            {"$match": {"jugadores.id": jugador_id}},
            {"$unwind": "$jugadores"},
            {"$match": {"jugadores.id": jugador_id}},
            {"$group": {
                "_id": "$jugadores.id",
                "nombre": {"$first": "$jugadores.nombre"},
                "partidas_jugadas": {"$sum": 1},
                "partidas_ganadas": {
                    "$sum": {"$cond": [{"$eq": ["$ganador", jugador_id]}, 1, 0]}
                },
                "ppm_promedio": {"$avg": "$jugadores.ppm"},
                "mejor_ppm": {"$max": "$jugadores.ppm"},
                "total_errores": {"$sum": "$jugadores.errores"}
            }}
        ]
        resultado = list(self.db.partidas.aggregate(pipeline))
        if resultado:
            stats = resultado[0]
            return EstadisticasJugador(
                jugador_id=stats["_id"],
                nombre=stats["nombre"],
                partidas_jugadas=stats["partidas_jugadas"],
                partidas_ganadas=stats["partidas_ganadas"],
                ppm_promedio=round(stats["ppm_promedio"], 2),
                mejor_ppm=round(stats["mejor_ppm"], 2),
                total_errores=stats["total_errores"]
            )
        return EstadisticasJugador(jugador_id=jugador_id, nombre="")

    # --- FRASES ---
    def obtener_frases_terror(self, cantidad: int = 10) -> list:
        return list(self.db.frases.find({"categoria": "terror"}).limit(cantidad))

    def agregar_frase(self, texto: str, dificultad: str = "media", categoria: str = "terror"):
        frase = {
            "texto": texto,
            "dificultad": dificultad,
            "categoria": categoria,
            "fecha_agregada": datetime.now()
        }
        self.db.frases.insert_one(frase)