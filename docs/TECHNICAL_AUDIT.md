# Auditoría técnica — PureThermal / FLIR Lepton

## 1. Hardware identificado

### Módulo USB

El sistema corresponde a un módulo PureThermal / PureThermal 3 con firmware reportado como `fw:v1.3.0`. PureThermal 3 es un módulo Smart I/O para núcleos FLIR Lepton, expuesto por USB como cámara térmica UVC.

### Núcleo térmico probable

La resolución observada es `160x120` a aproximadamente `9 fps`. Esa resolución es consistente con la familia FLIR Lepton 3.x. No permite distinguir con certeza entre Lepton 3.0, 3.5, 3.1R u otra variante compatible; para eso se debe leer información OEM/part number mediante SDK, control UVC, I2C o herramientas del fabricante.

### Dispositivos Linux

- `/dev/video0`: dispositivo principal de imagen.
- `/dev/video1`: probablemente endpoint o nodo de metadata UVC; no debe tratarse automáticamente como segunda cámara de imagen.

### Formatos de pixel observados

Formatos detectados: `UYVY`, `Y16`, `GREY`, `RGBP`, `BGR3`.

El formato relevante para datos térmicos crudos es `Y16`, que en GStreamer aparece como `GRAY16_LE`: un canal por píxel, 16 bits little-endian.

### 160x120 vs 160x122

- `160x120`: matriz térmica activa esperada.
- `160x122`: probablemente `160x120` + 2 filas adicionales de telemetría o metadata embebida.

Los scripts auditados no decodifican esas dos filas. Por lo tanto, el software nuevo las recorta opcionalmente pero no interpreta su contenido.

## 2. Clasificación de archivos antiguos

| Archivo | Clasificación | Uso probable | Observaciones |
|---|---|---|---|
| `webcamtest(2).py` / `#webcamtest(2).py#` | Script de prueba/exploración | Verificar GStreamer + PureThermal Y16 | Abre un pipeline `v4l2src ... GRAY16_LE ! appsink`, lee frames y aplica una conversión térmica directa. No graba. |
| `thermal_server(1).py` | Servicio web / núcleo útil parcial | Flujo web antiguo con cámara visible + térmica | Usa Flask, OpenCV, YAML, threading y gzip. Parece haber manejado preview web, grabación de video visible y grabación térmica. Su formato térmico es distinto e incompleto respecto a `thcap.py`: solo escribe tamaño y frames, sin framerate ni timestamp. |
| `thcap(2).py` | Núcleo útil del flujo, pero con errores | Captura térmica + visible por hilos | Define `ThermalFileFrameWriter` con framerate, tamaño, timestamp por frame y matriz raw. Parece el formato más completo. Tiene bugs en el bloque `__main__`. |
| `thcap(2).py-bu` | Backup/obsoleto | Versión anterior de `thcap.py` | Versión con escritor térmico más pobre; no conserva timestamp por frame. |
| `play_thermal(2).py` | Utilidad de reproducción | Leer capturas térmicas gzip del formato `thcap.py` | Confirma layout: framerate float64, tamaño uint16, timestamp float64 por frame, frame uint16. Tiene ruta absoluta hardcodeada. |
| `dualcap(1).py` | Script útil para múltiples térmicas | Captura dos cámaras térmicas configuradas en YAML | Usa `config['thermal_devices']`, GStreamer y `ThermalFileFrameWriter` de `thcap.py`. No usa cámara visible ni audio. |
| `config(2).yaml` | Configuración | Define dos dispositivos térmicos por by-id y carpeta `video` | No contiene `video_device` ni `thermal_device`, por lo que no es compatible directamente con `thermal_server.py` ni con el bloque principal de `thcap.py`. |
| `thermal_server(2).service` | Servicio/automatización | Arranque systemd del servidor Flask | Ejecuta `/opt/thermal_server/thermal_server.py` como usuario `lif-2`. Depende de rutas absolutas del sistema antiguo. |

