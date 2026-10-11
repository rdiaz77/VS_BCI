"""Allowed TIPO_GASTO values, per statement type.

The two vocabularies are deliberately different, and nothing may write a value
outside the matching list — the dropdown silently blanks unknown values, and
classification propagation is scoped by ORIGEN for the same reason.
"""

TIPO_GASTO_OPTIONS_NAC = [
    "Airbnb", "Alimentacion", "Alojamiento", "BCI Paga TC",
    "Canva", "Combustible", "Comida", "Comision Intl", "Comision Nacional",
    "Tr Deuda Intl", "Electronic", "Estacionamiento", "G. Comun", "Garantia",
    "Google Suite", "Hardware", "Hubspot", "Impuesto", "Kilometraje", "Legales",
    "Libro", "Marketing", "Materiales", "Movilizacion", "Otro", "Pasajes Aereos",
    "Peajes", "Personal CA", "Personal RD", "Personal RM",
    "Shutterstock", "Software", "Telefonos", "Transporte", "Viaticos",
]

TIPO_GASTO_OPTIONS_INTL = [
    "Airbnb", "Canva", "Food", "Google", "GSuite", "Hotel", "Huber", "Hubspot",
    "Marketing", "Microsoft", "Personal CA", "Personal RD", "Personal RM",
    "Shutterstock", "Software", "Taxi", "Ticket Fare",
    "Trp a Deuda Nacional", "VEED", "Yachay",
]


CATEGORIAS_NO_GASTO = {
    "BCI Paga TC", "Pago Tarjeta", "Tr Deuda Intl",
    "Trp a Deuda Nacional", "Personal RD", "Personal CA", "Personal RM",
    "Devolución",
}

KAME_ACCOUNT_MAP: dict[str, tuple[str, str]] = {
    # Alimentación (4.01.03.29)
    "Alimentacion":             ("4.01.03.29", "Gastos Alimentación"),
    "Alimentación":             ("4.01.03.29", "Gastos Alimentación"),
    "Comida":                   ("4.01.03.29", "Gastos Alimentación"),
    "Food":                     ("4.01.03.29", "Gastos Alimentación"),
    # Alojamiento (4.01.03.32)
    "Alojamiento":              ("4.01.03.32", "Gastos de Alojamiento"),
    "Hotel":                    ("4.01.03.32", "Gastos de Alojamiento"),
    "Airbnb":                   ("4.01.03.32", "Gastos de Alojamiento"),
    "Viajes":                   ("4.01.03.32", "Gastos de Alojamiento"),
    # Movilización y sus cuentas propias
    "Transporte":               ("4.01.03.28", "Gastos de Movilización"),
    "Movilizacion":             ("4.01.03.28", "Gastos de Movilización"),
    "Pasajes Aereos":           ("4.01.03.28", "Gastos de Movilización"),
    "Kilometraje":              ("4.01.03.28", "Gastos de Movilización"),
    "Viaticos":                 ("4.01.03.28", "Gastos de Movilización"),
    "Taxi":                     ("4.01.03.28", "Gastos de Movilización"),
    "Ticket Fare":              ("4.01.03.28", "Gastos de Movilización"),
    "Huber":                    ("4.01.03.28", "Gastos de Movilización"),
    "Combustible":              ("4.01.03.18", "Combustibles"),
    "Peajes":                   ("4.01.03.19", "Peajes"),
    "Vehículo":                 ("4.01.03.20", "Mantención Vehículos"),
    "Estacionamiento":          ("4.01.03.21", "Estacionamiento"),
    # Telecomunicaciones (4.01.03.13)
    "Telefonos":                ("4.01.03.13", "Gastos Telefónicos e Internet"),
    "Telecomunicaciones":       ("4.01.03.13", "Gastos Telefónicos e Internet"),
    "Servicios Básicos":        ("4.01.03.13", "Gastos Telefónicos e Internet"),
    # Suscripciones (4.01.03.12) — NO es 4.01.03.36, que en el libro diario
    # sólo tiene un comprobante y no es la cuenta de suscripciones de la empresa.
    "Software":                 ("4.01.03.12", "Suscripciones"),
    "Hubspot":                  ("4.01.03.12", "Suscripciones"),
    "Google":                   ("4.01.03.12", "Suscripciones"),
    "Google Suite":             ("4.01.03.12", "Suscripciones"),
    "GSuite":                   ("4.01.03.12", "Suscripciones"),
    "Canva":                    ("4.01.03.12", "Suscripciones"),
    "Shutterstock":             ("4.01.03.12", "Suscripciones"),
    "Microsoft":                ("4.01.03.12", "Suscripciones"),
    "VEED":                     ("4.01.03.12", "Suscripciones"),
    "Yachay":                   ("4.01.03.12", "Suscripciones"),
    "Electronic":               ("4.01.03.12", "Suscripciones"),
    # Publicidad (4.01.03.09)
    "Marketing":                ("4.01.03.09", "Publicidad y Marketing"),
    # Oficina e insumos
    "Oficina y Mantenimiento":  ("4.01.03.04", "Artículos de Oficina"),
    "Marketplace":              ("4.01.03.04", "Artículos de Oficina"),
    "Materiales":               ("4.01.03.04", "Artículos de Oficina"),
    "Equipos y Hardware":       ("4.01.03.06", "Insumos computacionales"),
    "Hardware":                 ("4.01.03.06", "Insumos computacionales"),
    # Cuentas propias que v3 colapsaba en Generales
    "Impuestos y Municipal":    ("4.01.03.15", "Patentes Comerciales"),
    "Capacitación":             ("4.01.03.24", "Capacitaciones"),
    "Seguros":                  ("4.01.03.08", "Seguros"),
    # Generales (4.01.03.22) — lo que de verdad no tiene cuenta propia
    "Insumos Laboratorio":      ("4.01.03.22", "Gastos Generales"),
    "G. Comun":                 ("4.01.03.22", "Gastos Generales"),
    "Libro":                    ("4.01.03.22", "Gastos Generales"),
    "Otro":                     ("4.01.03.22", "Gastos Generales"),
    "Otros":                    ("4.01.03.22", "Gastos Generales"),
    "Salud":                    ("4.01.03.22", "Gastos Generales"),
    "Ferias y Congresos":       ("4.01.03.22", "Gastos Generales"),
    # Aduana / Courier (4.01.03.17)
    "Courier y Envios":         ("4.01.03.17", "Gastos Aduana"),
    "Courier y Envíos":         ("4.01.03.17", "Gastos Aduana"),
    # Profesionales y legales
    "Servicios Profesionales":  ("4.01.03.31", "Gastos Consultoría Admin"),
    "Legales":                  ("4.01.03.03", "Gastos Legales"),
    # Bancarios (4.02.07.01)
    "Comision Intl":            ("4.02.07.10", "Comision Compra Intl Tarjeta de Credito"),
    "Comision Nacional":        ("4.02.07.01", "Gastos Bancarios"),
    "Impuesto":                 ("4.02.07.01", "Gastos Bancarios"),
}


