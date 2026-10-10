# Plan: Ingreso de Gastos TC 2022 a Kame ERP

## Contexto

La base de datos tiene **260 transacciones de tarjetas de crédito BCI del año 2022**, todas clasificadas por tipo de gasto (`tipo_gasto`), ninguna ingresada aún a Kame (`FACT_KAME=0`). Se deben generar asientos contables mensuales agrupados por cuenta Kame.

### Titulares activos en 2022
- **Rafael** (••4364, ••7325): 158 nacionales + 35 internacionales = 193 tx
- **Maria** (••6988): 67 nacionales

### Tratamiento de transacciones internacionales

Las transacciones internacionales están en USD. El banco traspasa la deuda internacional acumulada a la cartola nacional como "TRASPASO DEUDA INTERNACIONAL" en CLP. Ese monto CLP ya existe como línea en la cartola nacional y representa el valor real pagado.

**Regla**: Las transacciones internacionales NO se contabilizan individualmente. El gasto en CLP se registra a través de la línea "Tr Deuda Intl" que aparece en la cartola nacional. El detalle de transacciones internacionales queda como respaldo documental.

### Categorías excluidas (no son gasto)
| Categoría | Motivo |
|---|---|
| BCI Paga TC, Pago Tarjeta | Pago de pasivo — rebaja la deuda TC, no es gasto |
| Tr Deuda Intl | Traspaso interno: la deuda USD pasa a CLP. Se contabiliza como gasto agregado en la cuenta que corresponda |
| Trp a Deuda Nacional | Contrapartida del traspaso en la cartola internacional |
| Personal RD, Personal CA | Gasto personal, no de empresa |

## Mapeo Categoría → Cuenta Kame

| Categoría BD | Cuenta Kame | Código |
|---|---|---|
| Alimentación | Gastos Alimentación | 4.01.03.29 |
| Viajes | Gastos de Alojamiento | 4.01.03.32 |
| Transporte | Gastos de movilización | 4.01.03.28 |
| Combustible | Gastos de movilización | 4.01.03.28 |
| Telecomunicaciones | Gastos telefónicos e Internet | 4.01.03.13 |
| Software | Gastos Comercio Electrónico | 4.01.03.36 |
| Marketing | Gastos Comercio Electrónico | 4.01.03.36 |
| Hubspot | Gastos Comercio Electrónico | 4.01.03.36 |
| Google | Gastos Comercio Electrónico | 4.01.03.36 |
| GSuite | Gastos Comercio Electrónico | 4.01.03.36 |
| Canva | Gastos Comercio Electrónico | 4.01.03.36 |
| Shutterstock | Gastos Comercio Electrónico | 4.01.03.36 |
| Equipos y Hardware | Gastos Generales | 4.01.03.22 |
| Oficina y Mantenimiento | Gastos Generales | 4.01.03.22 |
| Courier y Envíos | Gastos Aduana | 4.01.03.17 |
| Servicios Profesionales | Gastos Consultoría Admin | 4.01.03.31 |
| Capacitación | Gastos Generales | 4.01.03.22 |
| Seguros | Gastos Generales | 4.01.03.22 |
| Salud | Gastos Generales | 4.01.03.22 |
| Impuestos y Municipal | Gastos Generales | 4.01.03.22 |
| Legales | Gastos Notariales | 4.01.03.02 |
| Marketplace | Gastos Generales | 4.01.03.22 |
| Comisión Intl | Gastos Bancarios | 4.02.07.01 |
| Comisión Nacional | Gastos Bancarios | 4.02.07.01 |
| Impuesto | Gastos Bancarios | 4.02.07.01 |
| Garantía | Gastos Boleta de Garantía | 4.02.07.06 |
| Otros | Gastos Generales | 4.01.03.22 |

## Conexión a la base de datos

```python
import tomllib
from data.database import init_db
secrets = tomllib.load(open('.streamlit/secrets.toml', 'rb'))
url = secrets.get('supabase_db_url') or secrets.get('SUPABASE_DB_URL')
conn = init_db(url)
```

## Consulta base para cada mes

