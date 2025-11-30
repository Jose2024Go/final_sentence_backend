from database import BaseDatos

print("🔍 Probando conexión a MongoDB Atlas...")

try:
    db = BaseDatos()
    frases = db.obtener_frases_terror(10)

    print("✔ Conexión exitosa a MongoDB Atlas")
    print(f"✔ Se encontraron {len(frases)} frases:\n")

    for f in frases:
        print("-", f.get("texto", "(sin texto)"))

except Exception as e:
    print("❌ ERROR conectando a MongoDB:")
    print(e)
