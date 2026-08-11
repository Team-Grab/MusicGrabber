# Music Grabber — Plan de pruebas

Documento vivo. Marca cada prueba con `[x]` cuando la valides en Nobara. Si algo falla, anota la línea en tu mensaje a Claude y se arregla antes de seguir.

Hay tres bloques: **arranque limpio**, **uso normal** y **regresión de los puntos ya arreglados**. Ve en orden — cada bloque depende del anterior.

---

## Antes de empezar

```bash
rm -rf ~/MusicGrabber-test
cp -r ~/Claude-Cowork/RESULTADOS/musicgrabber-linux ~/MusicGrabber-test
cd ~/MusicGrabber-test && bash install.sh
```

Para forzar el comportamiento de primer arranque, antes de ejecutar:

```bash
rm -f ~/.local/share/MusicGrabber/config.json
rm -f ~/.local/share/MusicGrabber/library.db
rm -rf ~/.local/share/MusicGrabber/_inbox
```

Después: `musicgrabber`.

---

## Bloque A — Arranque limpio (primer uso)

- [x] **A1. Diálogo de bienvenida.** Aparece la ventana "Bienvenido a Music Grabber" con: explicación, campo de carpeta con valor `~/Music`, botón "Examinar...", combos Formato y Calidad, checkbox MB (desmarcado por defecto) y dos botones (Salir / Empezar).
- [x] **A2. Crear carpeta nueva.** Cambia la ruta a una carpeta que no exista (p. ej. `~/Music/TestMG`). Pulsa Empezar. La carpeta se crea y el diálogo desaparece.
- [x] **A3. La app arranca con tema oscuro.** Fondo oscuro, texto claro, sin franjas blancas raras.
- [x] **A4. Indicador MB visible en barra superior.** Aparece "MB: OFF" en gris (porque dejaste el checkbox desmarcado).
        
---

## Bloque B — Descarga sin MusicBrainz

Para esto, MB debe estar OFF (verifica en el indicador superior).

- [x] **B1. Pega una URL de YouTube** (un vídeo solo) en el campo y pulsa Descargar. En el log de eventos aparecen líneas con timestamp: "Analizando enlace", "Lista detectada", "Descargando: ...", "Extrayendo audio", "Tags base aplicados", "Archivo en biblioteca: Artista/Álbum/Título.mp3".
- [x] **B2. La barra de progreso inferior** se mueve mientras descarga y queda al 100 % al terminar.
- [x] **B3. Al terminar, aparece el resumen de lote** entre dos líneas `──────`, con Tiempo, Descargas, y (si los hay) Duplicados y Fallos.
- [x] **B4. El reloj "Sesión"** se detiene cuando termina y queda en `—`.
- [x] **B5. Descarga una playlist de YouTube** (10-50 pistas). La primera descarga arranca en segundos, no minutos. Cada pista aparece en el log según va terminando.
- [x] **B6. Vista Biblioteca.** Cambia a la pestaña Biblioteca (sidebar). Las pistas descargadas aparecen. La columna MB está vacía en todas (sin estrellita).
- [x] **B7. Sidebar con conteos.** Aparece el número total de pistas junto a "Biblioteca". Si las pistas tienen género en los tags, aparecen secciones en GÉNEROS con conteos.

---

## Bloque C — Ordenación y filtrado

- [x] **C1. Click en el header "Título"** ordena alfabéticamente ascendente (▲ aparece junto al nombre).
- [x] **C2. Click otra vez** ordena descendente (▼).
- [x] **C3. Click en "Artista"** cambia la ordenación. El ▲/▼ se mueve a Artista.
- [x] **C4. Click en "Duración"** ordena por segundos reales, no alfabéticamente. (3:50 viene antes que 12:00).
- [x] **C5. Filtro de búsqueda** (caja arriba de la tabla). Escribe parte de un título: la tabla se filtra en vivo.
- [x] **C6. Click en un género** de la sidebar. La tabla muestra solo pistas de ese género. El badge superior dice "Género: X". Click en "Biblioteca" para limpiar.

---

## Bloque D — Menú contextual y edición manual

