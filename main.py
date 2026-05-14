import logging
from ui.textual_app import run_tui
from core.state import state, APP_DATA_DIR

logger = logging.getLogger("Orquestador")

def main() -> None:
    logger.info("[Sistema] Arrancando Music Grabber TUI...")
    
    try:
        # Iniciamos directamente la interfaz textual
        run_tui()
    except KeyboardInterrupt:
        logger.warning("\n[Sistema] Cierre forzado detectado (Ctrl+C).")
    except Exception as e:
        logger.critical(f"[Sistema] Fallo crítico en hilo principal: {e}", exc_info=True)
    finally:
        state.is_running = False
        logger.info("[Sistema] Apagado limpio completado.")

if __name__ == "__main__":
    main()