```sql
SELECT tipo_gasto, titular_nombre, t.origen, count(*) as n_tx,
       round(sum(CASE WHEN t.origen='NACIONAL' THEN coalesce(monto_total,0)
                      ELSE coalesce(monto_operacion,0) END)::numeric, 0) as monto
FROM transacciones t
WHERE fecha_operacion_d >= '{año}-{mes:02d}-01'
  AND fecha_operacion_d < '{año}-{mes_sig:02d}-01'
  AND tipo_gasto NOT IN ('BCI Paga TC','Pago Tarjeta','Tr Deuda Intl',
                          'Trp a Deuda Nacional','Personal RD','Personal CA')
GROUP BY tipo_gasto, titular_nombre, t.origen
ORDER BY t.origen, tipo_gasto, titular_nombre
```

## Marcar como ingresadas (después de cada mes)

```sql
UPDATE transacciones SET fact_kame = 1
WHERE fecha_operacion_d >= '{año}-{mes:02d}-01'
  AND fecha_operacion_d < '{año}-{mes_sig:02d}-01'
  AND tipo_gasto NOT IN ('BCI Paga TC','Pago Tarjeta','Tr Deuda Intl',
                          'Trp a Deuda Nacional','Personal RD','Personal CA')
```

---

## Detalle Mes a Mes

### Enero 2022

| Categoría | Titular | Origen | N | Monto | Cuenta Kame | Código |
|---|---|---|---:|---:|---|---|
| Alimentación | Maria | NAC | 1 | CLP 16.720 | Gastos Alimentación | 4.01.03.29 |
| Capacitación | Rafael | NAC | 1 | CLP 94.500 | Gastos Generales | 4.01.03.22 |
| Comisión Intl | Rafael | NAC | 1 | CLP 1.299 | Gastos Bancarios | 4.02.07.01 |
| Comisión Nacional | Maria | NAC | 1 | CLP 3.113 | Gastos Bancarios | 4.02.07.01 |
| Comisión Nacional | Rafael | NAC | 1 | CLP 3.117 | Gastos Bancarios | 4.02.07.01 |
| Oficina y Mant. | Maria | NAC | 2 | CLP 31.779 | Gastos Generales | 4.01.03.22 |
| Salud | Maria | NAC | 1 | CLP 246.083 | Gastos Generales | 4.01.03.22 |
| Software | Rafael | NAC | 1 | CLP 92.358 | Gastos Com. Electrónico | 4.01.03.36 |
| Telecomunicaciones | Rafael | NAC | 2 | CLP 148.933 | Gastos Telefónicos | 4.01.03.13 |
| Transporte | Maria | NAC | 3 | CLP 21.428 | Gastos Movilización | 4.01.03.28 |
| Transporte | Rafael | NAC | 1 | CLP 25.000 | Gastos Movilización | 4.01.03.28 |
| Viajes | Maria | NAC | 2 | CLP 22.220 | Gastos Alojamiento | 4.01.03.32 |

**Asiento Enero:**

| Código | Cuenta | Debe |
|---|---|---:|
| 4.01.03.13 | Gastos Telefónicos e Internet | 148.933 |
| 4.01.03.22 | Gastos Generales | 372.362 |
| 4.01.03.28 | Gastos de Movilización | 46.428 |
| 4.01.03.29 | Gastos Alimentación | 16.720 |
| 4.01.03.32 | Gastos de Alojamiento | 22.220 |
| 4.01.03.36 | Gastos Comercio Electrónico | 92.358 |
| 4.02.07.01 | Gastos Bancarios | 7.529 |
| **TOTAL DEBE** | | **706.550** |

---

### Febrero 2022