**Nota (11/08/2026):** D4 se reportó con dos fallos independientes: (a) el
diálogo se abría en blanco/vacío hasta redimensionar la ventana, (b) el
tamaño fijo tapaba los botones "Aplicar candidato" y "Cancelar". Causa: en
`ManualMBSearchDialog` (`ui/gui_app.py:605-668`) se llamaba `grab_set()`
antes de construir los widgets — mismo patrón que causó los bugs de A4
(Ajustes) y L2 (Sleep timer). Corregido en el commit `53c8962` moviendo la
construcción de widgets a un método `_build()` bajo `try/except`, con
`grab_set()` al final. Validado el mismo día instanciando el diálogo real
y capturando la ventana: todo el contenido visible de inmediato, sin
redimensionar.

- [x] **D1. Click derecho** sobre una pista en la biblioteca abre menú con: Reproducir, Editar tags, Buscar en MB, Reenriquecer, Abrir carpeta, Quitar del índice, Eliminar archivo.
- [x] **D2. Reproducir.** Lanza el reproductor por defecto del sistema con esa pista.
- [x] **D3. Editar tags a mano.** Abre diálogo con campos Title/Artist/Album/Year/Track/Genre rellenos con los valores actuales. Cambia el título, pulsa Guardar. La tabla se actualiza.
- [x] **D4. Buscar en MB manualmente.** Abre diálogo con título y artista editables (debe abrirse con contenido visible de inmediato, sin necesidad de redimensionar). Pulsa "Buscar en MusicBrainz". Aparecen candidatos (puede tardar 1 s). Selecciona uno y pulsa "Aplicar candidato". La pista pasa a tener ✦ en la columna MB.
- [x] **D5. Abrir carpeta contenedora.** Abre el navegador de archivos en la carpeta de la pista.
- [x] **D6. Quitar del índice.** Pide confirmación. Si aceptas, la pista desaparece de la tabla pero el archivo físico sigue ahí.
- [x] **D7. Eliminar archivo del disco.** Pide confirmación con icono de warning. Si aceptas, el archivo desaparece de disco Y de la tabla.

---

## Bloque E — Atajos de teclado

Estando en la vista Biblioteca con una pista seleccionada:

- [x] **E1. F5** refresca la biblioteca y purga entradas obsoletas.
- [x] **E2. F2** abre el diálogo Editar tags a mano.
- [x] **E3. Enter** reproduce la pista seleccionada.
- [x] **E4. Del** quita la pista del índice (con confirmación).
- [x] **E5. Ctrl+D** salta a la vista Descargar.
- [x] **E6. Ctrl+L** salta a la vista Biblioteca.
- [x] **E7. Ctrl+R** salta a la vista Sin metadatos.
- [x] **E8. Enter en el campo de URL** sigue descargando, no reproduce nada raro (el atajo respeta los inputs de texto).

---

## Bloque F — MusicBrainz activo

- [x] **F1. Activa MB en Ajustes** (botón superior derecho). Marca el checkbox "Mejorar tags con MusicBrainz" y guarda.
- [x] **F2. El indicador MB cambia a "MB: ON"** en verde.
- [x] **F3. Descarga una pista conocida** (canción mainstream que claramente está en MB). Al terminar, en el log aparece "MB match (score N): ...". La pista en la biblioteca tiene ✦ en la columna MB.
- [x] **F4. Descarga una pista oscura** (poco probable que esté en MB). En el log aparece "MB ambiguo" o "MB sin match: ... (a bandeja de revisión)". La pista aparece en la vista "Sin metadatos".

---

## Bloque G — Bandeja de revisión ("Sin metadatos")

- [x] **G1.** Las pistas listadas vienen con `[ambiguous]` o `[no_match]` entre corchetes.
- [x] **G2. Selecciona una pista.** En el panel derecho aparecen los candidatos de MusicBrainz (puede estar vacío si fue no_match).
- [x] **G3. Aplicar candidato seleccionado.** Si hay candidatos, selecciona uno y aplícalo. La pista desaparece de la bandeja, aparece en la biblioteca con ✦.
  **Nota (11/08/2026):** nunca se había podido validar porque no se lograba generar un caso `[ambiguous]` real navegando YouTube. Se probó con un caso fabricado end-to-end (MP3 real + entrada `pending_review` con 2 candidatos insertada directamente en `library.db`, app real corriendo, flujo ejecutado sobre los widgets reales — sin mocks): la pista se seleccionó, los 2 candidatos aparecieron en la tabla, al aplicar el elegido se escribieron los tags en el archivo MP3 real (verificado con mutagen), `mb_recording_id` quedó correcto en `tracks`, la entrada se borró de `pending_review` y la pista apareció en Biblioteca (1) y en Géneros (Pop: 1). Sin bugs encontrados.
