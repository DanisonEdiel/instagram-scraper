# Examen

## 3) ¿Qué categorías predominan? ¿Qué consume? ¿Hay patrones?

- Predominan cuentas creativas: diseñadores, fotografía y proyectos visuales.
- También hay crítica/curaduría de publicidad y algo de ciencia/tecnología.
- Prefiere contenido breve y visual (reels/carruseles) para inspiración.
- Patrón claro: busca referentes estéticos y análisis de campañas; menos marcas “corporativas” y más creadores independientes.

## 4) ¿Cómo uso esto en el UX/UI de mi app?

- Onboarding: chips de categorías detectadas para que el usuario confirme.
- Feed: mezcla de “lo que sigues” con descubrimiento de creadores similares.
- Insights: gráfico simple por categorías y top cuentas por actividad.
- Guardar en boards tipo “Tipografía” y “Publicidad crítica”.
- Red lenta (3–10 Mbps): miniaturas progresivas y sin autoplay por defecto.
- Control: transparencia de inferencias y opción de excluir categorías/cuentas.

## Para obtener los datos

- Autenticación: `python main.py auth --headless false`
- Following: `python main.py following --url "https://www.instagram.com/<usuario>/" --limit 30 --output "storage/<usuario>_following_details.xlsx" --force-ui --chunk 1 --delay-ms 12000 --retry-tries 4 --retry-base-ms 2500`

Si el Excel está abierto, se guardará como `..._v2.xlsx`. En conexión lenta sube `--delay-ms` a `15000`.
