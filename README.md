# compara_fichero_VCARD.py

Script en Python que compara dos ficheros **VCF (vCard 3.0)** y genera un VCF nuevo
con las entradas del segundo que **no existen** en el primero.

- `--base` → fichero de referencia (**BASE**). Sus entradas se consideran "ya existentes".
- `--add` → fichero cuyas entradas se quieren incorporar (**ADD**).
- Salida → VCF con únicamente las tarjetas de ADD que son **nuevas** respecto a BASE.
- Parámetros posicionales no; también acepta los atajos `-b` y `-a`.

## Uso rápido

```bash
# Con los ficheros de prueba del repositorio
python3 compara_fichero_VCARD.py --base icloud.vcf --add empresa.vcf -o salida.vcf

# Si se omite -o, el fichero se llama <nombre_de_add>_nuevos.vcf
# (p. ej. empresa_nuevos.vcf)
```

> **Nota: importar los contactos en un iPhone.** La forma fiable de cargar el
> VCF generado en un iPhone es **enviar un correo electrónico con el fichero
> VCF como adjunto**, abrir ese correo desde el propio iPhone y, al tocar el
> adjunto, elegir **«Crear nuevo contacto»** para importarlo.

Opciones:

| Opción | Descripción |
|---|---|
| `--base`, `-b` | (obligatorio) VCF de referencia (BASE). |
| `--add`, `-a` | (obligatorio) VCF del que extraer las entradas nuevas (ADD). |
| `-o`, `--output` | Fichero VCF de salida. Por defecto `<add>_nuevos.vcf`. |
| `--keep-technical` | Conserva propiedades técnicas (UID, PRODID, REV, fotos, extensiones `X-`…). Por defecto se eliminan para dejar la salida limpia. |
| `-v`, `--verbose` | Imprime por stderr dos secciones separadas y ordenadas por nombre — **duplicadas primero y después las nuevas** — indicando en el caso de las duplicadas **qué campos** provocaron la coincidencia (correo, teléfono o nombre). |

## Cómo decide si una entrada está duplicada

No basta con comparar el nombre: de cada VCARD se extrae una "huella de identidad"
con todos los campos relevantes y se indexa BASE por tres claves:

1. **Correos electrónicos** (`EMAIL`) — normalizados a minúsculas, sin espacios,
   sin `mailto:` ni etiquetas tipo `Nombre <a@b.c>`.
2. **Teléfonos** (`TEL`) — normalizados: sólo dígitos ASCII, conservando el `+`
   inicial si existe. Se descartan cadenas que no son un número (p. ej. los
   `"TEL:type=twitter"` o los códigos internos tipo `item1.TEL:22350` de Apple
   se tratan con cuidado).
3. **Nombre completo** — el conjunto de "tokens" de `FN`, `N` y `ORG`, sin acentos,
   en minúsculas y sin símbolos (se descartan partes entre paréntesis).

Una entrada de ADD es **duplicada** (no se exporta) si:

- comparte **algún correo** o **algún teléfono** con alguna entrada de BASE **y**
  además comparten **alguna palabra del nombre** (esto evita falsos positivos
  cuando una persona distinta hereda un número o correo antiguo), **o**
- no comparte correos/teléfonos pero su **nombre completo normalizado** coincide
  exactamente con una entrada de BASE.

En caso contrario, la entrada se considera **nueva** y se escribe en el VCF de salida.

### Detalles del formato vCard 3.0 que se manejan

- **Plegado de líneas** (RFC 2426): líneas que empiezan por espacio/tabulación
  son continuación de la anterior. La salida se re-pliega a 75 columnas.
- **Finales de línea CRLF** (RFC 2426 §2.1): la salida siempre se escribe con
  `\r\n`. Los ficheros vCard con finales LF (Unix) son **rechazados por el
  importador de Contactos de iCloud** con el error «Esta vCard no puede
  importarse porque contiene datos de contacto no válidos». Si un VCF tuyo
  falla al importarlo en iCloud, comprueba esto primero (`file fichero.vcf` o
  `grep -c $'\r' fichero.vcf`).
- **Escapes de valor**: `\;`, `\,`, `\n` (`RFC 2426 §2.4.2`).
- **Grupos indexados de Apple** (`item1.TEL:`, `item2.EMAIL:`, …): se tratan como
  propiedades normales tras quitar el prefijo `itemN.`.
- **Fidelidad byte a byte**: los valores se emiten tal cual estaban en el fichero
  original (los `;` estructurales de `N`/`ADR`/`ORG` se conservan intactos, sin
  re-escapar). La cabecera se normaliza a `VERSION:3.0`.
- Extensiones `X-` de Apple/iCloud (`X-ABShowAs`, `X-SOCIALPROFILE`,
  `X-ADDRESSBOOKLABELEDDATA`, …) se ignoran en la comparación y se eliminan de la
  salida salvo con `--keep-technical`.

## Resultado con los ficheros de prueba

Con `icloud.vcf` (2 279 contactos) como BASE y `empresa.vcf` (1 556) como ADD:

```
BASE   (icloud.vcf): 2279 contactos
ADD    (empresa.vcf): 1556 contactos
Duplicadas (ya en BASE): 1298
Nuevas escritas en "salida.vcf": 258
```

Con `-v` se ven dos secciones separadas y ordenadas por nombre (primero las
duplicadas, después las nuevas), por ejemplo:

```
==== DUPLICADAS (ya en BASE): 1298 ====================
[duplicada] Jacopo XXXX == Jacopo XXXX  (email jacopoXXXX@dXXXa.com, tel 931234567, tel 612355678)
...
==== NUEVAS: 258 ==================================
[nueva] Sara XXXX XXXX
```

## Validaciones realizadas

- **Round-trip**: concatenando `icloud.vcf` + las 258 nuevas en un fichero y
  usando ese como BASE, al reprocesar `empresa.vcf` el resultado es **0 nuevas**
  (prueba de completitud de la detección).
- **Precisión**: muestreo manual de contactos "nuevos"; sus correos y teléfonos
  no aparecen realmente en `icloud.vcf`.
- **Integridad**: la salida tiene los bloques `BEGIN:VCARD` / `VERSION:3.0` /
  `END:VCARD` balanceados y no contiene metadatos técnicos.

## Notas del dominio detectadas al analizar los ficheros

- Ambos ficheros son vCard 3.0 generados por iPhone/iCloud (`PRODID:-//Apple Inc.//iPhone OS 26.6.2//EN`).
- Hay campos `TEL` legítimos con **dos números concatenados** en el mismo valor,
  p. ej. `TEL;type=WORK;type=VOICE:931234567 /901234567`. Existen idénticos en
  ambos ficheros, de modo que comparan bien aunque su normalización los fusione
  en una única cadena de dígitos.
- Apple usa grupos indexados (`item1.TEL`, `item2.TEL`, …) que este script
  soporta de forma transparente.


## Requisitos

Python 3.x (solo librería estándar: `argparse`, `re`, `unicodedata`, `os`, `sys`).
No se necesita instalar ningún paquete externo.