| Categoría | Titular | Origen | N | Monto | Cuenta Kame | Código |
|---|---|---|---:|---:|---|---|
| Alimentación | Maria | NAC | 2 | CLP 192.850 | Gastos Alimentación | 4.01.03.29 |
| Combustible | Maria | NAC | 1 | CLP 40.000 | Gastos Movilización | 4.01.03.28 |
| Comisión Intl | Rafael | NAC | 1 | CLP 1.397 | Gastos Bancarios | 4.02.07.01 |
| Comisión Nacional | Maria | NAC | 1 | CLP 3.141 | Gastos Bancarios | 4.02.07.01 |
| Comisión Nacional | Rafael | NAC | 2 | CLP 4.591 | Gastos Bancarios | 4.02.07.01 |
| Equipos y Hardware | Rafael | NAC | 1 | CLP 71.570 | Gastos Generales | 4.01.03.22 |
| Impuesto | Rafael | NAC | 1 | CLP 33 | Gastos Bancarios | 4.02.07.01 |
| Marketplace | Rafael | NAC | 1 | CLP 11.220 | Gastos Generales | 4.01.03.22 |
| Software | Rafael | NAC | 1 | CLP 93.072 | Gastos Com. Electrónico | 4.01.03.36 |
| Transporte | Maria | NAC | 3 | CLP 25.635 | Gastos Movilización | 4.01.03.28 |

**Asiento Febrero:**

| Código | Cuenta | Debe |
|---|---|---:|
| 4.01.03.22 | Gastos Generales | 82.790 |
| 4.01.03.28 | Gastos de Movilización | 65.635 |
| 4.01.03.29 | Gastos Alimentación | 192.850 |
| 4.01.03.36 | Gastos Comercio Electrónico | 93.072 |
| 4.02.07.01 | Gastos Bancarios | 9.162 |
| **TOTAL DEBE** | | **443.509** |

---

### Marzo 2022

| Categoría | Titular | Origen | N | Monto | Cuenta Kame | Código |
|---|---|---|---:|---:|---|---|
| Alimentación | Maria | NAC | 3 | CLP 217.260 | Gastos Alimentación | 4.01.03.29 |
| Alimentación | Rafael | NAC | 1 | CLP 41.950 | Gastos Alimentación | 4.01.03.29 |
| Comisión Intl | Rafael | NAC | 2 | CLP 1.855 | Gastos Bancarios | 4.02.07.01 |
| Comisión Nacional | Maria | NAC | 1 | CLP 3.170 | Gastos Bancarios | 4.02.07.01 |
| Comisión Nacional | Rafael | NAC | 2 | CLP 4.916 | Gastos Bancarios | 4.02.07.01 |
| Garantía | Rafael | NAC | 2 | CLP 114.205 | Gastos Boleta Garantía | 4.02.07.06 |
| Impuesto | Rafael | NAC | 1 | CLP 37 | Gastos Bancarios | 4.02.07.01 |
| Marketplace | Rafael | NAC | 3 | CLP 33.191 | Gastos Generales | 4.01.03.22 |
| Salud | Rafael | NAC | 1 | CLP 214.795 | Gastos Generales | 4.01.03.22 |
| Software | Rafael | NAC | 1 | CLP 131.891 | Gastos Com. Electrónico | 4.01.03.36 |
| Telecomunicaciones | Rafael | NAC | 4 | CLP 183.940 | Gastos Telefónicos | 4.01.03.13 |
| Transporte | Maria | NAC | 2 | CLP 21.720 | Gastos Movilización | 4.01.03.28 |
| Transporte | Rafael | NAC | 1 | CLP 26.000 | Gastos Movilización | 4.01.03.28 |

**Asiento Marzo:**

| Código | Cuenta | Debe |
|---|---|---:|
| 4.01.03.13 | Gastos Telefónicos e Internet | 183.940 |
| 4.01.03.22 | Gastos Generales | 247.986 |
| 4.01.03.28 | Gastos de Movilización | 47.720 |
| 4.01.03.29 | Gastos Alimentación | 259.210 |
| 4.01.03.36 | Gastos Comercio Electrónico | 131.891 |
| 4.02.07.01 | Gastos Bancarios | 9.978 |
| 4.02.07.06 | Gastos Boleta de Garantía | 114.205 |
| **TOTAL DEBE** | | **994.930** |

---

### Abril 2022

