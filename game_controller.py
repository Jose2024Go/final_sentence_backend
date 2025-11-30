#Backend/game_controller.py
from game_manager import AdministradorJuego

# Instancia única del juego compartida por HTTP y WebSocket
juego = AdministradorJuego()