- [x] **G4. Buscar manualmente en MB.** Edita título/artista y pulsa Buscar. Aparecen nuevos candidatos. Aplica uno.
- [x] **G5. Editar tags a mano.** Edita campos manualmente y guarda. La pista sale de la bandeja.
- [x] **G6. Dejar como está.** Quita la pista de la bandeja sin tocar tags.

---

## Bloque H — Persistencia y robustez

- [x] **H1. Redimensiona y mueve la ventana.** Ciérrala. Vuelve a abrirla. Aparece exactamente del mismo tamaño y posición.
- [x] **H2. Cambia el orden de columnas** (por Año descendente, por ejemplo). Cierra la app y reábrela. La biblioteca vuelve a abrirse con ese mismo orden.
- [x] **H3. Borra archivos a mano** desde el navegador de archivos (sin pasar por la app). Cierra la app. Vuelve a abrirla: las entradas obsoletas se purgan automáticamente al arrancar (verás "Sincronización inicial: N entradas obsoletas..." en el log).
- [x] **H4. Vaciar índice.** En la vista Biblioteca, botón "Vaciar índice". Pide confirmación, borra todo del índice pero no toca archivos.
- [x] **H5. Escanear biblioteca.** En la barra superior. Reindexa los archivos físicos. Las pistas vuelven a aparecer.

---

## Bloque I — Enriquecimiento masivo

- [x] **I1. Botón "Enriquecer con MusicBrainz"** sobre una biblioteca con pistas sin ✦. Aparece confirmación con número de pistas y tiempo estimado.
- [x] **I2. Modal de progreso.** Barra de progreso, contador X/N, label "Procesando: TÍTULO", label gris con resultado de la última (✔ Match / ✗ Sin match / ✗ Error). Botón Cancelar funcional.
- [x] **I3.** Al terminar, label dice "Proceso terminado. X de N pistas enriquecidas con éxito". La tabla se refresca y muestra ✦ en las matched.

---

## Bloque J — Reproductor integrado (fase 2a)

**Prerrequisito.** VLC instalado: `sudo dnf install vlc`. Si no lo tienes, los controles del reproductor aparecerán deshabilitados y verás un aviso en el log.

- [x] **J0. VLC detectado.** En la barra inferior aparece el reproductor: carátula placeholder `♪`, título "—", controles ⏮ ▶ ⏭ ■, slider de progreso 0:00 / 0:00, control de volumen. No hay aviso de VLC inactivo en el log.
- [x] **J1. Doble clic en pista de la biblioteca.** La pista empieza a sonar. El título y el artista aparecen en la barra inferior. El botón central pasa a ⏸.
- [x] **J2. Slider de progreso.** Avanza solo mientras suena. Los textos `0:00` cambian a tiempo actual / duración total.
- [x] **J3. Pausa.** Click en ⏸ pausa. Click otra vez (ahora ▶) reanuda.
- [x] **J4. Siguiente (⏭).** Salta a la siguiente pista de la vista activa. Si era la última, para.
- [x] **J5. Anterior (⏮).** Si llevas más de 3 s, reinicia la pista actual. Si llevas menos, salta a la anterior.
- [x] **J6. Stop (■).** Detiene la reproducción y deja la barra en 0.
- [x] **J7. Seek con el slider.** (a) drag o (b) click en cualquier punto: la pista salta a la fracción correcta. Debounce de 80 ms evita "blips" con clicks rápidos.
- [x] **J8. Volumen.** Slider con click + drag. Click en cualquier punto va al valor exacto.
- [x] **J9. Filtros de biblioteca.** Filtro por texto + filtro por género (click en sidebar). Botón "Limpiar filtros" vacía ambos. La búsqueda ignora acentos ("tio" encuentra "Tío Sam").
- [x] **J10. Menú contextual: "Añadir al final de la cola"** sobre otra pista mientras una está sonando. La pista sonando no cambia. Al pulsar ⏭, se reproduce la que añadiste.
- [x] **J11. Menú contextual: "Reproducir a continuación"** sobre otra pista. Al pulsar ⏭, suena esa antes que la siguiente de la cola original.
- [x] **J12. Menú contextual: "Abrir con reproductor externo"** lanza el reproductor del sistema (xdg-open) y NO interfiere con el reproductor interno.
- [x] **J13. Auto-next al terminar pista.** Deja una pista corta llegar al final sola. La siguiente arranca automáticamente.
- [x] **J14. Persistencia de play_count (informativa, aplazable).** El conteo de reproducciones se guarda en la DB para usarlo en fase 2d (smart playlists). No tiene interfaz visible aún. Si quieres verificarlo, abre una terminal con la app cerrada y ejecuta: `sqlite3 ~/.local/share/MusicGrabber/library.db "SELECT title, play_count FROM tracks WHERE play_count > 0 LIMIT 10"`. Si has reproducido algo, debería listar pistas con su contador. Si no sale nada o no quieres tocar sqlite, deja este check sin marcar.