| Categoría | Titular | Origen | N | Monto | Cuenta Kame | Código |
|---|---|---|---:|---:|---|---|
| Comisión Intl | Rafael | NAC | 3 | CLP 2.187 | Gastos Bancarios | 4.02.07.01 |
| Comisión Nacional | Maria | NAC | 1 | CLP 3.202 | Gastos Bancarios | 4.02.07.01 |
| Comisión Nacional | Rafael | NAC | 2 | CLP 5.850 | Gastos Bancarios | 4.02.07.01 |
| Impuesto | Rafael | NAC | 1 | CLP 49 | Gastos Bancarios | 4.02.07.01 |
| Marketplace | Rafael | NAC | 2 | CLP 203.226 | Gastos Generales | 4.01.03.22 |
| Salud | Maria | NAC | 1 | CLP 298.112 | Gastos Generales | 4.01.03.22 |
| Software | Rafael | NAC | 1 | CLP 94.649 | Gastos Com. Electrónico | 4.01.03.36 |
| Transporte | Maria | NAC | 1 | CLP 5.000 | Gastos Movilización | 4.01.03.28 |

**Asiento Abril:**

| Código | Cuenta | Debe |
|---|---|---:|
| 4.01.03.22 | Gastos Generales | 501.338 |
| 4.01.03.28 | Gastos de Movilización | 5.000 |
| 4.01.03.36 | Gastos Comercio Electrónico | 94.649 |
| 4.02.07.01 | Gastos Bancarios | 11.288 |
| **TOTAL DEBE** | | **612.275** |

---

### Mayo 2022

| Categoría | Titular | Origen | N | Monto | Cuenta Kame | Código |
|---|---|---|---:|---:|---|---|
| Google | Rafael | **INTL** | 1 | USD 96 | *(respaldo)* | |
| Marketing | Rafael | **INTL** | 1 | USD 17 | *(respaldo)* | |
| Alimentación | Maria | NAC | 3 | CLP 29.850 | Gastos Alimentación | 4.01.03.29 |
| Alimentación | Rafael | NAC | 2 | CLP 36.280 | Gastos Alimentación | 4.01.03.29 |
| Combustible | Maria | NAC | 2 | CLP 78.000 | Gastos Movilización | 4.01.03.28 |
| Comisión Intl | Rafael | NAC | 2 | CLP 2.433 | Gastos Bancarios | 4.02.07.01 |
| Comisión Nacional | Maria | NAC | 1 | CLP 3.252 | Gastos Bancarios | 4.02.07.01 |
| Comisión Nacional | Rafael | NAC | 2 | CLP 6.265 | Gastos Bancarios | 4.02.07.01 |
| Impuesto | Rafael | NAC | 1 | CLP 63 | Gastos Bancarios | 4.02.07.01 |
| Oficina y Mant. | Maria | NAC | 1 | CLP 14.970 | Gastos Generales | 4.01.03.22 |
| Software | Rafael | NAC | 1 | CLP 96.267 | Gastos Com. Electrónico | 4.01.03.36 |
| Transporte | Maria | NAC | 1 | CLP 8.000 | Gastos Movilización | 4.01.03.28 |

> **Traspaso INTL→NAC en mayo**: CLP 95.909 — contabilizar como Gastos Com. Electrónico (4.01.03.36) ya que las tx internacionales del período son Google y Marketing digital.

**Asiento Mayo:**

| Código | Cuenta | Debe |
|---|---|---:|
| 4.01.03.22 | Gastos Generales | 14.970 |
| 4.01.03.28 | Gastos de Movilización | 86.000 |
| 4.01.03.29 | Gastos Alimentación | 66.130 |
| 4.01.03.36 | Gastos Comercio Electrónico | 192.176 |
| 4.02.07.01 | Gastos Bancarios | 12.013 |
| **TOTAL DEBE** | | **371.289** |

---

### Junio 2022