## 3. Formato térmico existente

Se detectan dos formatos incompatibles.

### Formato A — `thermal_server.py`

```text
uint16 width
uint16 height
repeat:
    uint16[height, width] raw_frame
```

Problemas:

- No guarda framerate.
- No guarda timestamp por frame.
- El parámetro del constructor se llama `framewrite`, pero se le pasa `thermalFramerate`; no se usa.
- El nombre de archivo contiene `80x60`, aunque el hardware actual reporta `160x120`.

### Formato B — `thcap.py` / `play_thermal.py`

```text
float64 framerate
uint16 width
uint16 height
repeat:
    float64 timestamp_unix_seconds
    uint16[height, width] raw_frame
```

Este es el formato más completo y el que se conserva en la refactorización.

El timestamp viene de `time.time()`, por lo que está en segundos Unix como número flotante. Conversión a milisegundos:

```python
timestamp_ms = int(round(timestamp_s * 1000.0))
```

## 4. Errores e inconsistencias relevantes

- `thcap.py` define `FrameRecorder(capture_string, capture_type, imgcallback=None)`, pero en `__main__` lo llama como `FrameRecorder(config['thermal_device'], raw2image)`. Ahí `raw2image` queda pasado como `capture_type`, no como callback. Eso es inconsistente y probablemente rompe la captura.
- `thcap.py` clasifica cámaras térmicas buscando `80x60`; esto está desactualizado para el hardware actual `160x120`.
- `gststr()` genera un string con prefijo `gst-launch-1.0`, que no corresponde al pipeline que normalmente recibe `cv2.VideoCapture(..., cv2.CAP_GSTREAMER)`.
- `thermal_server.py` usa `config['video_device']` y `config['thermal_device']`, pero el YAML adjunto define `thermal_devices` y `data_folder`, no esas claves.
- `thermal_server.py` contiene un typo: `self.vieoWriter = None`.
- `thermal_server.py` corta el loop solo si `not video_ok and thermal_ok`; probablemente debería cortar si falla cualquiera de las dos cámaras.
- `play_thermal.py` usa una ruta absoluta `/home/ubuntu/thermal_server/video/`, no portable.
- No hay evidencia suficiente de audio en los archivos adjuntos.

## 5. Flujo de adquisición reconstruido

El flujo original parece haber tenido dos ramas:

1. **Servidor Flask (`thermal_server.py`)**
   - Abre cámara visible desde `config['video_device']`.
   - Abre cámara térmica desde `config['thermal_device']`.
   - Une visualmente ambas imágenes para streaming MJPEG.
   - Graba visible como MP4.
   - Graba térmica como gzip raw, pero sin timestamp.

2. **Captura por scripts (`thcap.py` / `dualcap.py`)**
   - Abre una o más cámaras térmicas por GStreamer.
   - Muestra preview normalizado.
   - Graba gzip con framerate, tamaño, timestamp y frame raw.

El código nuevo prioriza la segunda rama porque conserva timestamp por frame y separa mejor raw vs visualización.

## 6. Limitaciones conocidas

- No se identifica el part number exacto del núcleo Lepton.
- No se decodifica telemetría embebida.
- La conversión a Celsius se conserva como opción, pero solo es válida si el stream Y16 está en formato radiométrico T-linear.
- No se implementa audio por falta de evidencia.
- No se implementa sincronización estricta entre cámara visible y térmica; queda como extensión posterior.

## 7. Pendientes experimentales

- Confirmar con `v4l2-ctl --list-formats-ext -d /dev/video0` si el modo final de captura es `Y16 160x120` o `Y16 160x122`.
- Validar si `160x122` contiene telemetría útil y documentar su layout real.
- Confirmar que OpenCV recibe `np.uint16` de un canal al usar GStreamer.
- Comparar temperatura estimada `raw / 100 - 273.15` con una referencia física para verificar radiometría.
- Leer OEM/part number del Lepton con herramienta compatible.