---

## Bloque K — Shuffle, repeat y panel de cola (fase 2b)

Empieza con una pista en reproducción y al menos 6-8 pistas en la cola (doble clic en una pista de la biblioteca).

- [x] **K0. Botones nuevos visibles** en la barra inferior: **"Mezclar"** a la izquierda del transporte, **"Repetir"** y **"Cola"** a la derecha. Inicialmente en gris.
- [x] **K1. Activar shuffle.** Click en "Mezclar" → cambia a color azul (accent). El log de eventos no muestra nada (el cambio es solo de estado).
- [x] **K2. Pulsa ⏭ varias veces con shuffle ON.** Las siguientes pistas suenan en orden aleatorio.
- [x] **K3. Desactivar shuffle.** Click en "Mezclar" de nuevo → vuelve a gris. La cola se reordena al orden original, pero la pista actual sigue siendo la misma (relocalizada a su sitio canónico).
- [x] **K4. Repeat off → list.** Click en "Repetir" → texto cambia a "Repetir lista" en azul. Cuando la cola llegue a la última pista y termine, vuelve a la primera y sigue sonando.
- [x] **K5. Repeat list → track.** Click otra vez → texto cambia a "Repetir pista" en azul. Al terminar la pista actual, la misma vuelve a sonar.
- [x] **K6. Repeat track → off.** Click otra vez → vuelve a "Repetir" en gris. Al terminar la última pista, la reproducción se detiene.
- [x] **K7. Abrir panel de cola.** Click en "Cola" → se abre una ventana "Cola de reproducción" con tabla `# | Título | Artista`. La pista en reproducción aparece resaltada en azul.
- [x] **K8. Selección + botones inferiores.** Selecciona una pista en el panel. Pulsa "↑ Subir" → la pista sube una posición. "↓ Bajar" → baja una. "Quitar" → desaparece de la cola.
- [x] **K9. Drag & drop.** Mantén pulsado el ratón sobre una pista, arrastra a otra posición de la cola, suelta. La pista se reordena.
- [x] **K10. Reproducir aquí.** Selecciona una pista cualquiera, pulsa "Reproducir aquí" (o doble clic). La reproducción salta a esa pista.
- [x] **K11. Vaciar cola.** Botón "Vaciar" arriba a la derecha del panel → confirmación → la cola queda vacía, reproducción se detiene.
- [x] **K12. Auto-refresco del panel.** Con el panel abierto, pulsa ⏭ en la barra inferior. La fila resaltada se actualiza sola (cada ~800 ms).
- [x] **K13. Quitar pista actual.** Con una pista sonando, quítala de la cola con "Quitar". La siguiente pista de la cola arranca automáticamente.
- [x] **K14. Cerrar el panel.** Botón "Cerrar" o cerrar la ventana con la X. La reproducción no se interrumpe. Al volver a pulsar "Cola", se reabre.

---

## Bloque L — Shuffle inteligente, sleep timer, ecualizador (fase 2c)

**Nota (21/05/2026):** los fallos reportados de L2 (diálogo Sleep timer vacío),
MB:OFF (Settings vacío), L3 (preamp silencia) y L3.2 (crash al aplicar preset)
están arreglados en código pero **pendientes de validar en uso**. Causas:
`vlc.AudioEqualizer(idx)` segfaulteaba en VLC 3.0.23 → sustituido por
`libvlc_audio_equalizer_*` directos; preamp se inicializaba a 12 (= silencio
en la escala de VLC); el orden `grab_set` antes de los widgets bloqueaba el
render de los Toplevel.

### L1 — Shuffle inteligente

- [x] **L1.0.** Carga una cola con al menos 8-10 pistas de **2-3 artistas distintos repetidos** (p. ej. 5 de Radiohead, 4 de Metallica). Doble clic en una.
- [x] **L1.1.** Activa "Mezclar". Abre el panel de cola. Observa el orden: **no debe haber dos pistas seguidas del mismo artista** mientras sea posible (si todas las restantes son del mismo, se aceptan seguidas).
- [x] **L1.2.** Cambia de cola (doble clic en otra vista filtrada con mezcla activa). La cola nueva también se mezcla con la misma regla.
- [x] **L1.3.** Desactiva "Mezclar". La cola vuelve a su orden original.