| Categoría | Titular | Origen | N | Monto | Cuenta Kame | Código |
|---|---|---|---:|---:|---|---|
| Google | Rafael | **INTL** | 2 | USD 112 | *(respaldo)* | |
| Alimentación | Maria | NAC | 2 | CLP 161.399 | Gastos Alimentación | 4.01.03.29 |
| Comisión Intl | Rafael | NAC | 2 | CLP 2.346 | Gastos Bancarios | 4.02.07.01 |
| Comisión Nacional | Maria | NAC | 1 | CLP 3.296 | Gastos Bancarios | 4.02.07.01 |
| Comisión Nacional | Rafael | NAC | 2 | CLP 6.468 | Gastos Bancarios | 4.02.07.01 |
| Garantía | Rafael | NAC | 1 | CLP 112.135 | Gastos Boleta Garantía | 4.02.07.06 |
| Impuesto | Rafael | NAC | 1 | CLP 65 | Gastos Bancarios | 4.02.07.01 |
| Oficina y Mant. | Maria | NAC | 1 | CLP 34.000 | Gastos Generales | 4.01.03.22 |
| Seguros | Rafael | NAC | 1 | CLP 23.541 | Gastos Generales | 4.01.03.22 |
| Software | Rafael | NAC | 1 | CLP 97.654 | Gastos Com. Electrónico | 4.01.03.36 |
| Transporte | Maria | NAC | 1 | CLP 7.757 | Gastos Movilización | 4.01.03.28 |

> **Traspaso INTL→NAC en junio**: CLP 98.671 — contabilizar como Gastos Com. Electrónico (4.01.03.36).

**Asiento Junio:**

| Código | Cuenta | Debe |
|---|---|---:|
| 4.01.03.22 | Gastos Generales | 57.541 |
| 4.01.03.28 | Gastos de Movilización | 7.757 |
| 4.01.03.29 | Gastos Alimentación | 161.399 |
| 4.01.03.36 | Gastos Comercio Electrónico | 196.325 |
| 4.02.07.01 | Gastos Bancarios | 12.175 |
| 4.02.07.06 | Gastos Boleta de Garantía | 112.135 |
| **TOTAL DEBE** | | **547.332** |

---

### Julio 2022

| Categoría | Titular | Origen | N | Monto | Cuenta Kame | Código |
|---|---|---|---:|---:|---|---|
| Alimentación | Rafael | **INTL** | 7 | USD 1.187 | *(respaldo)* | |
| Marketing | Rafael | **INTL** | 1 | USD 23 | *(respaldo)* | |
| Otros | Rafael | **INTL** | 1 | USD 14 | *(respaldo)* | |
| Software | Rafael | **INTL** | 1 | USD 96 | *(respaldo)* | |
| Transporte | Rafael | **INTL** | 2 | USD 100 | *(respaldo)* | |
| Viajes | Rafael | **INTL** | 2 | USD 378 | *(respaldo)* | |
| Alimentación | Maria | NAC | 1 | CLP 9.700 | Gastos Alimentación | 4.01.03.29 |
| Combustible | Maria | NAC | 1 | CLP 53.023 | Gastos Movilización | 4.01.03.28 |
| Comisión Intl | Rafael | NAC | 13 | CLP 38.231 | Gastos Bancarios | 4.02.07.01 |
| Comisión Nacional | Maria | NAC | 1 | CLP 3.333 | Gastos Bancarios | 4.02.07.01 |
| Comisión Nacional | Rafael | NAC | 2 | CLP 7.725 | Gastos Bancarios | 4.02.07.01 |
| Impuesto | Rafael | NAC | 1 | CLP 76 | Gastos Bancarios | 4.02.07.01 |
| Marketplace | Rafael | NAC | 21 | CLP 539.689 | Gastos Generales | 4.01.03.22 |
| Oficina y Mant. | Maria | NAC | 1 | CLP 41.250 | Gastos Generales | 4.01.03.22 |
| Telecomunicaciones | Rafael | NAC | 2 | CLP 53.349 | Gastos Telefónicos | 4.01.03.13 |
| Transporte | Maria | NAC | 1 | CLP 9.000 | Gastos Movilización | 4.01.03.28 |

> **Traspaso INTL→NAC en julio**: CLP 115.535 — mes con mezcla de categorías (alimentación de viaje, transporte, software, viajes). Sugerencia: contabilizar como Gastos Generales (4.01.03.22) o desglosar proporcionalmente.

**Asiento Julio:**

