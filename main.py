import sys
import logging
from core.state import state

logger = logging.getLogger("Orquestador")


def main() -> None:
    use_tui = "--tui" in sys.argv

    if use_tui:
        from ui.textual_app import run_tui as run_app
        logger.info("[Sistema] Arrancando Music Grabber TUI...")
    else:
        from ui.gui_app import run_gui as run_app
        logger.info("[Sistema] Arrancando Music Grabber GUI...")

    try:
        run_app()
    except KeyboardInterrupt:
        logger.warning("\n[Sistema] Cierre forzado detectado (Ctrl+C).")
    except Exception as e:
        logger.critical(f"[Sistema] Fallo crítico en hilo principal: {e}", exc_info=True)
    finally:
        state.is_running = False
        logger.info("[Sistema] Apagado limpio completado.")


if __name__ == "__main__":
    main()