### L2 — Sleep timer

- [x] **L2.0. Botón "Timer"** visible en la barra inferior del reproductor (junto al volumen).
- [x] **L2.1. Click en "Timer".** Se abre un diálogo "Sleep timer" con: input "Minutos" (default 30), dos radios "Detener tras X minutos" / "Detener al terminar la pista actual", botones "Iniciar" y "Cancelar".
- [x] **L2.2. Iniciar timer.** Pon 1 minuto, modo "tras X minutos", inicia reproducción y pulsa Iniciar. Cierra el diálogo. En la barra de estado debe aparecer "Sleep: 00:59" decreciendo.
- [x] **L2.3. Expiración del timer.** Al llegar a 0, la reproducción se detiene (no pausa, stop). El indicador desaparece.
- [x] **L2.4. Modo "tras pista actual".** Inicia reproducción. Abre Timer, marca "Detener al terminar la pista actual", Iniciar. Cuando la pista termine, **no** salta a la siguiente: para.
- [x] **L2.5. Cancelar timer activo.** Inicia un timer largo (10 min), reábre el diálogo y pulsa "Cancelar timer". El indicador desaparece.

### L3 — Ecualizador

- [x] **L3.0. Botón "EQ"** visible en la barra inferior junto a Timer.
- [x] **L3.1. Click en "EQ"** abre un diálogo "Ecualizador" con: dropdown de presets, 10 sliders verticales (60 Hz–16 kHz), botón "Reset" y un slider de pre-amplificación.
- [x] **L3.2. Aplicar preset "Rock"** (o cualquier otro). Los sliders se mueven a los valores del preset. La música suena distinta (sube graves/agudos según preset).
- [x] **L3.3. Mover un slider individual.** Cambio audible si la pista está sonando.
- [x] **L3.4. Reset.** Botón "Reset" devuelve todos los sliders a 0 dB y el preset a "Flat".
- [x] **L3.5. Persistencia.** Cierra la app y reábrela. El preset/sliders se mantienen.

---

## Bloque M — Crossfade entre pistas (fase 2c.4) + regresión EQ

**Nota (24/05/2026):** fase 2c.4 nueva y fix del bug "EQ silencia en primer
cambio" (causa: primer `set_equalizer` mid-stream en VLC 3.0.23+pipewire).
Bloque pendiente de validar entero. Para arranque limpio:

```bash
rm -f ~/.local/share/MusicGrabber/config.json
rm -rf ~/MusicGrabber-test
cp -r ~/Claude-Cowork/RESULTADOS/musicgrabber-linux ~/MusicGrabber-test
cd ~/MusicGrabber-test && bash install.sh
musicgrabber
```

### M1 — Regresión EQ (arranque limpio)

- [x] **M1.1.** Tras primer arranque limpio (config.json eliminado), carga una pista y dale play. Sonido normal, sin distorsión.
- [x] **M1.2.** Abre EQ y cambia a "Rock" (o cualquier preset). El sonido cambia audiblemente y NO se silencia. (Antes silenciaba en el primer cambio del primer arranque.)
- [x] **M1.3.** Cambia entre 3-4 presets seguidos sin reiniciar la app. Cada uno se aplica sin silencio ni crash.
- [x] **M1.4.** Mueve el slider de Pre-amp arriba y abajo: cambio audible inmediato.
- [x] **M1.5.** Cierra la app y reábrela. Carga una pista. El preset persistido se aplica antes del play y sigue funcionando.

### M2 — Crossfade básico

- [x] **M2.0.** Abre Ajustes (botón "MB: ..."). Aparece nueva sección "Fundido encadenado entre pistas (crossfade)" con checkbox + slider 1–12 s. El slider se deshabilita si el checkbox está OFF.
- [x] **M2.1.** Marca el checkbox, pon el slider a 4 s, Guardar. Reabre Ajustes: el estado se mantiene.
- [x] **M2.2.** Carga una cola con 3-4 pistas (doble clic en una vista filtrada). Deja sonar hasta que falten ~4 s para el final de la primera. La siguiente empieza a sonar mezclada (fade in) mientras la primera baja (fade out). En el log aparece "Crossfade: arrancando → ..." y "Crossfade: completado → ...".
- [x] **M2.3.** El swap es limpio: tras el fade, la barra inferior pasa a mostrar la nueva pista y el slider de progreso se reinicia.
- [x] **M2.4.** Sube el volumen general durante el fade (slider de volumen): la rampa NO se machaca; el volumen objetivo se aplica al terminar el fade.
- [x] **M2.5.** Cambia el slider de crossfade a 8 s en Ajustes y Guardar mientras suena. El siguiente fade dura ~8 s.