| Código | Cuenta | Debe |
|---|---|---:|
| 4.01.03.13 | Gastos Telefónicos e Internet | 53.349 |
| 4.01.03.22 | Gastos Generales | 696.474 |
| 4.01.03.28 | Gastos de Movilización | 62.023 |
| 4.01.03.29 | Gastos Alimentación | 9.700 |
| 4.02.07.01 | Gastos Bancarios | 49.365 |
| **TOTAL DEBE** | | **870.911** |

---

### Agosto 2022

| Categoría | Titular | Origen | N | Monto | Cuenta Kame | Código |
|---|---|---|---:|---:|---|---|
| Google | Rafael | **INTL** | 1 | USD 20 | *(respaldo)* | |
| Software | Rafael | **INTL** | 1 | USD 101 | *(respaldo)* | |
| Comisión Intl | Rafael | NAC | 3 | CLP 6.563 | Gastos Bancarios | 4.02.07.01 |
| Comisión Nacional | Maria | NAC | 1 | CLP 3.372 | Gastos Bancarios | 4.02.07.01 |
| Comisión Nacional | Rafael | NAC | 2 | CLP 40.571 | Gastos Bancarios | 4.02.07.01 |
| Impuesto | Rafael | NAC | 1 | CLP 889 | Gastos Bancarios | 4.02.07.01 |
| Otros | Rafael | NAC | 1 | CLP -277.866 | Gastos Generales | 4.01.03.22 |
| Software | Rafael | NAC | 2 | CLP 198.914 | Gastos Com. Electrónico | 4.01.03.36 |
| Transporte | Maria | NAC | 1 | CLP 8.826 | Gastos Movilización | 4.01.03.28 |

> **Traspaso INTL→NAC en agosto**: CLP 1.347.090 — contabilizar como Gastos Com. Electrónico (4.01.03.36).
> **Nota**: el "Otros" negativo (CLP -277.866) es un MONTO CANCELADO — verificar si corresponde a un abono o devolución.

**Asiento Agosto:**

| Código | Cuenta | Debe |
|---|---|---:|
| 4.01.03.28 | Gastos de Movilización | 8.826 |
| 4.01.03.36 | Gastos Comercio Electrónico | 1.546.004 |
| 4.02.07.01 | Gastos Bancarios | 51.395 |
| **TOTAL DEBE** | | **1.606.225** |

---

### Septiembre 2022

| Categoría | Titular | Origen | N | Monto | Cuenta Kame | Código |
|---|---|---|---:|---:|---|---|
| Google | Rafael | **INTL** | 1 | USD 84 | *(respaldo)* | |
| Marketing | Rafael | **INTL** | 1 | USD 17 | *(respaldo)* | |
| Software | Rafael | **INTL** | 1 | USD 115 | *(respaldo)* | |
| Comisión Intl | Rafael | NAC | 3 | CLP 5.130 | Gastos Bancarios | 4.02.07.01 |
| Comisión Nacional | Rafael | NAC | 1 | CLP 3.423 | Gastos Bancarios | 4.02.07.01 |
| Impuestos y Municipal | Rafael | NAC | 1 | CLP 640.884 | Gastos Generales | 4.01.03.22 |
| Otros | Rafael | NAC | 1 | CLP -1.385.285 | Gastos Generales | 4.01.03.22 |
| Seguros | Rafael | NAC | 1 | CLP 93.650 | Gastos Generales | 4.01.03.22 |
| Software | Rafael | NAC | 1 | CLP 100.709 | Gastos Com. Electrónico | 4.01.03.36 |
| Viajes | Rafael | NAC | 2 | CLP 7.705 | Gastos Alojamiento | 4.01.03.32 |

> **Traspaso INTL→NAC en septiembre**: CLP 383.385 — contabilizar como Gastos Com. Electrónico (4.01.03.36).
> **Nota**: "Otros" negativo (CLP -1.385.285) es MONTO CANCELADO — pago parcial de la TC, no es gasto. Podría excluirse del asiento.

**Asiento Septiembre (excluyendo MONTO CANCELADO):**

