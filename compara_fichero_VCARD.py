#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compara_fichero_VCARD.py
========================

Lee dos ficheros VCF (vCard 3.0) y genera un nuevo VCF con las entradas que
están en ADD y NO existen en BASE.

Uso:
    python3 compara_fichero_VCARD.py --base icloud.vcf --add acciona.vcf \
        [-o salida.vcf] [--keep-technical] [-v]

Una entrada de ADD se considera duplicada (ya presente en BASE) si comparte
con alguna entrada de BASE:
  * algún correo electrónico (normalizado: minúsculas, sin etiquetas),
  * algún teléfono (normalizado: sólo se conservan los dígitos y el '+' inicial),
  * o, en ausencia de datos de contacto comunes, el nombre completo (FN/N)
    exactamente igual tras normalizarlo.

Cuando coinciden teléfono/correo pero los nombres no guardan ninguna palabra
en común, la entrada se conserva (evita falsos duplicados por números o
correos antiguos reutilizados por personas distintas).

La salida conserva las líneas originales de cada VCARD: se despliega el
plegado de líneas (folding, RFC 2426) y se re-pliesga a 75 columnas, y la
cabecera se normaliza a VERSION:3.0. Por defecto se eliminan las propiedades
puramente técnicas (UID, PRODID, REV, fotos, extensiones X- de Apple/iCloud);
usa --keep-technical para conservarlas tal cual.