### M3 — Crossfade y transporte manual

- [x] **M3.1. Next durante un fade.** Pulsa ⏭ mientras suena un crossfade. El fade se cancela y salta directo a la siguiente pista (cut limpio, no fundido).
- [x] **M3.2. Pause durante un fade.** Cancela el fade y pausa la pista actual (sin restos del secundario sonando).
- [x] **M3.3. Seek durante un fade.** Mueve el slider de progreso mientras suena un crossfade. El fade se cancela; la pista actual salta a la posición pedida sin restos del secundario.
- [x] **M3.4. Stop durante un fade.** El botón ■ corta ambos streams.

### M4 — Crossfade vs. modos especiales

- [x] **M4.1. Repeat=track.** Activa "Repetir pista". Deja terminar una pista corta: NO hay crossfade (sería absurdo cruzar consigo misma); se repite normalmente.
- [x] **M4.2. Repeat=list.** Activa "Repetir lista". En la última pista de la cola, el crossfade arranca con la primera al llegar al final.
- [x] **M4.3. Sleep timer "tras pista actual".** Activa el modo. La pista termina sin crossfade y la reproducción se detiene.
- [x] **M4.4. Crossfade OFF.** Desmarca el checkbox en Ajustes y Guardar. Las pistas siguen avanzando como antes, sin fundido. El comportamiento original se mantiene intacto.
- [x] **M4.5. Pista más corta que el crossfade.** Si una pista dura menos de 1.5× la duración del crossfade configurado, no se hace fade (mejor un cut limpio que un fundido sin tiempo).

### M5 — Crossfade + cola

- [x] **M5.1. Quitar la siguiente pista durante un fade.** Si quitas la pista que está entrando, el fade se cancela y la reproducción salta al siguiente índice válido.
- [x] **M5.2. Reordenar la siguiente pista durante un fade.** El fade se cancela.
- [x] **M5.3. "Reproducir aquí" en panel de cola.** Salta a una pista distinta durante un fade: el fade se cancela y arranca la pista elegida con cut limpio.

### M6 — Crossfade + ecualizador (regresión combinada)

- [x] **M6.1.** Con crossfade ON y un preset distinto a Flat aplicado, el fade entre pistas suena ecualizado en AMBAS pistas durante todo el fundido. Sin ventana de silencio o desigualdad en EQ entre la pista saliente y la entrante.

---

## Bloque N — Modo fiesta v2 (fase 2c.5 reescrita)

**Nota (27/05/2026):** la versión 1 del modo fiesta (filtrar la cola
existente) se rediseñó tras feedback de uso. Ahora es **autoplay puro**:
ignora la cola, escoge pistas de la biblioteca en un rango de BPM y
autollena la cola mientras avanza, con pool fresco. Se añaden presets de
"feeling" y range slider de doble thumb.

Requiere librosa instalado (lo gestiona `install.sh` vía `requirements.txt`):

```bash
rm -rf ~/MusicGrabber-test
cp -r ~/Claude-Cowork/RESULTADOS/musicgrabber-linux ~/MusicGrabber-test
cd ~/MusicGrabber-test && bash install.sh
musicgrabber
```

Para forzar arranque limpio (recrea config y la DB con la columna nueva):

```bash
rm -f ~/.local/share/MusicGrabber/config.json
rm -f ~/.local/share/MusicGrabber/library.db
```

**Nota (11/08/2026):** Bloque N validado end-to-end (agentes automatizados,
app real corriendo en entorno aislado, con audio real generado con ffmpeg y
`librosa` instalado). N1-N4 y N6 se probaron llamando directamente a las
mismas funciones que disparan los botones/atajos reales (`pipeline.process_new_download`,
`player.set_party_mode`, `_compute_bpm_library`, etc. — el mismo código, sin
mocks de la lógica), inspeccionando estado real (DB, `state.event_log`,
archivos en disco) en vez de solo mirar la pantalla. N5 y el modal de N3 sí
se probaron a través de los widgets reales (`SettingsDialog`, `EnrichProgressDialog`).
Se encontraron y arreglaron 3 bugs reales en el proceso (no estaban en
`Reporte-de-fallos.txt`, salieron de esta ronda de pruebas):

