# backend/main.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import bcrypt

from models import Jugador, TipoSala
from game_controller import juego
from routes_ws import router as ws_router

app = FastAPI(title="Final Sentence API", version="1.0.0")

# ------------------------ CORS ------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------ ROUTES WS ------------------------
app.include_router(ws_router)

# ------------------------ AUTH ------------------------
@app.post("/auth/register")
async def registrar(nombre: str, password: str):

    if len(password) < 4:
        raise HTTPException(400, "La contraseña debe tener mínimo 4 caracteres")

    # nombre único
    existente = juego.base_datos.obtener_jugador_por_nombre(nombre)
    if existente:
        raise HTTPException(409, "Ese nombre ya está en uso")

    jugador_id = f"jugador_{abs(hash(nombre)) % (10**12)}"
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    jugador = Jugador(
        id=jugador_id,
        nombre=nombre,
        avatar="default",
        password_hash=password_hash
    )

    juego.base_datos.guardar_jugador(jugador)

    return {
        "mensaje": "Cuenta creada",
        "jugador": {
            "id": jugador.id,
            "nombre": jugador.nombre,
            "avatar": jugador.avatar
        }
    }


@app.post("/auth/login")
async def login(nombre: str, password: str):
    data = juego.base_datos.obtener_jugador_por_nombre(nombre)
    if not data:
        raise HTTPException(404, "Usuario no encontrado")

    password_hash = data.get("password_hash", "")

    if not bcrypt.checkpw(password.encode(), password_hash.encode()):
        raise HTTPException(401, "Contraseña incorrecta")

    return {
        "mensaje": "Login correcto",
        "jugador": {
            "id": data["id"],
            "nombre": data["nombre"],
            "avatar": data["avatar"]
        }
    }

# ------------------------ HTTP ENDPOINTS ------------------------
@app.post("/jugador/nuevo")
async def crear_jugador(nombre: str, avatar: str = "default"):
    jugador_id = f"jugador_{abs(hash(nombre)) % (10**12)}"
    jugador = Jugador(id=jugador_id, nombre=nombre, avatar=avatar)
    juego.base_datos.guardar_jugador(jugador)
    return jugador.dict()


@app.get("/jugador/{jugador_id}/estadisticas")
async def obtener_estadisticas(jugador_id: str):
    stats = juego.base_datos.obtener_estadisticas_jugador(jugador_id)
    return stats.dict()


@app.post("/sala/crear")
async def crear_sala(jugador_id: str, tipo: TipoSala, max_jugadores: int = 10):
    jugador = juego.base_datos.obtener_jugador(jugador_id)
    sala = juego.crear_sala(jugador, tipo, max_jugadores)
    return sala.dict()


@app.post("/sala/unir")
async def unir_sala(jugador_id: str, codigo_sala: str):
    jugador = juego.base_datos.obtener_jugador(jugador_id)
    sala = juego.unir_sala(jugador, codigo_sala)
    return sala.dict() if sala else {"error": "Sala no encontrada"}


@app.post("/sala/{sala_id}/iniciar")
async def iniciar_partida(sala_id: str):
    await juego.iniciar_partida(sala_id)
    return {"mensaje": "Partida iniciada"}


# ------------------------ ROOT ------------------------
@app.get("/")
async def root():
    return {"status": "ok", "message": "Final Sentence Backend activo!"}