Importante: la salida se escribe SIEMPRE con finales de línea CRLF
("\\r\\n"), como exige el RFC 2426 §2.1 en los ficheros vCard. Los ficheros
con finales LF (Unix) son rechazados por el importador de Contactos de
iCloud con el error «Esta vCard no puede importarse porque contiene datos de
contacto no válidos».
"""

import argparse
import os
import re
import sys
import unicodedata
from typing import Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Constantes / expresiones regulares
# ---------------------------------------------------------------------------

# Grupos indexados de vCard 3.0 en ficheros de Apple: "item1.TEL:", "item2.EMAIL:"...
GROUP_PREFIX_RE = re.compile(r'^item\d+\.', re.IGNORECASE)

# Propiedades puramente técnicas (metadata): no definen identidad y no se
# copian a la salida salvo con --keep-technical
TECHNICAL_NAMES = {
    'PRODID', 'REV', 'UID', 'LASTMODIFIED', 'PHOTO', 'LOGO', 'SOUND',
    'KEY', 'AGENT',
}

# Carácter centinela para proteger el backslash escapado ('\\\\') durante la
# decodificación de los valores
_ESC_BACKSLASH = '\x00'


# ---------------------------------------------------------------------------
# Parsing vCard 3.0 (RFC 2426)
# ---------------------------------------------------------------------------

def unfold_text(raw: str) -> List[str]:
    """
    Despliega las líneas físicas de un VCF en líneas lógicas y elimina
    líneas vacías, según el folding de RFC 2426 (continuación con ' ' o Tab).
    """
    raw = raw.replace('\r\n', '\n').replace('\r', '\n')
    out: List[str] = []
    for line in raw.split('\n'):
        if not line:
            continue
        if line[0] in ' \t' and out:
            out[-1] += line[1:]  # continuación de la línea anterior
        else:
            out.append(line)
    return out


def decode_value(value: str) -> str:
    """Decodifica los escapes de los valores vCard 3.0: \\\\, \\n, \\;, \\,"""
    value = value.replace('\\\\', _ESC_BACKSLASH)
    value = re.sub(r'\\([nN])', '\n', value)
    value = value.replace('\\;', ';').replace('\\,', ',')
    return value.replace(_ESC_BACKSLASH, '\\')


def encode_value(value: str) -> str:
    """Re-escapa un valor decodificado para serializarlo en vCard 3.0."""
    value = value.replace('\\', '\\\\')
    value = value.replace(';', '\\;').replace(',', '\\,')
    return value.replace('\n', '\\n')


def _split_unescaped(raw: str) -> List[str]:
    """Divide por ';' que NO estén escapados con backslash."""
    parts: List[str] = []
    buf: List[str] = []
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == '\\' and i + 1 < len(raw):
            buf.append(raw[i:i + 2])
            i += 2
        elif ch == ';':
            parts.append(''.join(buf))
            buf = []
            i += 1
        else:
            buf.append(ch)
            i += 1
    parts.append(''.join(buf))
    return parts


def parse_property(line: str) -> Optional[Dict[str, object]]:
    """
    Analiza una línea lógica de propiedad del tipo:
        [grupo.]NOMBRE[;param[=valor]...][:VALOR]
    Devuelve None si la línea no es reconocible como propiedad.
    """
    if ':' not in line:
        return None
    head, raw_value = line.split(':', 1)
    pieces = head.split(';')
    full_name = pieces[0].strip()
    group = ''
    name = full_name
    m = GROUP_PREFIX_RE.match(full_name)
    if m:
        group = full_name[:m.end()]
        name = full_name[m.end():]
    name = name.strip().upper()
    if not name:
        return None
    params_raw = [p for p in pieces[1:]]
    return {
        'group': group,
        'name': name,
        'params': params_raw,
        'value_raw': raw_value,
        'value': decode_value(raw_value.strip()),
    }


# ---------------------------------------------------------------------------
# Normalización y huella de identidad
# ---------------------------------------------------------------------------

def _strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize('NFKD', text)
    return ''.join(ch for ch in nfkd if not unicodedata.combining(ch))


def normalize_phone(raw: str) -> str:
    """
    Normaliza un teléfono a su mínimo comparable: sólo dígitos ASCII,
    conservando el '+' inicial si va delante de cualquier dígito.
    Descarta cadenas que no sean esencialmente un número (p. ej. "type=twitter").
    """
    txt = _strip_accents(raw).lower()
    # Dígitos Unicode frecuentes -> ASCII
    txt = txt.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789'))
    digits = ''.join(ch for ch in txt if '0' <= ch <= '9')
    if not digits:
        return ''
    plus_idx = txt.find('+')
    first_digit = next((i for i, ch in enumerate(txt) if '0' <= ch <= '9'), -1)
    if plus_idx != -1 and (first_digit == -1 or plus_idx < first_digit):
        return '+' + digits
    return digits


def normalize_email(raw: str) -> str:
    """Normaliza un correo: minúsculas, sin espacios ni etiquetas tipo "<...>"."""
    email = _strip_accents(raw).strip().lower()
    m = re.search(r'<([^<>]+)>', email)  # "Nombre <a@b.c>" -> "a@b.c"
    if m:
        email = m.group(1)
    if email.startswith('mailto:'):
        email = email[len('mailto:'):]
    return re.sub(r'\s+', '', email)


def _name_tokens(text: str) -> Set[str]:
    """
    Tokens normalizados de un nombre: sin acentos, minúsculas, sin símbolos y
    sin partes entre paréntesis (p. ej. "(Casa)", "(Trabajo)").
    """
    txt = _strip_accents(text).lower()
    txt = re.sub(r'\(.*?\)', ' ', txt)
    txt = re.sub(r'[^0-9a-z]+', ' ', txt)
    return {t for t in txt.split() if t}


def split_n_value(n_raw: str) -> List[str]:
    """Divide el valor de N en sus componentes vCard (separados por ';')."""
    return [decode_value(p.strip()) for p in _split_unescaped(n_raw)]


class Card:
    """Representa un VCARD ya desplegado."""

    __slots__ = ('props', 'fn', 'name_tokens', 'emails', 'phones', 'name_key')

    def __init__(self) -> None:
        self.props: List[Dict[str, object]] = []
        self.fn: str = ''
        self.name_tokens: Set[str] = set()
        self.emails: Set[str] = set()
        self.phones: Set[str] = set()
        self.name_key: frozenset = frozenset()

    def collect_identity(self, prop: Dict[str, object]) -> None:
        name = prop['name']  # type: ignore[union-attr]
        value = prop['value']  # type: ignore[union-attr]
        raw_value = prop['value_raw']  # type: ignore[union-attr]
        if name == 'FN':
            if not self.fn:
                self.fn = str(value)
            self.name_tokens |= _name_tokens(str(value))
        elif name == 'N':
            for component in split_n_value(str(raw_value)):
                if component:
                    self.name_tokens |= _name_tokens(component)
        elif name == 'ORG':
            for component in _split_unescaped(str(raw_value).strip()):
                if component:
                    self.name_tokens |= _name_tokens(component)
        elif name == 'EMAIL':
            norm = normalize_email(str(value))
            if norm and '@' in norm:
                self.emails.add(norm)
        elif name == 'TEL':
            norm = normalize_phone(str(value))
            if norm:
                self.phones.add(norm)

    def finalize(self) -> None:
        self.name_key = frozenset(self.name_tokens)


def parse_vcf(path: str, label: str) -> List[Card]:
    """Lee un fichero VCF y devuelve la lista de VCARDs que contiene."""
    try:
        with open(path, 'r', encoding='utf-8-sig', errors='replace') as fh:
            raw = fh.read()
    except OSError as exc:
        print(f"ERROR al abrir {label} ({path}): {exc}", file=sys.stderr)
        raise SystemExit(2)

    cards: List[Card] = []
    current: Optional[Card] = None
    for line in unfold_text(raw):
        stripped = line.strip().upper()
        if stripped == 'BEGIN:VCARD':
            current = Card()
            continue
        if stripped == 'END:VCARD':
            if current is not None:
                current.finalize()
                cards.append(current)
            current = None
            continue
        if current is None:
            continue  # líneas fuera de un VCARD se ignoran
        prop = parse_property(line)
        if prop is None:
            continue
        current.props.append(prop)
        current.collect_identity(prop)
    return cards


# ---------------------------------------------------------------------------
# Índice de BASE y detección de duplicados
# ---------------------------------------------------------------------------

class BaseIndex:
    """Índice de los contactos de BASE por correo, teléfono y nombre."""

    def __init__(self, cards: List[Card]) -> None:
        self.email_map: Dict[str, List[Card]] = {}
        self.phone_map: Dict[str, List[Card]] = {}
        self.name_map: Dict[frozenset, List[Card]] = {}
        for card in cards:
            for email in card.emails:
                self.email_map.setdefault(email, []).append(card)
            for phone in card.phones:
                self.phone_map.setdefault(phone, []).append(card)
            self.name_map.setdefault(card.name_key, []).append(card)

    def find_matches(self, card: Card) -> List[Tuple[Card, Set[str]]]:
        """
        Devuelve los contactos de BASE con los que `card` sería duplicado,
        junto con las coincidencias que los justifican.
        """
        found: Dict[int, Tuple[Card, Set[str]]] = {}

        for email in card.emails:
            for base_card in self.email_map.get(email, []):
                entry = found.setdefault(id(base_card), (base_card, set()))
                entry[1].add(f'email {email}')

        for phone in card.phones:
            for base_card in self.phone_map.get(phone, []):
                entry = found.setdefault(id(base_card), (base_card, set()))
                entry[1].add(f'tel {phone}')

        # Guarda: si coincide el teléfono/correo pero los nombres no comparten
        # ninguna palabra, podría tratarse de otra persona -> no duplicado.
        matches: List[Tuple[Card, Set[str]]] = []
        for base_card, reasons in found.values():
            if (card.name_tokens and base_card.name_tokens
                    and not (card.name_tokens & base_card.name_tokens)):
                continue
            matches.append((base_card, reasons))

        # Sin datos de contacto comunes, sólo duplicado si el nombre completo
        # coincide exactamente (FN + N + ORG normalizados).
        if not found and card.name_key in self.name_map:
            for base_card in self.name_map[card.name_key]:
                matches.append((base_card, {'nombre'}))

        return matches


# ---------------------------------------------------------------------------
# Serialización de la salida
# ---------------------------------------------------------------------------

def _serialize_prop(prop: Dict[str, object]) -> str:
    """
    Serializa una propiedad conservando el valor original (value_raw) para
    mantener la fidelidad exacta de los campos estructurados (N, ADR, ORG...)
    cuyos componentes van separados por ';' sin escapar.
    """
    head = str(prop['group']) + str(prop['name'])
    for param in prop['params']:  # type: ignore[union-attr]
        if param:
            head += ';' + str(param)
    return _fold_line(head + ':' + str(prop['value_raw']))


def _fold_line(line: str, limit: int = 75) -> str:
    """Re-pliega una línea a `limit` caracteres (continuación con espacio)."""
    if len(line) <= limit:
        return line
    out: List[str] = []
    pos = 0
    first = True
    while pos < len(line):
        chunk_limit = limit if first else limit - 1
        chunk = line[pos:pos + chunk_limit]
        nxt = pos + len(chunk)
        # Evita que la siguiente línea empiece por ' ' (se perdería al desplegar)
        if nxt < len(line) and line[nxt] == ' ':
            chunk = line[pos:pos + len(chunk) + 1]
            nxt += 1
        out.append(chunk if first else ' ' + chunk)
        first = False
        pos = nxt
    # RFC 2426 §2.1: el fichero debe usar CRLF como separador de líneas.
    return '\r\n'.join(out)


def _is_technical(prop: Dict[str, object]) -> bool:
    name = str(prop['name'])
    return name in TECHNICAL_NAMES or name.startswith('X-')


def serialize_card(card: Card, keep_technical: bool = False) -> str:
    """Serializa un VCARD a texto vCard 3.0 (sin plegar los saltos de valor)."""
    lines: List[str] = ['BEGIN:VCARD', 'VERSION:3.0']
    for prop in card.props:
        if str(prop['name']) == 'VERSION':
            continue  # la cabecera ya se emite normalizada arriba
        if not keep_technical and _is_technical(prop):
            continue
        lines.append(_serialize_prop(prop))
    lines.append('END:VCARD')
    # RFC 2426 §2.1: el fichero debe usar CRLF como separador de líneas.
    return '\r\n'.join(lines)


# ---------------------------------------------------------------------------
# Programa principal
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=('Genera un VCF con las entradas del fichero --add que no '
                     'existen en el fichero --base (vCards 3.0).'),
    )
    parser.add_argument('--base', '-b', required=True, metavar='FICHERO',
                        help='VCF de referencia (BASE)'
                        )
    parser.add_argument('--add', '-a', required=True, metavar='FICHERO',
                        help='VCF cuyas entradas nuevas se quieren exportar (ADD)')
    parser.add_argument('-o', '--output', metavar='FICHERO', default=None,
                        help='fichero VCF de salida (por defecto: <add>_nuevos.vcf)')
    parser.add_argument('--keep-technical', action='store_true',
                        help='conserva propiedades técnicas (UID, PRODID, REV, '
                             'fotos, extensiones X-...) en la salida')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='muestra el detalle de duplicadas y nuevas')
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    base_cards = parse_vcf(args.base, 'BASE')
    add_cards = parse_vcf(args.add, 'ADD')
    base_index = BaseIndex(base_cards)

    new_cards: List[Card] = []
    dup_entries: List[Tuple[str, str, List[str]]] = []  # (fn_add, fn_base, motivos)
    for card in add_cards:
        matches = base_index.find_matches(card)
        if matches:
            reasons = sorted({r for _, rs in matches for r in rs})
            base_fn = next((c.fn for c, _ in matches if c.fn), '(sin FN)')
            if args.verbose:
                dup_entries.append((card.fn or '(sin FN)', base_fn, reasons))
        else:
            new_cards.append(card)

    if args.verbose:
        key = lambda fn: _strip_accents(fn).casefold()  # noqa: E731
        print(f'==== DUPLICADAS (ya en BASE): {len(dup_entries)} ' + '=' * 20,
              file=sys.stderr)
        for fn_add, fn_base, reasons in sorted(dup_entries, key=lambda e: key(e[0])):
            print(f'[duplicada] {fn_add} == {fn_base}  ({", ".join(reasons)})',
                  file=sys.stderr)
        print(f'==== NUEVAS: {len(new_cards)} ' + '=' * 34, file=sys.stderr)
        for card in sorted(new_cards, key=lambda c: key(c.fn or '(sin FN)')):
            print(f'[nueva] {card.fn or "(sin FN)"}', file=sys.stderr)

    root, ext = os.path.splitext(args.add)
    output_path = args.output or (root + '_nuevos' + (ext if ext else '.vcf'))
    # Sin línea en blanco entre tarjetas (los ficheros exportados de iPhone
    # tampoco la llevan); se conservan las propiedades tal cual.
    content = '\r\n'.join(
        serialize_card(card, keep_technical=args.keep_technical)
        for card in new_cards
    )
    if content:
        content += '\r\n'
    # Los ficheros vCard (RFC 2426 §2.1) deben usar finales de línea CRLF.
    # iCloud rechaza los VCF que sólo usan LF con el error «Esta vCard no
    # puede importarse porque contiene datos de contacto no válidos».
    with open(output_path, 'w', encoding='utf-8', newline='') as fh:
        fh.write(content)

    print(f'BASE   ({args.base}): {len(base_cards)} contactos')
    print(f'ADD    ({args.add}): {len(add_cards)} contactos')
    print(f'Duplicadas (ya en BASE): {len(dup_entries)}')
    print(f'Nuevas escritas en "{output_path}": {len(new_cards)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

