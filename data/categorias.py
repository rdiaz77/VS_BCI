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
    # Movilización (4.01.03.28)
    "Transporte":               ("4.01.03.28", "Gastos de Movilización"),
    "Combustible":              ("4.01.03.28", "Gastos de Movilización"),
    "Movilizacion":             ("4.01.03.28", "Gastos de Movilización"),
    "Pasajes Aereos":           ("4.01.03.28", "Gastos de Movilización"),
    "Estacionamiento":          ("4.01.03.28", "Gastos de Movilización"),
    "Peajes":                   ("4.01.03.28", "Gastos de Movilización"),
    "Kilometraje":              ("4.01.03.28", "Gastos de Movilización"),
    "Viaticos":                 ("4.01.03.28", "Gastos de Movilización"),
    "Taxi":                     ("4.01.03.28", "Gastos de Movilización"),
    "Ticket Fare":              ("4.01.03.28", "Gastos de Movilización"),
    "Huber":                    ("4.01.03.28", "Gastos de Movilización"),
    "Vehículo":                 ("4.01.03.28", "Gastos de Movilización"),
    # Telecomunicaciones (4.01.03.13)
    "Telefonos":                ("4.01.03.13", "Gastos Telefónicos e Internet"),
    "Telecomunicaciones":       ("4.01.03.13", "Gastos Telefónicos e Internet"),
    "Servicios Básicos":        ("4.01.03.13", "Gastos Telefónicos e Internet"),
    # Comercio Electrónico (4.01.03.36)
    "Software":                 ("4.01.03.36", "Gastos Comercio Electrónico"),
    "Marketing":                ("4.01.03.36", "Gastos Comercio Electrónico"),
    "Hubspot":                  ("4.01.03.36", "Gastos Comercio Electrónico"),
    "Google":                   ("4.01.03.36", "Gastos Comercio Electrónico"),
    "Google Suite":             ("4.01.03.36", "Gastos Comercio Electrónico"),
    "GSuite":                   ("4.01.03.36", "Gastos Comercio Electrónico"),
    "Canva":                    ("4.01.03.36", "Gastos Comercio Electrónico"),
    "Shutterstock":             ("4.01.03.36", "Gastos Comercio Electrónico"),
    "Microsoft":                ("4.01.03.36", "Gastos Comercio Electrónico"),
    "VEED":                     ("4.01.03.36", "Gastos Comercio Electrónico"),
    "Yachay":                   ("4.01.03.36", "Gastos Comercio Electrónico"),
    "Electronic":               ("4.01.03.36", "Gastos Comercio Electrónico"),
    "Marketplace":              ("4.01.03.36", "Gastos Comercio Electrónico"),
    # Generales (4.01.03.22)
    "Hardware":                 ("4.01.03.22", "Gastos Generales"),
    "Equipos y Hardware":       ("4.01.03.22", "Gastos Generales"),
    "Materiales":               ("4.01.03.22", "Gastos Generales"),
    "Insumos Laboratorio":      ("4.01.03.22", "Gastos Generales"),
    "Oficina y Mantenimiento":  ("4.01.03.22", "Gastos Generales"),
    "G. Comun":                 ("4.01.03.22", "Gastos Generales"),
    "Libro":                    ("4.01.03.22", "Gastos Generales"),
    "Otro":                     ("4.01.03.22", "Gastos Generales"),
    "Otros":                    ("4.01.03.22", "Gastos Generales"),
    "Seguros":                  ("4.01.03.22", "Gastos Generales"),
    "Salud":                    ("4.01.03.22", "Gastos Generales"),
    "Impuestos y Municipal":    ("4.01.03.22", "Gastos Generales"),
    "Capacitación":             ("4.01.03.22", "Gastos Generales"),
    "Ferias y Congresos":       ("4.01.03.22", "Gastos Generales"),
    "Avance en Efectivo":       ("4.01.03.22", "Gastos Generales"),
    # Aduana / Courier (4.01.03.17)
    "Courier y Envios":         ("4.01.03.17", "Gastos Aduana"),
    "Courier y Envíos":         ("4.01.03.17", "Gastos Aduana"),
    # Consultoría (4.01.03.31)
    "Servicios Profesionales":  ("4.01.03.31", "Gastos Consultoría Admin"),
    # Notariales (4.01.03.02)
    "Legales":                  ("4.01.03.02", "Gastos Notariales"),
    # Bancarios (4.02.07.01)
    "Comision Intl":            ("4.02.07.01", "Gastos Bancarios"),
    "Comision Nacional":        ("4.02.07.01", "Gastos Bancarios"),
    "Impuesto":                 ("4.02.07.01", "Gastos Bancarios"),
    # Garantía (4.02.07.06)
    "Garantia":                 ("4.02.07.06", "Gastos Boleta de Garantía"),
}


def opciones(origen: str) -> list[str]:
    return (
        TIPO_GASTO_OPTIONS_INTL
        if origen == "INTERNACIONAL"
        else TIPO_GASTO_OPTIONS_NAC
    )
