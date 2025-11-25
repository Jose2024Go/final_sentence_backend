# backend/config.py

import os
from dotenv import load_dotenv

# Cargar variables de entorno desde el archivo .env
load_dotenv()

# Obtener la URI de MongoDB (usa localhost por defecto si no se define)
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017/final_silencio")