| Código | Cuenta | Debe |
|---|---|---:|
| 4.01.03.22 | Gastos Generales | 734.534 |
| 4.01.03.32 | Gastos de Alojamiento | 7.705 |
| 4.01.03.36 | Gastos Comercio Electrónico | 484.094 |
| 4.02.07.01 | Gastos Bancarios | 8.553 |
| **TOTAL DEBE** | | **1.234.886** |

---

### Octubre 2022

| Categoría | Titular | Origen | N | Monto | Cuenta Kame | Código |
|---|---|---|---:|---:|---|---|
| Capacitación | Rafael | **INTL** | 1 | USD 105 | *(respaldo)* | |
| Software | Rafael | **INTL** | 1 | USD 70 | *(respaldo)* | |
| Alimentación | Rafael | NAC | 1 | CLP 77.900 | Gastos Alimentación | 4.01.03.29 |
| Comisión Intl | Rafael | NAC | 2 | CLP 4.338 | Gastos Bancarios | 4.02.07.01 |
| Comisión Nacional | Rafael | NAC | 1 | CLP 3.455 | Gastos Bancarios | 4.02.07.01 |
| Legales | Rafael | NAC | 1 | CLP 21.980 | Gastos Notariales | 4.01.03.02 |
| Salud | Rafael | NAC | 1 | CLP 61.176 | Gastos Generales | 4.01.03.22 |
| Seguros | Rafael | NAC | 1 | CLP 94.712 | Gastos Generales | 4.01.03.22 |
| Telecomunicaciones | Rafael | NAC | 1 | CLP 39.025 | Gastos Telefónicos | 4.01.03.13 |

> **Traspaso INTL→NAC en octubre**: CLP 206.826 — contabilizar como Gastos Generales (4.01.03.22) — mezcla de capacitación y software.

**Asiento Octubre:**

| Código | Cuenta | Debe |
|---|---|---:|
| 4.01.03.02 | Gastos Notariales | 21.980 |
| 4.01.03.13 | Gastos Telefónicos e Internet | 39.025 |
| 4.01.03.22 | Gastos Generales | 362.714 |
| 4.01.03.29 | Gastos Alimentación | 77.900 |
| 4.02.07.01 | Gastos Bancarios | 7.793 |
| **TOTAL DEBE** | | **509.412** |

---

### Noviembre 2022

| Categoría | Titular | Origen | N | Monto | Cuenta Kame | Código |
|---|---|---|---:|---:|---|---|
| Google | Rafael | **INTL** | 1 | USD 80 | *(respaldo)* | |
| Otros | Rafael | **INTL** | 1 | USD 72 | *(respaldo)* | |
| Alimentación | Maria | NAC | 2 | CLP 19.940 | Gastos Alimentación | 4.01.03.29 |
| Capacitación | Rafael | NAC | 1 | CLP 87.600 | Gastos Generales | 4.01.03.22 |
| Combustible | Rafael | NAC | 1 | CLP 84.005 | Gastos Movilización | 4.01.03.28 |
| Comisión Intl | Rafael | NAC | 2 | CLP 3.587 | Gastos Bancarios | 4.02.07.01 |
| Comisión Nacional | Maria | NAC | 1 | CLP 3.477 | Gastos Bancarios | 4.02.07.01 |
| Comisión Nacional | Rafael | NAC | 2 | CLP 4.950 | Gastos Bancarios | 4.02.07.01 |
| Impuesto | Rafael | NAC | 1 | CLP 105 | Gastos Bancarios | 4.02.07.01 |
| Oficina y Mant. | Maria | NAC | 1 | CLP 17.970 | Gastos Generales | 4.01.03.22 |
| Oficina y Mant. | Rafael | NAC | 1 | CLP 197.131 | Gastos Generales | 4.01.03.22 |
| Otros | Rafael | NAC | 1 | CLP -491.087 | *(MONTO CANCELADO — excluir)* | |
| Salud | Rafael | NAC | 1 | CLP 61.780 | Gastos Generales | 4.01.03.22 |
| Seguros | Rafael | NAC | 1 | CLP 95.494 | Gastos Generales | 4.01.03.22 |
| Telecomunicaciones | Rafael | NAC | 1 | CLP 18.923 | Gastos Telefónicos | 4.01.03.13 |
| Transporte | Rafael | NAC | 1 | CLP 1.200 | Gastos Movilización | 4.01.03.28 |

