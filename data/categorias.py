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
    "Marketing", "Microsoft", "Shutterstock", "Software", "Taxi", "Ticket Fare",
    "Trp a Deuda Nacional", "VEED", "Yachay",
]


def opciones(origen: str) -> list[str]:
    return (
        TIPO_GASTO_OPTIONS_INTL
        if origen == "INTERNACIONAL"
        else TIPO_GASTO_OPTIONS_NAC
    )