# Accounts the ledger itself revealed, by showing where Kame already posted
# these same charges.
CUENTA_PRESTAMO_RD = ("2.02.04.04", "Préstamos Particulares - Rdiaz")
CUENTA_FONDOS_POR_RENDIR = ("1.01.01.10", "Fondos por Rendir")
CUENTA_IMPOSICIONES = ("2.01.12.01", "Imposiciones por Pagar")
# Claudio has no receivable of his own, so his personal charges stay on the
# national card and the glosa carries his initials for later collection.
CUENTA_POR_COBRAR_CA = ("2.01.01.03", "Tarjeta de Credito Banco (BCI) — glosa: CA")

# The bank's category sometimes lumps merchants that belong to different Kame
# accounts, and only the merchant name separates them. Checked before the
# category map, in order, against the uppercased description.
KAME_OVERRIDES: dict[str, list[tuple[str, tuple[str, str]]]] = {
    "Servicios Profesionales": [
        ("NOTARIA",    ("4.01.03.02", "Gastos Notariales")),
        ("AUTOMOTORA", ("4.01.03.20", "Mantención Vehículos")),
        ("PAYPAL",     ("4.01.03.01", "Honorarios Profesionales")),
    ],
    "Salud": [
        ("ISAPRE", CUENTA_IMPOSICIONES),
    ],
    # From 2025 the chart separates the three bank charges the card generates.
    "Comision Nacional": [
        ("INTERESES",  ("4.02.07.05", "Intereses Tarjeta de Credito")),
        ("COBRO ADM",  ("4.02.07.09", "Cobro Admin Tarjeta de Credito")),
    ],
}

# What kind of entry a category produces. Anything absent is a direct expense.
# A boleta de garantía pays an invoice from MASAVAL already booked against the
# supplier, so posting it as expense would count that cost twice. A cash advance
# is money handed to an employee against a later expense report. And a personal
# charge the bank put on the company card is a receivable from the cardholder,
# settled during the year — not nothing at all.
TIPO_ASIENTO: dict[str, str] = {
    "Garantia":           "Paga factura proveedor",
    "Avance en Efectivo": "No es gasto — fondo por rendir",
    "Personal RD":        "No es gasto — cuenta por cobrar titular",
    "Personal CA":        "No es gasto — cuenta por cobrar titular",
    "Personal RM":        "No es gasto — cuenta por cobrar titular",
}

CUENTA_NO_GASTO: dict[str, tuple[str, str]] = {
    "Garantia":           ("2.01.07.01", "Proveedores Nacionales"),
    "Avance en Efectivo": CUENTA_FONDOS_POR_RENDIR,
    "Personal RD":        CUENTA_PRESTAMO_RD,
    "Personal CA":        CUENTA_POR_COBRAR_CA,
    "Personal RM":        CUENTA_PRESTAMO_RD,
}


def cuenta_kame(tipo_gasto: str, descripcion: str = "") -> tuple[str, str]:
    """Kame account for a transaction: ('4.01.03.12', 'Suscripciones')."""
    if tipo_gasto in CUENTA_NO_GASTO:
        return CUENTA_NO_GASTO[tipo_gasto]
    desc = (descripcion or "").upper()
    for patron, cuenta in KAME_OVERRIDES.get(tipo_gasto, ()):
        if patron in desc:
            return cuenta
    return KAME_ACCOUNT_MAP.get(tipo_gasto, ("", ""))


def tipo_asiento(tipo_gasto: str) -> str:
    return TIPO_ASIENTO.get(tipo_gasto, "Gasto directo")


def opciones(origen: str) -> list[str]:
    return (
        TIPO_GASTO_OPTIONS_INTL
        if origen == "INTERNACIONAL"
        else TIPO_GASTO_OPTIONS_NAC
    )
