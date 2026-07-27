import logging
from core.state import state
from ui.gui_app import run_gui

logger = logging.getLogger("Orquestador")


def main() -> None:
    logger.info("[Sistema] Arrancando Music Grabber GUI...")
    try:
        run_gui()
    except KeyboardInterrupt:
        logger.warning("\n[Sistema] Cierre forzado detectado (Ctrl+C).")
    except Exception as e:
        logger.critical(f"[Sistema] Fallo crítico en hilo principal: {e}", exc_info=True)
    finally:
        state.is_running = False
        logger.info("[Sistema] Apagado limpio completado.")


if __name__ == "__main__":
    main()