> **Traspaso INTL→NAC en noviembre**: CLP 159.048 — contabilizar como Gastos Com. Electrónico (4.01.03.36).

**Asiento Noviembre (excluyendo MONTO CANCELADO):**

| Código | Cuenta | Debe |
|---|---|---:|
| 4.01.03.13 | Gastos Telefónicos e Internet | 18.923 |
| 4.01.03.22 | Gastos Generales | 459.975 |
| 4.01.03.28 | Gastos de Movilización | 85.205 |
| 4.01.03.29 | Gastos Alimentación | 19.940 |
| 4.01.03.36 | Gastos Comercio Electrónico | 159.048 |
| 4.02.07.01 | Gastos Bancarios | 12.119 |
| **TOTAL DEBE** | | **755.210** |

---

### Diciembre 2022

| Categoría | Titular | Origen | N | Monto | Cuenta Kame | Código |
|---|---|---|---:|---:|---|---|
| Serv. Profesionales | Rafael | **INTL** | 1 | USD 370 | *(respaldo)* | |
| Alimentación | Maria | NAC | 3 | CLP 68.650 | Gastos Alimentación | 4.01.03.29 |
| Combustible | Maria | NAC | 2 | CLP 70.000 | Gastos Movilización | 4.01.03.28 |
| Comisión Nacional | Maria | NAC | 1 | CLP 3.501 | Gastos Bancarios | 4.02.07.01 |
| Marketplace | Rafael | NAC | 1 | CLP 68.990 | Gastos Generales | 4.01.03.22 |
| Oficina y Mant. | Maria | NAC | 1 | CLP 9.990 | Gastos Generales | 4.01.03.22 |
| Otros | Rafael | NAC | 1 | CLP -910.468 | *(MONTO CANCELADO — excluir)* | |

> **No hay traspaso INTL→NAC en diciembre 2022.** La tx internacional de Rafael (USD 370 Serv. Profesionales) se traspasará en enero 2023.

**Asiento Diciembre (excluyendo MONTO CANCELADO):**

| Código | Cuenta | Debe |
|---|---|---:|
| 4.01.03.22 | Gastos Generales | 78.980 |
| 4.01.03.28 | Gastos de Movilización | 70.000 |
| 4.01.03.29 | Gastos Alimentación | 68.650 |
| 4.02.07.01 | Gastos Bancarios | 3.501 |
| **TOTAL DEBE** | | **221.131** |

---

## Resumen Anual 2022

| Mes | Total Debe CLP |
|---|---:|
| Enero | 706.550 |
| Febrero | 443.509 |
| Marzo | 994.930 |
| Abril | 612.275 |
| Mayo | 371.289 |
| Junio | 547.332 |
| Julio | 870.911 |
| Agosto | 1.606.225 |
| Septiembre | 1.234.886 |
| Octubre | 509.412 |
| Noviembre | 755.210 |
| Diciembre | 221.131 |
| **TOTAL 2022** | **8.873.660** |

## Notas para el agente ejecutor

1. **MONTO CANCELADO**: Las líneas con tipo_gasto='Otros' y monto negativo son pagos parciales a la TC hechos en comercio. Deben excluirse del asiento de gastos — son rebaja de pasivo, igual que "BCI Paga TC".
2. **Traspaso INTL→NAC**: El monto CLP del traspaso se suma al asiento del mes en la cuenta que corresponda según el detalle internacional. Cuando el mes solo tiene Software/Google/Marketing, va a 4.01.03.36. Cuando es mezcla, va a 4.01.03.22 (Gastos Generales).
3. **Contrapartida (Haber)**: Todos los asientos cargan al Debe. La contrapartida en el Haber es la cuenta de pasivo de la tarjeta de crédito — confirmar código con el contador.
4. **Después de ingresar cada mes**, marcar `fact_kame = 1` en la base de datos usando la consulta UPDATE indicada arriba.
5. **No hay transacciones personales en 2022** — las 3 personales de Rafael son de otros años.