1. **RangeSlider rompía el diálogo de Ajustes por completo.** `ui/gui_app.py:232-233`:
   la clase guardaba el ancho/alto en `self._w`/`self._h`, pisando el atributo
   interno `self._w` que tkinter usa para la ruta Tcl del widget. Cualquier
   dibujo posterior (`self.delete("all")`) fallaba con
   `TclError: invalid command name "240"`. Como el error queda atrapado por
   el `try/except` de `_build()` (el mismo que arregló A4), Ajustes abría
   pero **sin la sección Modo fiesta ni los botones Guardar/Cancelar/Cambiar
   biblioteca** — no se podía guardar NINGÚN ajuste desde la GUI. Arreglado
   renombrando a `self._w_px`/`self._h_px`.
2. **El crossfade general no se restauraba con el valor nuevo al salir de fiesta.**
   `core/player.py`: `set_party_mode(False)` y `load_queue()` restauraban un
   crossfade "capturado" al activar la fiesta (`_party_pre_crossfade`), no el
   vigente. Si cambiabas el crossfade general en Ajustes mientras la fiesta
   sonaba, al desactivarla volvía al valor VIEJO, no al nuevo guardado.
   Arreglado: ahora restaura siempre `state.crossfade_enabled/seconds` en
   vivo; se eliminó el mecanismo de captura (`_party_pre_crossfade`), quedó
   más simple.
3. **Orden de la columna BPM al revés para pistas sin BPM.** `ui/gui_app.py:2421`:
   usaba `-1.0` como valor de orden para "sin BPM calculado", que se comporta
   como "más bajo que cualquier BPM real" — las quedaba PRIMERO en ascendente
   y AL FINAL en descendente, justo al revés de lo esperado. Arreglado
   usando `float("inf")` en su lugar.

### N1 — Migración de la DB

- [x] **N1.1. DB legacy.** Si conservas una `library.db` anterior al 25/05, al arrancar verás en consola `[INFO] Migración DB: columna 'bpm' añadida`. Sin error.
- [x] **N1.2. DB nueva.** Tras borrar `library.db`, el arranque limpio crea la tabla con la columna `bpm` desde el principio. No aparece el log de migración.

### N2 — Cálculo automático al descargar

- [x] **N2.1. Descarga una pista** con MB OFF. En el log aparecen líneas "Calculando BPM: …" y "BPM: 128.0 (…)" o "BPM no detectado: …". La pista entra en biblioteca con la columna BPM rellena.
- [x] **N2.2. Descarga otra pista** con MB ON. Mismo comportamiento: además de los tags MB, se calcula y guarda el BPM.
- [x] **N2.3. Sin librosa.** Si librosa no está instalado (test manual: `pip uninstall librosa`), el log dice "BPM backend (librosa) no disponible" una sola vez al primer cálculo y las descargas siguen funcionando sin BPM (no rompe nada).

### N3 — Botón "Calcular BPM" masivo

- [x] **N3.1. Botón visible** en la barra superior junto a "Enriquecer con MusicBrainz".
- [x] **N3.2. Pulsa el botón** con la biblioteca con pistas viejas sin BPM. Confirmación con número de pistas y tiempo estimado (~3 s/pista).
- [x] **N3.3. Modal de progreso** similar al de Enriquecer: contador, barra, label "Calculando BPM (librosa)", última pista con BPM detectado o "✗ Sin BPM".
- [x] **N3.4. Cancelar.** El botón "Cancelar" interrumpe el proceso. Las pistas ya procesadas quedan con BPM guardado.
- [x] **N3.5. Nada que calcular.** Si todas las pistas tienen BPM, mensaje "Nada que calcular".

### N4 — Modo fiesta (autoplay)

