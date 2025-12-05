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
        self.db = self.cliente.final_sentence

        self._crear_indices()
        self.inicializar_frases_terror()

    # -------------------------------------------------------
    # ÍNDICES
    # -------------------------------------------------------
    def _crear_indices(self):
        self.db.jugadores.create_index("id", unique=True)
        self.db.jugadores.create_index("nombre", unique=True)   # 🔥 nombre único
        self.db.salas.create_index("id", unique=True)
        self.db.salas.create_index("codigo", unique=True)
        self.db.partidas.create_index("fecha")

    # -------------------------------------------------------
    # FRASES DE TERROR
    # -------------------------------------------------------
    def inicializar_frases_terror(self):
        if self.db.frases.count_documents({}) > 0:
            return

        frases = [
            { "texto": "La sombra avanzaba silenciosa por el pasillo.", "dificultad": "media", "categoria": "terror" },
            { "texto": "Al abrir la puerta, nadie respondió al llamado.", "dificultad": "baja", "categoria": "terror" },
            { "texto": "El susurro decía mi nombre al oído sin moverse nadie.", "dificultad": "media", "categoria": "terror" },
            { "texto": "Las luces titilaron y la figura estaba ya detrás de mí.", "dificultad": "alta", "categoria": "terror" },
            { "texto": "No había teléfonos en la casa, pero alguien marcó desde adentro.", "dificultad": "media", "categoria": "terror" },
            { "texto": "Encontré una nota en mi almohada que decía: vuelve a dormir.", "dificultad": "baja", "categoria": "terror" },
            { "texto": "El espejo reflejó una habitación que no era la mía.", "dificultad": "media", "categoria": "terror" },
            { "texto": "Cada vez que parpadeaba, alguien estaba más cerca.", "dificultad": "alta", "categoria": "terror" },
            { "texto": "La casa respiraba y yo no estaba dentro de ella.", "dificultad": "alta", "categoria": "terror" },
            { "texto": "Las marcas en la pared formaban mi nombre, escrito de atrás hacia adelante.", "dificultad": "alta", "categoria": "terror" }
        ]

        for frase in frases:
            frase["fecha_agregada"] = datetime.now()
            self.db.frases.insert_one(frase)

    # -------------------------------------------------------
    # JUGADORES
    # -------------------------------------------------------
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

    def obtener_jugador_por_nombre(self, nombre: str):
        return self.db.jugadores.find_one({"nombre": nombre})

    # -------------------------------------------------------
    # SALAS
    # -------------------------------------------------------
    def crear_sala(self, sala: Sala) -> str:
        data = sala.dict()
        data["fecha_creacion"] = datetime.now()
        res = self.db.salas.insert_one(data)
        return str(res.inserted_id)

    def obtener_sala(self, sala_id: str) -> Optional[Sala]:
        data = self.db.salas.find_one({"id": sala_id})
        return Sala(**data) if data else None

    def obtener_sala_por_codigo(self, codigo: str) -> Optional[Sala]:
        data = self.db.salas.find_one({"codigo": codigo})
        return Sala(**data) if data else None

    def actualizar_sala(self, sala: Sala):
        self.db.salas.update_one({"id": sala.id}, {"$set": sala.dict()})

    def eliminar_sala(self, sala_id: str):
        self.db.salas.delete_one({"id": sala_id})

    # -------------------------------------------------------
    # PARTIDAS
    # -------------------------------------------------------
    def guardar_partida(self, partida: Partida) -> str:
        data = partida.dict()
        res = self.db.partidas.insert_one(data)
        return str(res.inserted_id)

    def obtener_estadisticas_jugador(self, jugador_id: str) -> EstadisticasJugador:
        pipeline = [
            {"$match": {"jugadores.id": jugador_id}},
            {"$unwind": "$jugadores"},
            {"$match": {"jugadores.id": jugador_id}},
            {"$group": {
                "_id": "$jugadores.id",
                "nombre": {"$first": "$jugadores.nombre"},
                "partidas_jugadas": {"$sum": 1},
                "partidas_ganadas": {"$sum": {"$cond": [{"$eq": ["$ganador", jugador_id]}, 1, 0]}},
                "ppm_promedio": {"$avg": "$jugadores.ppm"},
                "mejor_ppm": {"$max": "$jugadores.ppm"},
                "total_errores": {"$sum": "$jugadores.errores"},
            }}
        ]

        r = list(self.db.partidas.aggregate(pipeline))
        if r:
            data = r[0]
            return EstadisticasJugador(
                jugador_id=data["_id"],
                nombre=data["nombre"],
                partidas_jugadas=data["partidas_jugadas"],
                partidas_ganadas=data["partidas_ganadas"],
                ppm_promedio=round(data["ppm_promedio"], 2),
                mejor_ppm=round(data["mejor_ppm"], 2),
                total_errores=data["total_errores"]
            )

        return EstadisticasJugador(jugador_id=jugador_id, nombre="")

