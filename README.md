# Thermal Capture - PureThermal / FLIR Lepton

Software Python para Ubuntu/Linux orientado a adquisición experimental con
PureThermal / PureThermal 3. El flujo activo abre una cámara UVC térmica,
captura frames Y16/GRAY16_LE, muestra siempre una vista previa normalizada y,
en modo `record`, guarda los datos crudos en un gzip binario versionado.

## Hardware objetivo

- Dispositivo USB: PureThermal / PureThermal 3.
- Driver Linux: `uvcvideo`.
- Dispositivo por defecto: `/dev/video0`.
- Stream térmico esperado: `Y16` / `GRAY16_LE`.
- Resolución esperada: `160x120`.
- FPS esperado: `9`.

Si el dispositivo entrega `160x122`, el programa recorta a `160x120` y emite
una advertencia una sola vez. Las dos filas extra podrían ser telemetría
embebida, pero esta versión no las interpreta.

## Instalación

Dependencias de sistema recomendadas en Ubuntu:

```bash
sudo apt update
sudo apt install python3 python3-venv v4l-utils
```

`v4l-utils` no es una dependencia Python; se usa para diagnóstico con
`v4l2-ctl`.

Entorno virtual Python:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

El backend por defecto es `v4l2`, porque es el camino más directo para UVC en
Ubuntu y permite solicitar `Y16` con `CAP_PROP_CONVERT_RGB=0`. Para usar
`--backend gst`, OpenCV debe estar compilado con soporte GStreamer:

```bash
python - <<'PY'
import cv2
print(cv2.getBuildInformation())
PY
```

## Uso

Diagnóstico rápido del entorno:

```bash
python main.py diagnose
```

Vista previa sin grabar:

```bash
python main.py preview
python main.py --backend v4l2 preview
```

Grabar raw Y16 gzip con vista previa activa:

```bash
python main.py record --output ./datos
python main.py record --output ./datos --duration 60
python main.py --backend v4l2 record --output ./datos --duration 10
```

Guardar además un CSV derivado en Celsius durante la captura:

```bash
python main.py record --output ./datos --save-csv
python main.py --backend v4l2 record --output ./datos --duration 10 --save-csv
```

`--save-csv` existe por conveniencia, pero no se recomienda para grabaciones
largas: el CSV es mucho más grande que el raw comprimido y puede agregar carga
de escritura durante la adquisición. El flujo recomendado es grabar solo raw y
exportar el CSV después.

Exportar CSV Celsius desde un raw ya grabado:

```bash
python main.py export-csv ./datos/thermal_20260512_143000.raw.y16.gz
python main.py export-csv ./datos/thermal_20260512_143000.raw.y16.gz --output ./datos/salida.celsius.csv
python main.py export-csv ./datos/*.raw.y16.gz
python main.py export-csv ./datos/*.raw.y16.gz --output ./csv_exportados
```

Con un solo archivo, `--output` puede ser el nombre del CSV final. Con varios
archivos, `--output` se interpreta como carpeta de destino; si no existe, se
crea.

Reproducir visualmente una captura raw:

```bash
python main.py play ./datos/thermal_20260512_143000.raw.y16.gz
python main.py play ./datos/thermal_20260512_143000.raw.y16.gz --speed 2.0
python main.py play ./datos/thermal_20260512_143000.raw.y16.gz --pause-ms 0
```

`play` lee el raw gzip v1 frame por frame, normaliza cada frame solo para
pantalla y superpone índice de frame, timestamp y min/media/max en Celsius. La
captura científica sigue siendo el archivo `.raw.y16.gz`; la imagen mostrada es
una visualización derivada.

Inspeccionar el header de un archivo raw:

```bash
python main.py inspect ./datos/thermal_20260512_143000.raw.y16.gz
```

Durante `record`, la imagen se muestra siempre, también cuando se usa
`--duration`. La captura termina con `q` en la ventana, `Ctrl+C` en terminal o
al cumplirse la duración indicada.

## Archivos de salida

Archivo principal, siempre generado por `record`:

```text
thermal_YYYYmmdd_HHMMSS.raw.y16.gz
```

Archivo derivado, solo con `--save-csv`:

```text
thermal_YYYYmmdd_HHMMSS.celsius.csv
```

El mismo CSV puede generarse offline con `export-csv`, que es preferible para
capturas largas.

El raw gzip es el archivo principal porque conserva las mediciones Y16 sin
pérdida y permite reprocesar los datos con otra calibración o fórmula. El CSV
en Celsius es una salida derivada conveniente para inspección y análisis
tabular, pero depende de asumir que el stream Y16 está en modo radiométrico /
T-linear.

Flujo recomendado:

```bash
python main.py record --output ./datos --duration 60
python main.py export-csv ./datos/thermal_YYYYmmdd_HHMMSS.raw.y16.gz
```

## Formato raw Y16 gzip v1

El archivo usa gzip en modo binario.

Header:

```text
magic       bytes fijos: b"PTY16V1\n"
header_len  uint32 little-endian
header_json JSON UTF-8 de longitud header_len
```

Contenido de `header_json`:

```json
{
  "format": "PureThermal Y16 Raw Gzip",
  "version": 1,
  "width": 160,
  "height": 120,
  "fps": 9,
  "dtype": "uint16",
  "endianness": "little",
  "raw_unit": "kelvin_x100_if_radiometric_tlinear",
  "celsius_formula": "raw / 100.0 - 273.15",
  "timestamp_s": "unix_seconds_float64",
  "timestamp_ms": "unix_milliseconds_int64",
  "frame_record": {
    "frame_index": "uint64",
    "timestamp_s": "float64",
    "timestamp_ms": "int64",
    "raw_y16": "uint16[height,width]"
  }
}
```

Cada frame se guarda como:

```text
frame_index uint64 little-endian
timestamp_s float64 little-endian
timestamp_ms int64 little-endian
raw_y16      uint16 little-endian[height,width]
```

## CSV Celsius opcional

Columnas:

```text
frame_index,timestamp_s,timestamp_ms,width,height,unit,frame_celsius
```

- `frame_index`: índice entero desde cero.
- `timestamp_s`: valor de `time.time()` en segundos.
- `timestamp_ms`: `int(round(timestamp_s * 1000))`.
- `width`: ancho de la matriz.
- `height`: alto de la matriz.
- `unit`: siempre `celsius`.
- `frame_celsius`: matriz 2D `height x width` serializada como JSON compacto.

Conversión:

```python
celsius = raw_y16 / 100.0 - 273.15
```

Esta fórmula solo es válida si el stream Y16 está en modo radiométrico /
T-linear, es decir, si los valores crudos representan Kelvin multiplicado por
100. Valida esto experimentalmente contra una referencia física antes de usar
el CSV como dato científico.

## Datos crudos, Celsius y display

El programa mantiene separados los datos científicos y la imagen de pantalla:

- `raw_y16`: matriz `uint16` original capturada desde Y16/GRAY16_LE.
- `Celsius`: salida derivada calculada desde `raw_y16`.
- Imagen normalizada: matriz 8-bit generada solo para `cv2.imshow`.

La imagen normalizada no se guarda como dato térmico. Si OpenCV entrega un
frame BGR/RGB de tres canales, el programa aborta con un error claro porque eso
no es Y16 crudo.

## Configuración

`config.yaml` define los valores por defecto:

```yaml
thermal_device: /dev/video0
backend: v4l2
width: 160
height: 120
fps: 9
output_dir: .
crop_telemetry_rows: true
radiometric_tlinear: true
```

`docs/TECHNICAL_AUDIT.md` se conserva como registro histórico de los scripts
auditados. No describe el formato activo final del proyecto.

## License

This project is licensed under the Creative Commons Attribution 4.0
International License (CC BY 4.0).

Creative Commons licenses are generally not the standard choice for software
projects, but this repository is intentionally distributed under CC BY 4.0 for
attribution-based reuse.

If you use this project, please cite or attribute:

Esteban Hurtado León and Nicolás Ulloa Gatica.
Laboratorio de Lenguaje, Interacción y Fenomenología,
Pontificia Universidad Católica de Chile.
purethermal-y16-recorder: Python CLI for recording PureThermal/FLIR Lepton Y16
thermal data on Ubuntu, 2026.
Licensed under CC BY 4.0.