- [x] **N4.0. Botón "Fiesta"** visible en la barra inferior junto a EQ (confirmado por captura de pantalla).
- [x] **N4.1. Activar fiesta sin pistas en el rango.** Sin pistas en rango, `set_party_mode` devuelve pool=0 y NO toca la cola existente (verificado que la cola previa queda intacta). El aviso "Modo fiesta sin pistas" está en el código de `_player_toggle_party` (gui_app.py:2053-2061), no se re-verificó el texto exacto en pantalla.
- [x] **N4.2. Activar fiesta sin cola previa.** Pool=6, cola se llena con semilla de 3, arranca a reproducir, `party_enabled=True` con el rango correcto.
- [x] **N4.3. Activar fiesta con cola previa.** La cola vieja se reemplaza por completo (0 pistas viejas sobreviven).
- [x] **N4.4. Autollenado.** Tras 10 ciclos de avance, siempre quedaron ≥2 pistas por delante del índice activo (lookahead mantenido).
- [x] **N4.5. Shuffle inteligente.** Secuencia de 12 pistas autollenadas sin un solo par de artistas consecutivos iguales.
- [x] **N4.6. Pool fresco.** Con pool de 2 pistas, "Modo fiesta: pool agotado, reiniciando rotación" apareció repetidamente en el log de eventos según se agotaba; la rotación siguió sin romperse.
- [x] **N4.7. Crossfade forzado.** Al activar, `crossfade_enabled=True` y `crossfade_seconds` igual al configurado para fiesta.
- [x] **N4.8. Desactivar fiesta.** Cola vaciada, `party_enabled=False`, crossfade restaurado al general.
- [x] **N4.9. Doble clic en biblioteca durante fiesta.** `load_queue()` desactiva la fiesta, deja la cola con solo esa pista, restaura el crossfade.

### N5 — Range slider y presets de fiesta

- [x] **N5.1. Range slider.** Sección "Modo fiesta": 4 presets, rango 60-200, label inicial correcta. (Encontró y motivó el fix del bug #1 de arriba — antes de arreglarlo, esta sección ni siquiera se creaba.)
- [x] **N5.2. Arrastrar thumbs.** Min no puede pasar de max y viceversa (clamping verificado simulando el evento de arrastre sobre el widget real).
- [x] **N5.3. Click en preset "Chill".** Thumbs a (70, 100), label "70 – 100".
- [x] **N5.4. Click en preset "Bailable".** Thumbs a (120, 145).
- [x] **N5.5. Guardar.** Rango guardado y persistido; reabrir Ajustes lo mantiene.
- [x] **N5.6. Crossfade en fiesta.** Slider a 10s, Guardar, persiste (`party_crossfade_s == 10` tras reabrir Ajustes). Que el fade "suene" 10s no se verificó de oído — sí se verificó en N4.7 que el valor configurado se aplica literalmente a `player._crossfade_seconds` al activar.
- [x] **N5.7. Crossfade general no se machaca.** Confirmado el comportamiento correcto Y encontrado el bug #2 de arriba: "no se machaca mientras la fiesta está activa" ya funcionaba, pero "al desactivar, queda en el nuevo valor" NO — arreglado, ahora sí.

### N6 — Columna BPM en biblioteca

- [x] **N6.1.** Vista Biblioteca: la tabla tiene una columna "BPM" entre "MB" y "Duración".
- [x] **N6.2.** Pistas con BPM calculado muestran el valor (ej. "128"). Pistas sin BPM muestran vacío.
- [x] **N6.3.** Click en el header "BPM" ordena numéricamente (ascendente con ▲, click otra vez para descendente ▼). Las pistas sin BPM quedan al final en asc, al principio en desc. Es el bug #3 de la nota de arriba — antes del fix quedaban al REVÉS.

---

## Limitaciones conocidas (no son bugs de código)

- **Contenido con restricción de edad.** Vídeos que YouTube marca como
  contenido infantil/restringido no se pueden descargar
  (`core/downloader.py:191-195` detecta y loguea el fallo, pero no hay
  soporte de cookies de navegador en las opciones de yt-dlp). Añadirlo es
  una decisión de producto pendiente (fricción/privacidad de pedir cookies
  del navegador vs. poder descargar ese contenido), no un fix de una línea.

---

## Si algo falla

Anota en tu mensaje a Claude la línea de la prueba que falló y qué viste exactamente. Si hay un error en consola, copia el traceback. Si lo que falla es visual, describe brevemente o haz captura.

---

## Resumen de archivos y rutas útiles

| Archivo                                              | Para qué                                      |
|------------------------------------------------------|-----------------------------------------------|
| `~/.local/share/MusicGrabber/config.json`            | Configuración persistente (incluye geometría) |
| `~/.local/share/MusicGrabber/library.db`             | Índice SQLite                                 |
| `~/.local/share/MusicGrabber/_inbox/`                | Buffer temporal de descargas                  |
| `{biblioteca}/_inbox_review/`                        | Pistas que MB no resolvió (cuando MB ON)      |
| `{biblioteca}/Failures_Log.txt`                      | Log persistente de fallos                     |
